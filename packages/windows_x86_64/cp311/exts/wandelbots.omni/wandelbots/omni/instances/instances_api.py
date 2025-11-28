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
    get_portal_api_url,
    invalidate_auth_token,
)
from wandelbots_api_client.v2.api.cell_api import CellApi
from wandelbots_api_client.v2.api.controller_api import ControllerApi
from wandelbots_api_client.v2.api.motion_group_api import MotionGroupApi
import wandelbots_api_client.v2 as wb_v2
from wandelbots.omni.environment import instance_store


class NOVAInstancesAPI:
    def __init__(self):
        self._base_url = get_portal_api_url()

    def get_cloud_instances(self) -> list[NOVACloudInstance]:
        token = get_auth_token()
        if not token:
            carb.log_verbose("No authentication token available for cloud instances")
            return []

        try:
            instances_data = self._fetch_instances(token)
            carb.log_verbose(
                f"Retrieved {len(instances_data)} cloud instances from API"
            )

            instances = [
                instance
                for instance in map(self._create_cloud_instance, instances_data)
                if instance is not None
            ]

            carb.log_verbose(
                f"Successfully parsed {len(instances)} valid cloud instances"
            )
            return instances

        except requests.HTTPError as e:
            if e.response.status_code == 401:
                carb.log_warn(
                    "Authentication token for cloud instances is invalid (401). Invalidating token."
                )
                invalidate_auth_token()
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

    def get_custom_instances(self) -> list[NOVACustomInstance]:
        custom_instances = instance_store.get_instances()
        return custom_instances

    async def fetch_cells_for_instance(
        self, instance: NOVAInstance
    ) -> Optional[NOVAInstance]:
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
                api_client = instance.create_api_client(token=get_auth_token())
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

            # Fetch cells for the instance
            cell_names = await self._fetch_cells(api_client)
            cells = await self._fetch_cells_data(api_client, cell_names)
            return cells

        except Exception:
            carb.log_verbose(f"No cell available for instance {instance.display_name}")
            return []
        finally:
            if "api_client" in locals() and api_client:
                try:
                    await api_client.close()
                except Exception as e:
                    carb.log_warn(f"Error closing API client: {e}")

    def toggle_instance_status(self, instance: NOVACloudInstance):
        try:
            new_status = "running" if instance.status == "stopped" else "stopped"

            headers = {"Authorization": f"Bearer {get_auth_token()}"}
            response = requests.put(
                f"{self._base_url}/instances/{instance.instance_id}/state",
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
                invalidate_auth_token()
            else:
                carb.log_error(f"HTTP error updating instance status: {e}")

    async def _fetch_instance_version(
        self, api_client: wb_v2.ApiClient
    ) -> Optional[str]:
        try:
            carb.log_verbose("Fetching instance version...")
            system_api = wb_v2.SystemApi(api_client=api_client)
            version = await system_api.get_system_version()
            return version
        except Exception as e:
            carb.log_error(f"Error fetching instance version: {e}")
            return None

    async def _fetch_cells(self, api_client: wb_v2.ApiClient) -> list[str]:
        try:
            carb.log_verbose("Fetching cells from instance...")
            cell_api = CellApi(api_client=api_client)
            cells = await cell_api.list_cells()
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
                carb.log_error(f"Error fetching cells: {e}")
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
                carb.log_error(f"Failed to fetch cell data: {result}")
            elif result is not None:
                valid_results.append(result)

        return valid_results

    async def _fetch_single_cell_data(
        self, api_client: wb_v2.ApiClient, cell_name: str
    ) -> Optional[NOVACellData]:
        try:
            controller_api = ControllerApi(api_client=api_client)
            motion_group_api = MotionGroupApi(api_client=api_client)
            controller_names = await controller_api.list_robot_controllers(
                cell=cell_name
            )

            controllers_data = []
            for controller_name in controller_names:
                controller_desc: wb_v2.ControllerDescription = (
                    await controller_api.get_controller_description(
                        cell=cell_name, controller=controller_name
                    )
                )

                motion_groups = []

                for motion_group_name in controller_desc.connected_motion_groups:
                    motion_group_desc: wb_v2.MotionGroupDescription = (
                        await motion_group_api.get_motion_group_description(
                            cell=cell_name,
                            controller=controller_name,
                            motion_group=motion_group_name,
                        )
                    )
                    motion_groups.append(
                        NOVAMotionGroupData(
                            name=motion_group_name,
                            motion_group_model_name=motion_group_desc.motion_group_model.replace(
                                "_", " "
                            ),
                        )
                    )

                controllers_data.append(
                    NOVAControllerData(
                        name=controller_name,
                        cell_name=cell_name,
                        description=controller_desc,
                        motion_groups=motion_groups,
                    )
                )

            return NOVACellData(name=cell_name, controllers=controllers_data)

        except Exception as e:
            carb.log_error(f"Error fetching data for cell {cell_name}: {e}")
            return None

    # Helper methods for cloud instance management
    def _fetch_instances(self, token: str) -> list[NOVAInstance]:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            f"{self._base_url}/instances", headers=headers, timeout=10
        )
        response.raise_for_status()
        instances = response.json().get("instances", [])
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
