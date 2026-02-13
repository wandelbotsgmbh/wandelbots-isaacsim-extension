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
)
from wandelbots.omni.environment import instance_store
from wandelbots.omni.manipulators import (
    is_prim_motion_group,
    get_motion_group_service,
    MotionGroupConfiguration,
    MotionStreamConfiguration,
)
from pxr import Usd
from omni.kit.async_engine import run_coroutine

import isaacsim.core.utils.stage as stage_utils
from .instances_api import get_instances_api


class NOVAInstancesService:
    def __init__(self):
        self._cloud_instances: list[NOVACloudInstance] = []
        self._custom_instances: list[NOVACustomInstance] = []
        self._instances_api = get_instances_api()
        self._connected_motion_groups: dict[str, MotionGroupConfiguration] = {}
        self._selected_articulations: dict[str, str] = {}

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

            if motion_group.motion_stream_configuration.host == instance.host:
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
        use_external_joint_stream: bool,
        callback: Optional[callable] = None,
    ):
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

            if is_prim_motion_group(prim):
                carb.log_info(
                    f"Articulation at {prim_path} is already connected. Updating configuration to selected motion group"
                )

            motion_stream_config = MotionStreamConfiguration(
                host=instance.host,
                secure_connection=instance.is_secure_connection,
                cell=controller.cell_name,
                controller=controller.name,
                motion_group=motion_group_name,
                use_external_joint_stream=use_external_joint_stream,
            )

            motion_group_config = MotionGroupConfiguration(
                name=motion_group_name,
                prim_path=prim_path,
                motion_stream_configuration=motion_stream_config,
            )

            async def create_motion_group_async():
                try:
                    await motion_group_service.create_motion_group(
                        configuration=motion_group_config
                    )
                    identifier = motion_group_config.identifier
                    self.add_to_connected_motion_groups(identifier, motion_group_config)
                    carb.log_verbose(
                        f"Successfully connected {identifier} to {prim_path}"
                    )
                    callback(True)
                except Exception as e:
                    carb.log_error(
                        f"Failed to connect {identifier} to {prim_path}: {e}"
                    )
                    callback(False, "Failed to connect. Try again.")

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
        self._instances_api.toggle_instance_status(
            auth_config_id=auth_config_id, instance=instance
        )
        if callback:
            callback()

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
            if host and stream_config.host != host:
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

    @property
    def instances_api(self) -> NOVAInstancesAPI:
        return self._instances_api
