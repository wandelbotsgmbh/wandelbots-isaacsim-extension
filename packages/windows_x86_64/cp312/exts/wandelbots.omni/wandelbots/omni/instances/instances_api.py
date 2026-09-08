import asyncio
import carb
from typing import Optional
import requests

from wandelbots.omni.instances.models import (
    NOVACellData,
    NOVAControllerData,
    NOVAMotionGroupData,
    NOVACloudInstance,
    NOVACustomInstance,
    NOVAInstance,
)
from wandelbots.omni.utils.auth import (
    get_auth_token,
    get_auth_config,
    get_auth_configs,
    get_portal_api_url,
    invalidate_auth_token,
    EntraIDModel,
)
from wandelbots.omni.utils.api import ApiConfiguration
from wandelbots_api_client.v2.api.cell_api import CellApi
from wandelbots_api_client.v2.api.controller_api import ControllerApi
from wandelbots_api_client.v2.api.motion_group_api import MotionGroupApi
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models
from wandelbots.omni.environment import instance_store
from packaging.version import Version, InvalidVersion
from wandelbots.omni.utils.hosts import normalize_host

# Reachability is decided by a single version request. Without an app-level cap it
# waits on the OS TCP-connect timeout (~5-10s on Windows for an unresponsive host),
# which keeps the instance dot yellow far too long before it turns red. Bound the
# probe so an unreachable host is reported quickly.
_VERSION_PROBE_TIMEOUT_S = 3.0

# Cell-data fetches fan out per controller and per motion group. Each SDK call is
# bounded so one slow/half-dead controller cannot stall an instance's whole cell
# load (the client otherwise defaults to a 300 s per-request timeout).
_CELL_CALL_TIMEOUT_S = 10.0


def _concise_api_error(e: Exception) -> str:
    """Short message from an SDK error, avoiding a full HTTP-header dump."""
    msg = str(e)
    body = getattr(e, "body", None)
    if body:
        try:
            import json

            parsed = json.loads(body)
            msg = parsed.get("message", msg)
        except (json.JSONDecodeError, TypeError):
            pass
    return msg


class NOVAInstancesAPI:
    def __init__(self):
        self._instance_host_auth_mapping: dict[str, str] = {}
        # True once a refresh published a complete mapping; a missing host is
        # then final (see get_auth_config_id_from_host).
        self._auth_mapping_loaded = False
        # Partial mapping of the in-flight refresh, None otherwise.
        self._auth_mapping_pending: dict[str, str] | None = None

    def get_auth_token_from_host(self, host: str) -> str | None:
        auth_config_id = self.get_auth_config_id_from_host(host)
        if auth_config_id is None:
            carb.log_verbose(f"No auth config found for host: {host}")
            return None
        return get_auth_token(auth_config_id)

    def create_api_client_for_instance(
        self, instance: NOVAInstance
    ) -> Optional[wb_v2.ApiClient]:
        if isinstance(instance, NOVACloudInstance):
            token = self.get_auth_token_from_host(instance.host)
            return instance.create_api_client(token=token)
        return instance.create_api_client()

    def get_api_configuration_for_instance(
        self, instance: NOVAInstance
    ) -> ApiConfiguration:
        return ApiConfiguration(
            host=instance.host,
            secure_connection=instance.is_secure_connection,
            access_token=self.get_auth_token_from_host(instance.host),
        )

    async def fetch_reachable_running_instances(self) -> list[NOVAInstance]:
        """Cloud and custom instances that are reachable and running.

        The cloud lookup is a blocking portal request, so it runs in a worker
        thread and callers can await this from the UI thread.
        """
        cloud, custom = await asyncio.to_thread(
            lambda: (self.get_cloud_instances(), self.get_custom_instances())
        )
        reachable = [
            instance
            for instances in cloud.values()
            for instance in instances
            if instance.is_reachable and instance.is_running
        ]
        reachable.extend(
            instance
            for instance in custom
            if instance.is_reachable and instance.is_running
        )
        return reachable

    def get_auth_config_id_from_host(self, host: str) -> str | None:
        # Keyed on the normalized host: callers pass either NOVAInstance.host or
        # a MotionStreamConfiguration host, and only the latter is guaranteed
        # scheme-less (_sanitize_host strips it). Without this a stage-sourced
        # cloud config would miss its auth mapping and stream without a token.
        key = normalize_host(host)
        mapping = self._instance_host_auth_mapping
        if key in mapping:
            return mapping[key]

        # A load in flight publishes only when every provider is in, so also read
        # what it has collected so far - otherwise a lookup racing the first load
        # would report "no auth" for a host that is already known.
        pending = self._auth_mapping_pending
        if pending is not None and key in pending:
            return pending[key]

        # Refresh only while the mapping has never been populated. The refresh
        # issues a *blocking* portal request per configured auth provider, and
        # callers reach this from inside coroutines (see
        # virtual_controller_service), so it stalls the event loop for as long as
        # every provider takes to answer. Once the mapping is loaded, a host that
        # is still missing is simply not an instance - e.g. one that only exists
        # in the stage - and refreshing again cannot resolve it. Without this the
        # refresh re-ran on every lookup, once per motion-group row, and froze the
        # panel on open.
        if self._auth_mapping_loaded or pending is not None:
            return None
        self._reload_instance_auth_mappings()
        return self._instance_host_auth_mapping.get(key)

    def _reload_instance_auth_mappings(self):
        self.get_cloud_instances()
        self.get_custom_instances()

    def get_cloud_instances_by_auth(
        self, auth_config_id: str, auth_mapping: dict[str, str] | None = None
    ) -> list[NOVACloudInstance]:
        """Instances of one provider.

        *auth_mapping* lets ``get_cloud_instances`` collect the host -> auth
        entries of every provider into one dict and publish them in a single
        assignment; standalone callers update the live mapping directly.
        """
        token = get_auth_token(auth_config_id)
        if not token:
            carb.log_verbose("No authentication token available for cloud instances")
            return []

        try:
            instances_data = self._fetch_instances(auth_config_id, token)
            carb.log_verbose(
                f"Retrieved {len(instances_data)} cloud instances from API"
            )

            instances = [
                instance
                for instance in map(self._create_cloud_instance, instances_data)
                if instance is not None
            ]

            target = (
                self._instance_host_auth_mapping
                if auth_mapping is None
                else auth_mapping
            )
            for instance in instances:
                target[normalize_host(instance.host)] = auth_config_id

            carb.log_verbose(
                f"Successfully parsed {len(instances)} valid cloud instances"
            )
            return instances

        except requests.HTTPError as e:
            if e.response.status_code == 401:
                carb.log_warn(
                    "Not able to fetch instances - authentication token is invalid. Please try a re-authentication."
                )
                return []
            else:
                carb.log_error(f"HTTP error retrieving cloud instances: {e}")
                return []
        except requests.RequestException as e:
            carb.log_error(f"Failed to retrieve cloud instances: {e}")
            return []
        except Exception as e:
            carb.log_error(f"Unexpected error while processing cloud instances: {e}")
            return []

    def get_cloud_instances(self) -> dict[str, list[NOVACloudInstance]]:
        # The panel runs this off the main thread, so the mapping must never be
        # observable half-written: collect every provider into a fresh dict and
        # publish it in one assignment. A concurrent lookup then sees either the
        # previous complete mapping or this one, and keeps resolving tokens while
        # a refresh is in flight. The in-flight flag only keeps such a lookup
        # from kicking off a second, blocking refresh of its own.
        cloud_mapping: dict[str, str] = {}
        self._auth_mapping_pending = cloud_mapping
        try:
            instances = {
                auth_config_id: self.get_cloud_instances_by_auth(
                    auth_config_id, auth_mapping=cloud_mapping
                )
                for auth_config_id in get_auth_configs().keys()
            }
            published = dict(self._instance_host_auth_mapping)
            published.update(cloud_mapping)
            self._instance_host_auth_mapping = published
            self._auth_mapping_loaded = True
        finally:
            self._auth_mapping_pending = None
        return instances

    def get_custom_instances(self) -> list[NOVACustomInstance]:
        custom_instances = instance_store.get_instances()
        for instance in custom_instances:
            # setdefault, not assignment: a custom entry carries no auth, so it
            # must never clear a mapping a cloud instance established for the
            # same host. _reload_instance_auth_mappings runs the cloud pass
            # first, and a manually added duplicate of a cloud host would
            # otherwise overwrite its auth_config_id with None.
            self._instance_host_auth_mapping.setdefault(
                normalize_host(instance.host), None
            )
        return custom_instances

    async def fetch_primary_cell_id(self, instance: NOVAInstance) -> Optional[str]:
        """Name of the cell an instance's setups are stored in, or None when it
        has none. Instances serve a single cell in practice; a second one is
        logged so a wrong pick is traceable."""
        cells = await self.fetch_cells_for_instance(instance)
        if not cells:
            return None
        if len(cells) > 1:
            carb.log_info(
                f"Instance {instance.display_name} has {len(cells)} cells; "
                f"using '{cells[0].name}'."
            )
        return cells[0].name

    async def fetch_cells_for_instance(
        self, instance: NOVAInstance
    ) -> Optional[list[NOVACellData]]:
        try:
            carb.log_info(f"Fetching cells for instance {instance.host}...")

            # Check if cloud instance is running before fetching cells
            if isinstance(instance, NOVACloudInstance):
                if instance.status and instance.status.lower() != "running":
                    carb.log_warn(
                        f"Instance {instance.display_name} is not running (status: {instance.status}). Skipping cell fetching."
                    )
                    return []

            # Get API client based on instance type
            if isinstance(instance, NOVACloudInstance):
                api_client = instance.create_api_client(
                    token=self.get_auth_token_from_host(instance.host)
                )
                if api_client.configuration.access_token is None:
                    carb.log_warn(
                        f"No valid token available for cloud instance {instance.display_name}"
                    )
            elif isinstance(instance, NOVACustomInstance):
                api_client = instance.create_api_client()
            else:
                carb.log_warn(
                    f"Unknown instance type: {type(instance)}. Cannot create API client."
                )
                return []

            if not api_client:
                carb.log_warn(
                    f"Failed to create API client for {instance.display_name}. Instance marked as unreachable."
                )
                instance.is_reachable = False
                return []  # Return instance without cells

            instance.version = await self._fetch_instance_version(api_client)
            if instance.version is None:
                instance.is_reachable = False
                return []

            instance.is_reachable = True

            # Fetch cells for the instance
            cell_names = await self._fetch_cells(api_client)
            cells = await self._fetch_cells_data(api_client, cell_names)
            return cells

        except Exception as ex:
            carb.log_warn(
                f"No cell available for instance {instance.display_name} {ex}"
            )
            return []
        finally:
            if "api_client" in locals() and api_client:
                try:
                    await api_client.close()
                except Exception as e:
                    carb.log_warn(f"Error closing API client: {e}")

    def toggle_instance_status(self, auth_config_id: str, instance: NOVACloudInstance):
        try:
            new_status = "running" if instance.status == "stopped" else "stopped"
            token = get_auth_token(auth_config_id)
            headers = {"Authorization": f"Bearer {token}"}
            instances_path = self._get_instances_path(auth_config_id)
            response = requests.put(
                f"{get_portal_api_url(auth_config_id)}/{instances_path}/{instance.instance_id}/state",
                headers=headers,
                timeout=10,
                json={"state": new_status},
            )
            response.raise_for_status()
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                carb.log_warn(
                    f"Authentication token for host '{instance.host}' is invalid (401). Invalidating token."
                )
                invalidate_auth_token(auth_config_id)
            else:
                carb.log_error(f"HTTP error updating instance status: {e}")

    async def _fetch_instance_version(
        self, api_client: wb_v2.ApiClient
    ) -> Optional[str]:
        try:
            carb.log_verbose("Fetching instance version...")
            system_api = wb_v2.SystemApi(api_client=api_client)
            version = await asyncio.wait_for(
                system_api.get_system_version(), timeout=_VERSION_PROBE_TIMEOUT_S
            )
            return Version(version)
        except asyncio.TimeoutError:
            carb.log_warn(
                f"Instance version probe timed out after {_VERSION_PROBE_TIMEOUT_S:.0f}s; "
                "treating instance as unreachable."
            )
            return None
        except InvalidVersion:
            # The api-gateway returns a JSON error body (not a version string) when
            # the request is unauthorized. Don't dump the whole blob; just note the
            # auth failure and treat the instance as unreachable.
            carb.log_warn(
                "Instance version probe was rejected (likely unauthorized); "
                "treating instance as unreachable."
            )
            return None
        except Exception as e:
            carb.log_warn(f"Error fetching instance version: {e}")
            return None

    async def _fetch_cells(self, api_client: wb_v2.ApiClient) -> list[str]:
        try:
            carb.log_verbose("Fetching cells from instance...")
            cell_api = CellApi(api_client=api_client)
            cells = await asyncio.wait_for(
                cell_api.list_cells(), timeout=_CELL_CALL_TIMEOUT_S
            )
            return cells
        except Exception as e:
            # Check if it's a network connectivity issue
            error_msg = str(e).lower()
            if any(
                keyword in error_msg
                for keyword in [
                    "connection",
                    "network",
                    "refused",
                    "timeout",
                    "unreachable",
                ]
            ):
                carb.log_warn(f"Network connection error fetching cells: {e}")
            else:
                carb.log_warn(f"Error fetching cells: {e}")
            return []

    async def _fetch_cells_data(
        self, api_client: wb_v2.ApiClient, cell_names: list[str]
    ) -> list[NOVACellData]:
        tasks = [
            self._fetch_single_cell_data(api_client, cell_name)
            for cell_name in cell_names
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out failed results
        valid_results = []
        for result in results:
            if isinstance(result, Exception):
                carb.log_warn(f"Failed to fetch cell data: {result}")
            elif result is not None:
                valid_results.append(result)

        return valid_results

    async def _fetch_single_cell_data(
        self, api_client: wb_v2.ApiClient, cell_name: str
    ) -> Optional[NOVACellData]:
        try:
            controller_api = ControllerApi(api_client=api_client)
            motion_group_api = MotionGroupApi(api_client=api_client)
            controller_names = await asyncio.wait_for(
                controller_api.list_robot_controllers(cell=cell_name),
                timeout=_CELL_CALL_TIMEOUT_S,
            )

            # Fan the per-controller descriptions out concurrently. This was a
            # serial loop of 1 + C + C*M round-trips per cell, which dominated the
            # time to populate the panel on a reachable instance.
            results = await asyncio.gather(
                *(
                    self._fetch_controller_data(
                        controller_api, motion_group_api, cell_name, controller_name
                    )
                    for controller_name in controller_names
                ),
                return_exceptions=True,
            )

            controllers_data = []
            for controller_name, result in zip(controller_names, results):
                if isinstance(result, Exception):
                    carb.log_warn(
                        f"Error fetching data for controller {controller_name} "
                        f"in cell {cell_name}: {_concise_api_error(result)}"
                    )
                elif result is not None:
                    controllers_data.append(result)

            return NOVACellData(name=cell_name, controllers=controllers_data)

        except Exception as e:
            carb.log_warn(f"Error fetching data for cell {cell_name}: {e}")
            return None

    async def _fetch_controller_data(
        self,
        controller_api: ControllerApi,
        motion_group_api: MotionGroupApi,
        cell_name: str,
        controller_name: str,
    ) -> Optional[NOVAControllerData]:
        controller_desc: wb_v2_models.ControllerDescription = await asyncio.wait_for(
            controller_api.get_controller_description(
                cell=cell_name, controller=controller_name
            ),
            timeout=_CELL_CALL_TIMEOUT_S,
        )

        # Fan the motion-group descriptions out concurrently too. No
        # return_exceptions here: a failure propagates and drops this whole
        # controller (matching the original serial behaviour), where the caller's
        # gather logs and skips it.
        mg_names = controller_desc.connected_motion_groups
        mg_descs: list[wb_v2_models.MotionGroupDescription] = await asyncio.gather(
            *(
                asyncio.wait_for(
                    motion_group_api.get_motion_group_description(
                        cell=cell_name,
                        controller=controller_name,
                        motion_group=mg_name,
                    ),
                    timeout=_CELL_CALL_TIMEOUT_S,
                )
                for mg_name in mg_names
            )
        )

        motion_groups = [
            NOVAMotionGroupData(
                name=mg_name,
                motion_group_model_name=mg_desc.motion_group_model,
            )
            for mg_name, mg_desc in zip(mg_names, mg_descs)
        ]

        return NOVAControllerData(
            name=controller_name,
            cell_name=cell_name,
            description=controller_desc,
            motion_groups=motion_groups,
        )

    # Helper methods for cloud instance management
    def _get_instances_path(self, auth_config_id: str) -> str:
        """Return the instances API path based on auth provider type."""
        config = get_auth_config(auth_config_id)
        if isinstance(config, EntraIDModel):
            return "virtual/instances"
        return "instances"

    def _fetch_instances(self, auth_config_id: str, token: str) -> list[NOVAInstance]:
        token = get_auth_token(auth_config_id)
        if not token:
            carb.log_verbose(
                f"No authentication token available for {auth_config_id} cloud instances"
            )
            return []
        headers = {"Authorization": f"Bearer {token}"}
        instances_path = self._get_instances_path(auth_config_id)
        response = requests.get(
            f"{get_portal_api_url(auth_config_id)}/{instances_path}",
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        instances = response.json().get("instances", [])
        for instance in instances:
            instance["auth_config_id"] = auth_config_id
        return instances

    def _create_cloud_instance(self, data: dict) -> NOVACloudInstance:
        if not self._is_valid_instance_data(data):
            return []

        try:
            return NOVACloudInstance(**data)
        except Exception as e:
            carb.log_warn(f"Skipping invalid instance data: {e}")
            return []

    def _is_valid_instance_data(self, data: dict) -> bool:
        required_fields = [
            "sandbox_name",
            "host",
            "expires_at",
            "instance_id",
            "obsolete_at",
            "status",
        ]
        return all(data.get(field) for field in required_fields)


_instances_api = NOVAInstancesAPI()


def get_instances_api() -> NOVAInstancesAPI:
    return _instances_api
