from typing import Callable
import weakref
import carb
import omni.ui as ui
from isaacsim.gui.components.ui_utils import get_style
from omni.kit.async_engine import run_coroutine
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.ui.utils import get_icon
from wandelbots.omni.manipulators import (
    get_motion_group_service,
    MotionGroupConfiguration,
)
from wandelbots.omni.instances.models import (
    NOVACustomInstance,
    NOVACloudInstance,
    NOVAInstance,
    NOVAControllerData,
    NOVACellData,
    NOVAMotionGroupData,
)
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.instances.motion_group_ui_builder import MotionGroupUIBuilder
from omni.kit.window.property.templates.header_context_menu import (
    GroupHeaderContextMenu,
    GroupHeaderContextMenuEvent,
)
from wandelbots.omni.ui.widgets.switch import Switch
from .models.motion_group_enabled_model import MotionGroupEnabledModel


def _build_frame_header(
    collapsed,
    text: str,
    group_id: str = None,
    motion_group_prim_path: str = None,
):
    group_id = group_id if group_id else text

    if collapsed:
        alignment = ui.Alignment.RIGHT_CENTER
        width = 5
        height = 7
    else:
        alignment = ui.Alignment.CENTER_BOTTOM
        width = 7
        height = 5

    header_stack = ui.HStack(
        name="header_stack",
        spacing=8,
    )
    with header_stack:
        ui.Spacer(width=1)
        with ui.VStack(width=0):
            ui.Spacer()
            ui.Triangle(
                style_type_name_override="CollapsableFrame.Header",
                width=width,
                height=height,
                alignment=alignment,
            )
            ui.Spacer()
        ui.Label(text, style_type_name_override="CollapsableFrame.Header")
        ui.Spacer()
        with ui.HStack(content_clipping=True, width=30):
            ui.Spacer(width=8)
            if motion_group_prim_path:
                Switch(
                    height=17,
                    model=MotionGroupEnabledModel(motion_group_prim_path),
                    tooltip="Toggle motion group for simulation",
                )

            ui.Spacer(width=5)

    def show_context_menu(b):
        if b != 1:
            return

        event = GroupHeaderContextMenuEvent(group_id=group_id, payload=[])
        GroupHeaderContextMenu.on_mouse_event(event)

    header_stack.set_mouse_pressed_fn(lambda x, y, b, _: show_context_menu(b))


class NOVAInstanceUIBuilder:
    def __init__(
        self,
        instance: NOVAInstance,
        instances_service: NOVAInstancesService,
        on_remove: Callable[[NOVAInstance], None] | None = None,
        on_toggle_status: Callable[[], None] | None = None,
    ):
        self._instance = instance
        self._instances_service = instances_service
        self._on_remove = on_remove
        self._on_toggle_status = on_toggle_status
        self._container = ui.VStack(spacing=5, height=0)
        self._style = get_style()
        self._fetch_cells()

    def build_ui(self):
        """Display an instance with its cells and status information."""
        self._container.clear()
        with self._container:
            ui.Spacer(height=2)
            with ui.HStack(height=20, spacing=5, alignment=ui.Alignment.CENTER):
                ui.Spacer(width=5)
                ui.Circle(
                    radius=5,
                    width=10,
                    size_policy=ui.CircleSizePolicy.FIXED,
                    alignment=ui.Alignment.CENTER,
                    style={"background_color": self._instance.status_color},
                    tooltip=f"Status: {self._instance.status}",
                )
                ui.Label(
                    f"{self._instance.display_name} ({self._instance.version if self._instance.version else 'n/a'})",
                    style={"font_size": 17},
                    width=40,
                )
                ui.Spacer(width=5)
                ui.Button(
                    image_url=get_icon("external_link.svg"),
                    width=20,
                    height=20,
                    style={
                        "color": NOVAColor.ACTION_ACTIVE.color,
                    },
                    tooltip=f"Open instance {self._instance.host} in browser.",
                    clicked_fn=lambda: self._on_open_in_browser(),
                )
                ui.Spacer()

                if isinstance(self._instance, NOVACustomInstance):
                    self._display_remove_button()
                elif isinstance(self._instance, NOVACloudInstance):
                    self._display_toggle_status_button(self._instance.auth_config_name)
                ui.Spacer(width=5)
            ui.Spacer(height=1)
            # Display content based on cells state
            if not self._instance.is_compatible:
                with ui.HStack():
                    ui.Spacer(width=15)
                    ui.Label(
                        "Please update your Wandelbots NOVA instance to at least 25.8.0.",
                        style={"color": NOVAColor.TEXT_SECONDARY.color},
                    )
                return
            if self._instance.status != "running":
                with ui.HStack():
                    ui.Spacer(width=15)
                    ui.Label(
                        f"Instance is {self._instance.status}.",
                        style={"color": NOVAColor.TEXT_SECONDARY.color},
                    )
                return

            if self._instance.cells is None:
                with ui.HStack():
                    ui.Spacer(width=15)
                    ui.Label(
                        "Loading instance data...",
                        style={"color": NOVAColor.TEXT_SECONDARY.color},
                    )
            elif len(self._instance.cells) == 1:
                cell = self._instance.cells[0]
                self._display_cell(cell, self._instance)
            elif len(self._instance.cells) > 1:
                with ui.VStack(spacing=3):
                    for cell in self._instance.cells:
                        with ui.CollapsableFrame(
                            title=cell.name,
                            height=0,
                            collapsed=True,
                        ):
                            self._display_cell(cell, self._instance)

            else:
                with ui.HStack():
                    ui.Spacer(width=15)
                    ui.Label(
                        "No cells available.",
                        style={"color": NOVAColor.TEXT_SECONDARY.color},
                    )

    # Display methods
    def _display_remove_button(self):
        ui.Button(
            image_url=get_icon("delete.svg"),
            width=20,
            height=20,
            tooltip="Remove this instance from the list.",
            style={
                "color": NOVAColor.ACTION_ACTIVE.color,
                "padding": 0,
            },
            clicked_fn=lambda instance=self._instance: self._on_remove(instance),
        )

    def _display_toggle_status_button(self, auth_config_name: str):
        def _toggle_instance_status_clicked():
            self._instances_service.toggle_instance_status(
                auth_config_name, self._instance, callback=self._on_toggle_status
            )

        if self._instance.status == "running":
            tooltip = "Stop this instance."
            icon = get_icon("stop.svg")
            enabled = True
        elif self._instance.status == "stopped":
            tooltip = "Start this instance."
            icon = get_icon("play.svg")
            enabled = True
        else:
            tooltip = "Loading..."
            icon = get_icon("pending.svg")
            enabled = False

        ui.Button(
            image_url=icon,
            width=20,
            height=20,
            tooltip=tooltip,
            style={
                "color": NOVAColor.ACTION_ACTIVE.color,
                "padding": 0,
            },
            enabled=enabled,
            clicked_fn=lambda: _toggle_instance_status_clicked(),
        )

    def _display_cell(self, cell: NOVACellData, instance: NOVAInstance):
        """Display a cell and its controllers."""
        with ui.VStack(spacing=5):
            if cell.controllers:
                for controller in cell.controllers:
                    self._display_controller(controller, instance)
            else:
                with ui.HStack():
                    ui.Spacer(width=20)
                    ui.Label("No controllers available")
            ui.Spacer()

    def _display_controller(
        self, controller: NOVAControllerData, instance: NOVAInstance
    ):
        """Display a controller with its motion groups."""
        self._fetch_motion_groups()
        if controller.motion_groups and len(controller.motion_groups) == 1:
            # If there's only one motion group, use it as the frame title
            motion_group = controller.motion_groups[0]
            title = f"{motion_group.motion_group_model_name} ({motion_group.name})"

            motion_groups = self._instances_service.find_connected_motion_group_by(
                host=instance.host,
                cell=controller.cell_name,
                controller=controller.name,
                motion_group=motion_group.name,
            )
            if len(motion_groups) > 1:
                carb.log_warn(
                    f"Multiple connected motion groups found for {motion_group.name} in controller {controller.name} on instance {instance.display_name}"
                )
            motion_group_prim_path = (
                (motion_groups[0]).prim_path if len(motion_groups) else None
            )

            with ui.CollapsableFrame(
                title=title,
                collapsed=True,
                height=0,
                style={
                    "font-weight": "bold",
                    "font-size": "20px",
                    "background_color": NOVAColor.BACKGROUND_DEFAULT.color,
                },
                build_header_fn=lambda collapsed,
                text,
                weak_self=weakref.ref(self): _build_frame_header(
                    collapsed, text, motion_group_prim_path=motion_group_prim_path
                )
                if weak_self()
                else None,
            ):
                self._display_motion_group(
                    motion_group=motion_group, instance=instance, controller=controller
                )
        else:
            # Multiple motion groups or no motion groups - use original layout
            controller_title = f"{controller.motion_groups[0].motion_group_model_name}"
            with ui.CollapsableFrame(title=controller_title, height=0, collapsed=True):
                if controller.motion_groups:
                    with ui.VStack(spacing=5):
                        for motion_group in controller.motion_groups:
                            title = f"{motion_group.motion_group_model_name} ({motion_group.name})"

                            with ui.CollapsableFrame(
                                title=title,
                                collapsed=True,
                                height=0,
                                style={
                                    "font-weight": "bold",
                                    "font-size": "20px",
                                    "background_color": NOVAColor.BACKGROUND_DEFAULT.color,
                                },
                            ):
                                self._display_motion_group(
                                    motion_group=motion_group,
                                    instance=instance,
                                    controller=controller,
                                )
                else:
                    with ui.HStack():
                        ui.Spacer(width=30)
                        ui.Label("No motion groups available")

    def _display_motion_group(
        self,
        motion_group: NOVAMotionGroupData,
        instance: NOVAInstance,
        controller: NOVAControllerData,
    ):
        """Display a single motion group with its configuration."""
        MotionGroupUIBuilder(
            instances_service=self._instances_service,
            instance=instance,
            controller=controller,
            motion_group=motion_group,
            motion_group_connection_changed_fn=lambda weak_self=weakref.ref(
                self
            ): weak_self().build_ui() if weak_self() else None,
        ).build_ui()

    # Handler for events
    def _on_open_in_browser(self):
        import webbrowser

        try:
            carb.log_info(f"Opening instance {self._instance.host} in browser")
            webbrowser.open(self._instance.host)
        except Exception as e:
            carb.log_error(f"Failed to open instance in browser: {e}")

    # Data Management Methods
    def _fetch_motion_groups(self):
        """Load existing motion group connections from the service."""
        try:
            motion_group_service = get_motion_group_service()
            if not motion_group_service:
                return

            self._instances_service.clear_connected_motion_groups()
            prim_paths = motion_group_service.get_all_motion_group_prim_paths()

            for prim_path in prim_paths:
                try:
                    carb.log_info(f"Loading motion group configuration for {prim_path}")
                    motion_group_config: MotionGroupConfiguration = (
                        motion_group_service.get_motion_group_configuration(prim_path)
                    )
                    if motion_group_config:
                        self._instances_service.add_to_connected_motion_groups(
                            motion_group_config.identifier,
                            motion_group_config,
                        )
                        self._instances_service.set_selected_articulation(
                            motion_group_config.identifier,
                            motion_group_config.prim_path,
                        )
                except Exception as e:
                    carb.log_warn(
                        f"Could not get configuration for motion group {prim_path}: {e}"
                    )

        except Exception as e:
            carb.log_error(f"Failed to load motion group connections: {e}")

    def _fetch_cells(self):
        self._instance.cells = None

        async def _load():
            try:
                # Skip fetch for stopped cloud instances
                if (
                    isinstance(self._instance, NOVACloudInstance)
                    and self._instance.status
                    and self._instance.status.lower() != "running"
                ):
                    self._instance.cells = []
                    return
                cells = await self._instances_service.instances_api.fetch_cells_for_instance(
                    self._instance
                )
                carb.log_info(
                    f"Loaded {len(cells)} cells for instance {self._instance.display_name}"
                )
                self._instance.cells = cells if cells is not None else []
            except Exception as e:
                carb.log_warn(
                    f"Failed to load cells for {self._instance.display_name}: {e}"
                )
                self._instance.cells = []
            finally:
                # Rebuild UI after load completes
                self.build_ui()

        try:
            run_coroutine(_load())
        except Exception as e:
            carb.log_error(f"Error scheduling cell load: {e}")
            self._instance.cells = []
