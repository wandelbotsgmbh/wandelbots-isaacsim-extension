from typing import Optional
import carb
import asyncio
import omni.ui as ui
import re
import ipaddress
from urllib.parse import urlparse
from wandelbots.omni.instances.instances_api import NOVAInstancesAPI
from wandelbots.omni.ui.auth import Auth0UIBuilder
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

from wandelbots.omni.manipulators.motion_group_service import (
    get_motion_group_service,
    MotionGroupConfiguration,
    MotionStreamConfiguration,
)


class NOVAInstancesService:
    def __init__(self):
        self._cloud_instances: list[NOVACloudInstance] = []
        self._custom_instances: list[NOVACustomInstance] = []
        self._instances_api = NOVAInstancesAPI()
        self._connected_motion_groups: dict[str, MotionStreamConfiguration] = {}
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

    def sign_out(self, callback: Optional[callable] = None):
        carb.log_verbose("Signing out user - invalidating auth token")
        invalidate_auth_token()
        self._cloud_instances.clear()
        callback()

    def sign_in(self, container: ui.Widget, callback: Optional[callable] = None):
        auth_token = get_auth_token()
        if auth_token is None:
            Auth0UIBuilder().show(container, callback=callback)

    def remove_instance(
        self, instance: NOVACustomInstance, callback: Optional[callable] = None
    ):
        carb.log_verbose(f"Removing instance: {instance.name}")

        if instance.status == "running":
            self._disconnect_motion_groups(instance)

        instance_store.remove_instance(instance.instance_id)
        callback()

    def _disconnect_motion_groups(self, instance: NOVAInstance):
        motion_group_service = get_motion_group_service()
        prim_paths = motion_group_service.get_all_motion_group_prim_paths()
        carb.log_verbose(f"Prim paths for motion groups: {prim_paths}")

        for prim_path in prim_paths:
            carb.log_verbose(
                f"Checking motion group at {prim_path} for instance {instance.host}"
            )
            motion_group = motion_group_service.get_motion_group(prim_path)
            if not motion_group:
                continue

            if motion_group.motion_stream_configuration.host == instance.host:
                carb.log_verbose(
                    f"Removing motion group {motion_group.name} at {prim_path} for instance {instance.host}"
                )
                try:
                    motion_group_service.remove_motion_group(prim_path)
                    identifier = motion_group.identifier
                    self.remove_from_connected_motion_group(identifier)
                except Exception as e:
                    carb.log_error(
                        f"Failed to remove motion group {motion_group.name} at {prim_path}: {e}"
                    )

    def delete_motion_group(
        self,
        motion_group_config: MotionGroupConfiguration,
        callback: Optional[callable] = None,
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

            if (
                motion_group_service.get_motion_group_by_prim_path(prim_path)
                is not None
            ):
                carb.log_warn(f"Articulation at {prim_path} is already connected.")
                callback(False, "Articulation is already connected.")
                return

            motion_stream_config = MotionStreamConfiguration(
                host=instance.host,
                secure_connection=instance.is_secure_connection,
                cell=controller.cell_name,
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

    def toggle_instance_status(self, instance: NOVACloudInstance, callback: callable):
        self._instances_api.toggle_instance_status(instance)
        callback()

    @property
    def is_signed_in(self) -> bool:
        """Check if user has valid authentication token for cloud access."""
        auth_token = get_auth_token()
        return auth_token != "" and auth_token is not None

    @property
    def connected_motion_groups(self) -> dict[str, MotionStreamConfiguration]:
        return self._connected_motion_groups

    def handle_authentication_error(self):
        invalidate_auth_token()
