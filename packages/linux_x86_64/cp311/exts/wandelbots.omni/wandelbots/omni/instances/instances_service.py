from typing import Callable, Optional
import carb
import asyncio
import re
import ipaddress
from urllib.parse import urlparse
from wandelbots.omni.instances.instances_api import NOVAInstancesAPI
from wandelbots.omni.utils.auth import (
    get_auth_token,
    invalidate_auth_token,
)
from wandelbots.omni.instances.models import (
    NOVAInstance,
    NOVACustomInstance,
    NOVACloudInstance,
    NOVAControllerData,
    NOVACellData,
    is_cloud_host,
)
from wandelbots.omni.instances.stage_discovery import (
    filter_unknown_host_instances,
    list_cells_for_host,
)
from wandelbots.omni.instances.events import push_motion_group_connection_changed
from wandelbots.omni.environment import instance_store
from wandelbots.omni.manipulators import (
    get_motion_group_service,
    get_motion_group_configuration_from_prim,
    get_scene_motion_group_prim_paths,
    MotionGroupConfiguration,
    MotionStreamConfiguration,
)
from wandelbots.omni.utils.hosts import normalize_host
from pxr import Usd
from omni.kit.async_engine import run_coroutine

import isaacsim.core.utils.stage as stage_utils
from .instances_api import get_instances_api


def retarget_motion_group_configuration(
    prim: Usd.Prim,
    prim_path: str,
    instance: NOVAInstance,
    controller: NOVAControllerData,
    motion_group_name: str,
    use_external_joint_stream: Optional[bool] = None,
) -> MotionGroupConfiguration:
    """Configuration that points *prim* at the given instance motion group.

    An already connected prim keeps the settings that are the user's to make -
    enabled, response rate, and the joint stream source unless the caller picked
    one - because reconnecting it, for instance to recreate a deleted virtual
    controller, must not silently change how the robot runs. An unconfigured
    prim starts from the defaults.
    """
    existing = get_motion_group_configuration_from_prim(prim)
    # Robots ship with MotionGroupAPI applied but empty attributes, so the host
    # is what tells a previous connection from an unconfigured prim.
    if existing is None or not existing.motion_stream_configuration.host:
        return MotionGroupConfiguration(
            name=motion_group_name,
            prim_path=prim_path,
            motion_stream_configuration=MotionStreamConfiguration(
                host=instance.host,
                secure_connection=instance.is_secure_connection,
                cell=controller.cell_name,
                controller=controller.name,
                motion_group=motion_group_name,
                use_external_joint_stream=bool(use_external_joint_stream),
            ),
        )

    carb.log_info(
        f"Articulation at {prim_path} is already connected. "
        "Updating configuration to selected motion group"
    )
    existing.name = motion_group_name
    stream_config = existing.motion_stream_configuration
    stream_config.host = instance.host
    stream_config.secure_connection = instance.is_secure_connection
    stream_config.cell = controller.cell_name
    stream_config.controller = controller.name
    stream_config.motion_group = motion_group_name
    if use_external_joint_stream is not None:
        stream_config.use_external_joint_stream = use_external_joint_stream
    return existing


class NOVAInstancesService:
    def __init__(self):
        self._cloud_instances: list[NOVACloudInstance] = []
        self._custom_instances: list[NOVACustomInstance] = []
        self._instances_api = get_instances_api()
        self._connected_motion_groups: dict[str, MotionGroupConfiguration] = {}
        self._selected_articulations: dict[str, str] = {}

    def find_instance_by_host(self, host: str) -> Optional[NOVAInstance]:
        # Normalized: callers resolve an instance from a stored motion-group
        # config, whose host was stripped of its scheme.
        wanted = normalize_host(host)

        def _custom():
            return next(
                (
                    i
                    for i in self._instances_api.get_custom_instances()
                    if normalize_host(i.host) == wanted
                ),
                None,
            )

        def _cloud():
            return next(
                (
                    i
                    for instances in self._instances_api.get_cloud_instances().values()
                    for i in instances
                    if normalize_host(i.host) == wanted
                ),
                None,
            )

        # A cloud host resolves to the cloud instance first: only that one can
        # authenticate, and a manually added custom duplicate would otherwise win
        # and leave callers without a token. Everything else keeps looking in the
        # local store first, so an on-prem lookup does not trigger a portal
        # request.
        order = (_cloud, _custom) if is_cloud_host(host) else (_custom, _cloud)
        for lookup in order:
            instance = lookup()
            if instance is not None:
                return instance
        return None

    def get_selected_articulation(self, identifier: str) -> Optional[str]:
        return self._selected_articulations.get(identifier, None)

    def set_selected_articulation(self, identifier: str, prim_path: str):
        self._selected_articulations[identifier] = prim_path
        carb.log_verbose(f"Selected articulation for {identifier} set to {prim_path}")

    def remove_selected_articulation(self, identifier: str):
        if identifier in self._selected_articulations:
            del self._selected_articulations[identifier]
            carb.log_verbose(f"Removed selected articulation for {identifier}")
        else:
            carb.log_verbose(
                f"No selected articulation found for {identifier} to remove."
            )

    def get_connected_motion_group(
        self, identifier: str
    ) -> Optional[MotionStreamConfiguration]:
        return self._connected_motion_groups.get(identifier, None)

    def clear_connected_motion_groups(self):
        self._connected_motion_groups.clear()
        carb.log_verbose("All connected motion groups cleared.")

    def add_to_connected_motion_groups(
        self, identifier: str, motion_group_config: MotionGroupConfiguration
    ):
        if identifier in self._connected_motion_groups:
            carb.log_verbose(
                f"Motion group with identifier {identifier} already exists, updating configuration."
            )
        self._connected_motion_groups[identifier] = motion_group_config
        carb.log_verbose(
            f"Connected motion group added: {identifier} at {motion_group_config.prim_path}"
        )

    def remove_from_connected_motion_group(self, identifier: str):
        if identifier in self._connected_motion_groups:
            del self._connected_motion_groups[identifier]
            carb.log_info(f"Disconnected motion group: {identifier}")
        else:
            carb.log_warn(
                f"Motion group with identifier {identifier} not found for disconnection."
            )

    def add_custom_instance(self, instance: NOVACustomInstance):
        instance_store.store_instance(instance)
        carb.log_verbose(f"Custom instance added: {instance.host}")

    def sign_out(self, auth_config_id: str, callback: Optional[callable] = None):
        carb.log_verbose("Signing out user - invalidating auth token")
        invalidate_auth_token(auth_config_id)
        self._cloud_instances.clear()
        callback()

    def is_signed_in(self, auth_config_id: str) -> bool:
        auth_token = get_auth_token(auth_config_id)
        if auth_token == "":
            carb.log_verbose(f"Auth token is empty for config: {auth_config_id}")
        return auth_token != "" and auth_token is not None

    def remove_instance(
        self, instance: NOVACustomInstance, on_complete_fn: Optional[callable] = None
    ):
        carb.log_verbose(f"Removing instance: {instance.name}")

        if instance.status == "running":

            def _done(_: asyncio.Future):
                carb.log_verbose(
                    f"Instance {instance.name} stopped. Proceeding to disconnect motion groups."
                )
                instance_store.remove_instance(instance.instance_id)
                on_complete_fn()

            run_coroutine(self._disconnect_motion_groups(instance)).add_done_callback(
                _done
            )
        else:
            instance_store.remove_instance(instance.instance_id)
            on_complete_fn()

    async def _disconnect_motion_groups(self, instance: NOVAInstance):
        motion_group_service = get_motion_group_service()
        prim_paths = motion_group_service.get_all_motion_group_prim_paths()
        carb.log_verbose(f"Prim paths for motion groups: {prim_paths}")

        for prim_path in prim_paths:
            carb.log_verbose(
                f"Checking motion group at {prim_path} for instance {instance.host}"
            )
            motion_group = motion_group_service.get_motion_group_configuration(
                prim_path
            )
            if not motion_group:
                continue

            if normalize_host(
                motion_group.motion_stream_configuration.host
            ) == normalize_host(instance.host):
                carb.log_verbose(
                    f"Removing motion group {prim_path} for instance {instance.host}"
                )
                try:
                    await motion_group_service.remove_motion_group(prim_path)
                    identifier = motion_group.identifier
                    self.remove_from_connected_motion_group(identifier)
                except Exception as e:
                    carb.log_error(f"Failed to remove motion group {prim_path}: {e}")

    def delete_motion_group(
        self,
        motion_group_config: MotionGroupConfiguration,
        callback: Optional[Callable[[bool], None]] = None,
    ):
        try:

            async def remove_motion_group_async():
                success = False
                try:
                    motion_group_service = get_motion_group_service()
                    await motion_group_service.remove_motion_group(
                        motion_group_config.prim_path
                    )
                    identifier = motion_group_config.identifier
                    self.remove_from_connected_motion_group(identifier)
                    push_motion_group_connection_changed(
                        host=motion_group_config.motion_stream_configuration.host,
                        cell=motion_group_config.motion_stream_configuration.cell,
                        controller=motion_group_config.motion_stream_configuration.controller,
                        motion_group=motion_group_config.motion_stream_configuration.motion_group,
                        prim_path=motion_group_config.prim_path,
                        action="disconnected",
                    )
                    success = True
                except Exception as e:
                    carb.log_error(
                        f"Failed to delete motion group at {motion_group_config.prim_path}: {e}"
                    )
                finally:
                    if callback:
                        callback(success)

            loop = asyncio.get_event_loop()
            asyncio.ensure_future(remove_motion_group_async(), loop=loop)
        except Exception as e:
            carb.log_error(
                f"Failed to delete motion group at {motion_group_config.prim_path}: {e}"
            )
            if callback:
                callback(False)

    def create_motion_group_from_nova(
        self,
        instance: NOVAInstance,
        controller: NOVAControllerData,
        motion_group_name: str,
        prim_path: str,
        use_external_joint_stream: Optional[bool] = None,
        callback: Optional[callable] = None,
    ):
        """Point the prim at the given motion group and open its stream.

        Pass ``use_external_joint_stream`` only when the user picked a joint
        stream source; leaving it out keeps the one the prim already carries.
        """
        prim: Usd.Prim = stage_utils.get_current_stage().GetPrimAtPath(prim_path)
        try:
            if not prim_path:
                carb.log_warn("No articulation path provided for motion group creation")
                callback(False)
                return

            motion_group_service = get_motion_group_service()
            if not motion_group_service:
                carb.log_error("Motion group service not available")
                callback(False)
                return

            carb.log_verbose(
                f"Creating motion group '{motion_group_name}' assigned to '{prim_path}'"
            )

            motion_group_config = retarget_motion_group_configuration(
                prim=prim,
                prim_path=prim_path,
                instance=instance,
                controller=controller,
                motion_group_name=motion_group_name,
                use_external_joint_stream=use_external_joint_stream,
            )

            async def create_motion_group_async():
                identifier = motion_group_config.identifier
                try:
                    await motion_group_service.create_motion_group(
                        configuration=motion_group_config
                    )
                    self.add_to_connected_motion_groups(identifier, motion_group_config)
                    instance.is_reachable = True
                    push_motion_group_connection_changed(
                        host=instance.host,
                        cell=controller.cell_name,
                        controller=controller.name,
                        motion_group=motion_group_name,
                        prim_path=prim_path,
                        action="connected",
                    )
                    carb.log_verbose(
                        f"Successfully connected {identifier} to {prim_path}"
                    )
                    callback(True)
                except Exception as e:
                    # Non-fatal: callers (e.g. the create-and-connect flow) retry
                    # this because a freshly created controller is briefly not
                    # pingable. Log as a warning and surface the real reason so an
                    # exhausted retry can show a meaningful message.
                    carb.log_warn(f"Failed to connect {identifier} to {prim_path}: {e}")
                    callback(False, f"Failed to connect: {e}")

            loop = asyncio.get_event_loop()
            asyncio.ensure_future(create_motion_group_async(), loop=loop)

        except Exception as e:
            carb.log_error(f"Error creating motion group: {e}")
            if callback:
                callback(False)

    def validate_host(self, host: str) -> str | None:
        if not host:
            raise ValueError("Host address cannot be empty")

        # Parse the host to extract hostname
        parsed_host = urlparse(host if "://" in host else f"http://{host}")
        hostname = parsed_host.hostname or parsed_host.netloc or parsed_host.path

        if not hostname:
            raise ValueError("You must provide a valid host address")

        # Check if it's a valid IP address
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            # If not IP, check if it's a valid domain
            # Accept localhost or domains with at least one dot
            if hostname.lower() == "localhost":
                pass  # localhost is valid
            elif "." not in hostname:
                raise ValueError(f"Invalid domain or IP address: {hostname}")
            else:
                # Check domain format with regex
                domain_pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+$"
                if not re.match(domain_pattern, hostname):
                    raise ValueError(f"Invalid domain or IP address: {hostname}")

        # Return normalized URL with scheme
        scheme = parsed_host.scheme or "https"
        port_part = f":{parsed_host.port}" if parsed_host.port else ""
        host = f"{scheme}://{hostname}{port_part}"
        return host

    def toggle_instance_status(
        self,
        auth_config_id: str,
        instance: NOVACloudInstance,
        callback: Callable[[], None] | None = None,
    ):
        # The status change is a blocking portal PUT (up to 10s); run it off the
        # Kit main thread so clicking start/stop doesn't freeze the UI, then invoke
        # the callback on completion.
        async def _run():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._instances_api.toggle_instance_status(
                    auth_config_id=auth_config_id, instance=instance
                ),
            )
            if callback:
                callback()

        run_coroutine(_run())

    @property
    def connected_motion_groups(self) -> dict[str, MotionGroupConfiguration]:
        return self._connected_motion_groups

    def find_connected_motion_group_by(
        self,
        prim_path: str = None,
        host: str = None,
        secured: bool = None,
        cell: str = None,
        controller: str = None,
        motion_group: str = None,
    ) -> list[MotionGroupConfiguration]:
        results = []
        for connected_motion_group in self._connected_motion_groups.values():
            if prim_path and connected_motion_group.prim_path != prim_path:
                continue
            stream_config = connected_motion_group.motion_stream_configuration
            # Normalized: callers pass NOVAInstance.host, which may carry a
            # scheme the stored host does not have.
            if host and normalize_host(stream_config.host) != normalize_host(host):
                continue
            if secured is not None and stream_config.secure_connection != secured:
                continue
            if cell and stream_config.cell != cell:
                continue
            if controller and stream_config.controller != controller:
                continue
            if motion_group and stream_config.motion_group != motion_group:
                continue
            results.append(connected_motion_group)

        return results

    def sync_connected_motion_groups_from_stage(self):
        # Rebuild the connection registry from the stage. Robots ship with
        # MotionGroupAPI applied but empty attrs; only treat a prim as connected
        # once it carries a real host, so the registry reflects actual connections
        # (assigned) and everything else stays discoverable as unassigned.
        try:
            self.clear_connected_motion_groups()
            for config in self._get_stage_motion_group_configurations():
                if config.motion_stream_configuration.host:
                    self.add_to_connected_motion_groups(config.identifier, config)
                    self.set_selected_articulation(config.identifier, config.prim_path)
        except Exception as e:
            carb.log_error(f"Failed to sync motion group connections: {e}")

    def _get_stage_motion_group_configurations(self) -> list[MotionGroupConfiguration]:
        stage = stage_utils.get_current_stage()
        if stage is None:
            return []

        motion_group_configs: list[MotionGroupConfiguration] = []
        for prim_path in get_scene_motion_group_prim_paths(
            include_prims_without_api=False
        ):
            prim = stage.GetPrimAtPath(prim_path)
            config = get_motion_group_configuration_from_prim(prim)
            if config is not None:
                motion_group_configs.append(config)
        return motion_group_configs

    def list_stage_instances(
        self,
        known_hosts: set[str],
    ) -> list[NOVACustomInstance]:
        """Crawl the stage for prims with MotionGroupAPI whose host is not in
        *known_hosts* and return a `NOVACustomInstance` per unknown host."""
        motion_group_configs = self._get_stage_motion_group_configurations()
        instances = filter_unknown_host_instances(motion_group_configs, known_hosts)
        for inst in instances:
            carb.log_verbose(f"Discovered orphan instance from stage: {inst.host}")
        return instances

    def list_cells_from_stage(
        self,
        host: str,
    ) -> list[NOVACellData]:
        """Build cell / controller / motion-group data purely from the stage
        prims that reference *host*.  Used as a fallback when the NOVA
        instance is unreachable."""
        motion_group_configs = self._get_stage_motion_group_configurations()
        return list_cells_for_host(motion_group_configs, host)

    def find_stage_config_for_motion_group(
        self,
        host: str,
        cell: str,
        controller: str,
        motion_group: str,
    ) -> Optional[MotionGroupConfiguration]:
        """Return the stage configuration whose host, cell, controller and
        motion-group match the given values, or ``None``."""
        wanted_host = normalize_host(host)
        for config in self._get_stage_motion_group_configurations():
            sc = config.motion_stream_configuration
            if (
                normalize_host(sc.host) == wanted_host
                and sc.cell == cell
                and sc.controller == controller
                and sc.motion_group == motion_group
            ):
                return config
        return None

    @property
    def instances_api(self) -> NOVAInstancesAPI:
        return self._instances_api
