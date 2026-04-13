import carb
import omni.ui as ui
from typing import Callable, Optional
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import (
    NOVAInstance,
    NOVAControllerData,
    NOVAMotionGroupData,
)
from wandelbots.omni.manipulators import MotionGroupConfiguration
from wandelbots.omni.ui.colors import NOVAColor


class ConnectButton(ui.HStack):
    """Connect / disconnect button for a single NOVA motion group.

    Handles the full connect/disconnect flow internally and emits
    ``MOTION_GROUP_CONNECTION_CHANGED`` events on success.
    """

    def __init__(
        self,
        instances_service: NOVAInstancesService,
        instance: NOVAInstance,
        controller: NOVAControllerData,
        motion_group: NOVAMotionGroupData,
        config: Optional[MotionGroupConfiguration] = None,
        on_connection_changed: Optional[Callable] = None,
        **kwargs,
    ):
        kwargs.setdefault("height", 20)
        super().__init__(**kwargs)

        self._instances_service = instances_service
        self._instance = instance
        self._controller = controller
        self._motion_group = motion_group
        self._config = config
        self._prim_path: Optional[str] = None
        self._use_external_joint_stream: bool = False
        self._on_connection_changed = on_connection_changed

        with self:
            ui.Spacer(width=15)
            self._error_label = ui.Label(
                "",
                visible=False,
                multiline=True,
                width=100,
                style={"color": NOVAColor.ERROR_MAIN.color},
            )
            ui.Spacer(width=10)
            ui.Spacer()
            self._button = ui.Button("Connect", width=100, height=20)
            ui.Spacer(width=10)

        self._apply_state(
            is_connected=config is not None,
            can_connect=False,
        )

    @property
    def prim_path(self) -> Optional[str]:
        return self._prim_path

    @prim_path.setter
    def prim_path(self, value: Optional[str]):
        self._prim_path = value

    @property
    def use_external_joint_stream(self) -> bool:
        return self._use_external_joint_stream

    @use_external_joint_stream.setter
    def use_external_joint_stream(self, value: bool):
        self._use_external_joint_stream = value

    def set_error(self, message: str):
        """Display (or clear) an error message next to the button."""
        self._error_label.text = message
        self._error_label.visible = bool(message)

    def update_state(self, is_connected: bool, can_connect: bool = False):
        """Refresh the visual state (e.g. after the articulation selection changes)."""
        self._apply_state(is_connected, can_connect)

    def _apply_state(self, is_connected: bool, can_connect: bool):
        if is_connected:
            self._button.text = "Disconnect"
            self._button.enabled = True
            self._button.tooltip = "Disconnect from articulation"
            self._button.style = {
                "background_color": NOVAColor.SECONDARY_TONAL.color,
                "color": NOVAColor.SECONDARY_CONTRAST_TEXT.color,
            }
            self._button.set_clicked_fn(self._on_disconnect_motion_group)
        elif can_connect:
            self._button.text = "Connect"
            self._button.enabled = True
            self._button.tooltip = "Connect to selected articulation"
            self._button.style = {
                "background_color": NOVAColor.PRIMARY_MAIN.color,
                "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
            }
            self._button.set_clicked_fn(self._on_connect_motion_group)
        else:
            self._button.text = "Connect"
            self._button.enabled = False
            self._button.tooltip = "Please select an articulation first."
            self._button.style = {
                "background_color": NOVAColor.ACTION_DISABLED_BACKGROUND.color,
                "color": NOVAColor.ACTION_DISABLED.color,
            }
            self._button.set_clicked_fn(self._on_connect_motion_group)

    def _on_connect_motion_group(self):
        if not self._prim_path:
            return

        self._button.enabled = False
        self._button.text = "Connecting..."
        self._button.tooltip = "Connecting to articulation..."

        def on_complete(success: bool, message: str = ""):
            if success:
                if self._on_connection_changed:
                    self._on_connection_changed()
            else:
                self.set_error(message)
                self._apply_state(is_connected=False, can_connect=True)

        carb.log_verbose(
            f"Connecting motion group {self._motion_group.name} to articulation {self._prim_path}"
        )
        try:
            self._instances_service.create_motion_group_from_nova(
                instance=self._instance,
                controller=self._controller,
                motion_group_name=self._motion_group.name,
                prim_path=self._prim_path,
                use_external_joint_stream=self._use_external_joint_stream,
                callback=on_complete,
            )
        except Exception as e:
            carb.log_verbose(f"Failed to connect motion_group: {e}")

    def _on_disconnect_motion_group(self):
        if not self._config:
            return

        self._button.enabled = False
        self._button.text = "Disconnecting..."
        self._button.tooltip = "Disconnecting articulation..."

        def on_complete(success: bool):
            if self._on_connection_changed:
                self._on_connection_changed()

        try:
            self._instances_service.delete_motion_group(
                self._config, callback=on_complete
            )
        except Exception as e:
            carb.log_error(f"Failed to disconnect motion group: {e}")
