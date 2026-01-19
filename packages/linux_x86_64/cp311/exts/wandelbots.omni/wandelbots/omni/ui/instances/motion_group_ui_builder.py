import asyncio
import carb
import omni.ui as ui
from typing import Callable, Optional
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.manipulators.utils import get_scene_motion_group_prim_paths
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    get_motion_group_service,
)
from wandelbots.omni.instances.models import (
    NOVAInstance,
    NOVAControllerData,
    NOVAMotionGroupData,
)
from wandelbots.omni.ui.colors import NOVAColor
from .models.external_joint_stream_model import ExternalJointStreamModel


class MotionGroupUIBuilder:
    def __init__(
        self,
        instances_service: NOVAInstancesService,
        instance: NOVAInstance,
        controller: NOVAControllerData,
        motion_group: NOVAMotionGroupData,
        motion_group_connection_changed_fn: Optional[Callable] = None,
    ):
        self._instances_service = instances_service
        self._instance = instance
        self._controller = controller
        self._motion_group = motion_group
        self._selected_articulation: Optional[str] = None
        self._connect_button: ui.Button = None
        self._connection_error_label: ui.Label = None
        self._container = ui.VStack(spacing=10)
        self._articulations = []
        self._use_external_joint_stream_model: ExternalJointStreamModel = None
        self._motion_group_connection_changed_fn = motion_group_connection_changed_fn

        self.connected_motion_group = self.motion_group_config

    def build_ui(self):
        self._articulations = get_scene_motion_group_prim_paths()
        self._container.clear()
        with self._container:
            with ui.VStack(alignment=ui.Alignment.LEFT, spacing=10):
                if self._articulations:
                    self._display_articulation_selector()
                    if self.motion_group_config:
                        self._display_external_joint_stream_checkbox(
                            self.motion_group_config
                        )
                    self._display_connect_button()
                else:
                    with ui.HStack(height=25):
                        ui.Spacer(width=15)
                        ui.Label("Articulation:", width=150)
                        ui.Label(
                            "No articulation found in the scene.", width=250, height=30
                        )
                        ui.Spacer()
                ui.Spacer(height=5)

    # Display methods

    def _display_external_joint_stream_checkbox(
        self, motion_group_config: MotionGroupConfiguration
    ):
        """Display checkbox for using external joint stream."""
        with ui.HStack(height=20):
            ui.Spacer(width=15)
            ui.Label("Sync with simulation:", width=150)

            self._use_external_joint_stream_model = ExternalJointStreamModel(
                motion_group_config.prim_path if motion_group_config else None,
                read_only=motion_group_config is not None,
            )
            checkbox = ui.CheckBox(
                width=20,
                height=20,
                model=self._use_external_joint_stream_model,
                style={
                    "background_color": NOVAColor.SECONDARY_TONAL.color,
                    "color": NOVAColor.SECONDARY_CONTRAST_TEXT.color,
                },
                tooltip="Enable to sync this motion group with the simulation.",
            )

            def on_checkbox_changed(model: ExternalJointStreamModel):
                carb.log_info(
                    f"use_external_joint_stream changing to: {model.get_value_as_bool()}"
                )

                asyncio.get_event_loop().create_task(
                    get_motion_group_service().update_motion_group_stream_configuration(
                        motion_group_prim_path=motion_group_config.prim_path,
                        motion_stream_configuration=model.motion_stream_configuration,
                    )
                )

            checkbox.model.add_value_changed_fn(on_checkbox_changed)
            carb.log_info(
                f"Using external joint stream: {self.use_external_joint_stream}"
            )

    @property
    def use_external_joint_stream(self) -> bool:
        return (
            self._use_external_joint_stream_model.get_value_as_bool()
            if self._use_external_joint_stream_model
            else False
        )

    def _display_articulation_selector(self):
        """Display articulation selector for connecting motion group to an articulation."""
        carb.log_info(
            f"Displaying articulation selector for motion group {self.motion_group_config}"
        )
        if self.motion_group_config:
            with ui.HStack(height=25):
                ui.Spacer(width=15)
                ui.Label("Articulation:", width=150)
                ui.StringField(
                    ui.SimpleStringModel(self.motion_group_config.prim_path),
                    read_only=True,
                )
                ui.Spacer(width=10)
            return

        with ui.HStack(height=25):
            ui.Spacer(width=15)
            ui.Label("Articulation:", width=150)
            dropdown_items = ["-- Select Articulation --"] + self._articulations

            combo = ui.ComboBox(0, *dropdown_items, alignment=ui.Alignment.CENTER)
            ui.Spacer(width=10)

            # Update selection and button state when dropdown changes
            def on_selection_changed(model: ui.AbstractItemModel, _):
                try:
                    current_index = model.get_item_value_model().as_int

                    if current_index > 0 and current_index < len(dropdown_items):
                        # Valid articulation selected (not placeholder)
                        self._selected_articulation = self._articulations[
                            current_index - 1
                        ]
                        self._instances_service.set_selected_articulation(
                            self._selected_articulation, self._selected_articulation
                        )
                    else:
                        self._selected_articulation = None

                    self._update_connect_button()
                    self._update_connection_error_message("")
                except Exception as e:
                    carb.log_error(f"Error getting selection from model: {e}")
                    self._instances_service.remove_from_connected_motion_group(
                        self.identifier
                    )

            combo.model.add_item_changed_fn(on_selection_changed)
            # set initial selection based on stored articulation
            self._selected_articulation = (
                self._instances_service.get_selected_articulation(self)
            )
            selected_index = (
                self._articulations.index(self._selected_articulation) + 1
                if self._selected_articulation
                else 0
            )
            combo.model.get_item_value_model().set_value(selected_index)

    def _display_connect_button(self):
        """Display the connect/disconnect button based on articulation selection."""
        with ui.HStack(height=20):
            ui.Spacer(width=15)
            self._connection_error_label = ui.Label(
                "",
                visible=False,
                multiline=True,
                width=100,
                style={"color": NOVAColor.ERROR_MAIN.color},
            )
            ui.Spacer(width=10)
            ui.Spacer()
            self._connect_button = ui.Button("Connect", width=100, height=20)
            ui.Spacer(width=10)

        self._update_connect_button()

    def _update_connection_error_message(self, message: str):
        self._connection_error_label.text = message
        self._connection_error_label.visible = True if message else False

    def _update_connect_button(self):
        """Toggle the connect button state based on articulation selection."""
        if self._connect_button is None:
            self._connect_button = ui.Button("Connect", width=100, height=20)

        if self.motion_group_config:
            self._connect_button.text = "Disconnect"
            self._connect_button.enabled = True
            self._connect_button.tooltip = "Disconnect from articulation"
            self._connect_button.style = {
                "background_color": NOVAColor.SECONDARY_TONAL.color,
                "color": NOVAColor.SECONDARY_CONTRAST_TEXT.color,
            }
            self._connect_button.set_clicked_fn(lambda: self._on_disconnect())
        elif self._selected_articulation:
            self._connect_button.text = "Connect"
            self._connect_button.enabled = True
            self._connect_button.tooltip = "Connect to selected articulation"
            self._connect_button.style = {
                "background_color": NOVAColor.PRIMARY_MAIN.color,
                "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
            }

            self._connect_button.set_clicked_fn(lambda: self._on_connect())
        else:
            self._connect_button.text = "Connect"
            self._connect_button.enabled = False
            self._connect_button.tooltip = "Please select an articulation first."
            self._connect_button.style = {
                "background_color": NOVAColor.ACTION_DISABLED_BACKGROUND.color,
                "color": NOVAColor.ACTION_DISABLED.color,
            }
            self._connect_button.set_clicked_fn(lambda: self._on_connect())

    def _on_connect(self):
        """Connect an Isaac Sim articulation to a NOVA motion group."""
        # Disable button and change text
        self._connect_button.enabled = False
        self._connect_button.text = "Connecting..."
        self._connect_button.tooltip = "Connecting to articulation..."

        def on_complete(success: bool, message: str = ""):
            if success:
                self.build_ui()
                if self._motion_group_connection_changed_fn:
                    self._motion_group_connection_changed_fn()
            else:
                self._update_connection_error_message(message)
                self._update_connect_button()

        carb.log_verbose(
            f"Connecting motion group {self._motion_group.name} to articulation {self._selected_articulation}"
        )
        try:
            self._instances_service.create_motion_group_from_nova(
                instance=self._instance,
                controller=self._controller,
                motion_group_name=self._motion_group.name,
                prim_path=self._selected_articulation,
                use_external_joint_stream=self.use_external_joint_stream,
                callback=on_complete,
            )
        except Exception as e:
            carb.log_verbose(f"Failed to connect motion_group: {e}")

    def _on_disconnect(self):
        """Connect an Isaac Sim articulation to a NOVA motion group."""
        # Disable button and change text
        self._connect_button.enabled = False
        self._connect_button.text = "Disconnecting..."
        self._connect_button.tooltip = "Disconnecting articulation..."

        def on_complete(success: bool):
            self.build_ui()
            if self._motion_group_connection_changed_fn:
                self._motion_group_connection_changed_fn()

        try:
            self._instances_service.delete_motion_group(
                self.motion_group_config, callback=on_complete
            )
        except Exception as e:
            carb.log_error(f"Failed to disconnect motion group: {e}")

    @property
    def motion_group_config(self) -> Optional[MotionGroupConfiguration]:
        connected_motion_groups = (
            self._instances_service.find_connected_motion_group_by(
                host=self._instance.host,
                secured=self._instance.is_secure_connection,
                controller=self._controller.name,
                cell=self._controller.cell_name,
                motion_group=self._motion_group.name,
            )
        )
        if len(connected_motion_groups) > 1:
            carb.log_warn(
                f"Multiple connected motion groups found, using the first one. {connected_motion_groups}"
            )
        return connected_motion_groups[0] if len(connected_motion_groups) > 0 else None
