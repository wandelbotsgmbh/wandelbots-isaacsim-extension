from typing import Callable, Optional
import weakref
import carb
import omni.ui as ui
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
    NOVACellData,
    MIN_VERSION as MIN_NOVA_VERSION,
)
from wandelbots.omni.ui.colors import NOVAColor
from .widgets.motion_group_section import MotionGroupSection


class InstanceWidget(ui.VStack):
    """Displays a single NOVA instance with its cells and motion groups."""

    def __init__(
        self,
        instance: NOVAInstance,
        instances_service: NOVAInstancesService,
        on_remove: Optional[Callable[[NOVAInstance], None]] = None,
        on_toggle_status: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        kwargs.setdefault("spacing", 5)
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._instance = instance
        self._instances_service = instances_service
        self._on_remove = on_remove
        self._on_toggle_status = on_toggle_status
        self._motion_group_sections: list = []
        self._fetch_cells()
        self.rebuild()

    @property
    def host(self) -> str:
        return self._instance.host

    def rebuild(self):
        """Clear and rebuild the entire instance UI."""
        self._motion_group_sections.clear()
        self.clear()
        with self:
            ui.Spacer(height=2)
            self._build_header()
            ui.Spacer(height=1)
            self._build_body()

    def _build_header(self):
        instance = self._instance
        with ui.HStack(height=20, spacing=5, alignment=ui.Alignment.CENTER):
            ui.Spacer(width=5)
            ui.Circle(
                radius=5,
                width=10,
                size_policy=ui.CircleSizePolicy.FIXED,
                alignment=ui.Alignment.CENTER,
                style={"background_color": instance.status_color},
                tooltip=f"Status: {instance.status}",
            )
            ui.Label(
                f"{instance.display_name} ({instance.version or 'n/a'})",
                style={"font_size": 17},
                width=40,
            )
            ui.Spacer(width=5)
            ui.Button(
                image_url=get_icon("external_link.svg"),
                width=20,
                height=20,
                style={"color": NOVAColor.ACTION_ACTIVE.color},
                tooltip=f"Open instance {instance.host} in browser.",
                clicked_fn=lambda _self=self: _self._on_open_in_browser(),
            )
            ui.Spacer()
            if isinstance(instance, NOVACustomInstance) and self._on_remove:
                ui.Button(
                    image_url=get_icon("delete.svg"),
                    width=20,
                    height=20,
                    tooltip="Remove this instance from the list.",
                    style={"color": NOVAColor.ACTION_ACTIVE.color, "padding": 0},
                    clicked_fn=lambda _self=self: _self._on_remove(_self._instance),
                )
            elif isinstance(instance, NOVACloudInstance):
                self._build_toggle_status_button(instance.auth_config_id)
            ui.Spacer(width=5)

    def _build_body(self):

        if not self._instance.is_reachable:
            stage_cells = self._instances_service.list_cells_from_stage(
                self._instance.host
            )
            self._label(
                "Instance is not reachable. Showing configured motion groups from stage."
                if stage_cells
                else "Instance is not reachable. Please check your network connection and instance settings."
            )
            if stage_cells:
                self._instance.cells = stage_cells
                self._sync_connected_motion_groups()
                for cell in self._instance.cells:
                    self._build_cell(cell)
            return

        if not self._instance.is_running:
            self._label("Instance is not running. Press play to start it.")
            return

        if self._instance.cells is None:
            self._label("Loading instance data...")
            return

        if not self._instance.is_compatible:
            self._label(
                f"Please update your Wandelbots NOVA instance to at least {MIN_NOVA_VERSION}."
            )
            return

        if not self._instance.cells:
            self._label("No cells available.")
            return

        self._sync_connected_motion_groups()
        if len(self._instance.cells) == 1:
            self._build_cell(self._instance.cells[0])
            return

        with ui.VStack(spacing=3):
            for cell in self._instance.cells:
                with ui.CollapsableFrame(title=cell.name, height=0, collapsed=True):
                    self._build_cell(cell)

    def _build_cell(self, cell: NOVACellData):
        with ui.VStack(spacing=5):
            if cell.controllers:
                for controller in cell.controllers:
                    if not controller.motion_groups:
                        self._label("No motion groups available")
                        continue
                    for mg in controller.motion_groups:
                        with ui.HStack(height=0):
                            ui.Spacer(width=10)
                            section = MotionGroupSection(
                                instances_service=self._instances_service,
                                instance=self._instance,
                                controller=controller,
                                motion_group=mg,
                                on_connection_changed=lambda ref=weakref.ref(self): (
                                    ref().rebuild() if ref() else None
                                ),
                            )
                            self._motion_group_sections.append(section)
            else:
                self._label("No controllers available")
            ui.Spacer()

    def _build_toggle_status_button(self, auth_config_id: str):
        inst = self._instance
        if inst.status == "running":
            tooltip, icon, enabled = "Stop this instance.", "stop.svg", True
        elif inst.status == "stopped":
            tooltip, icon, enabled = "Start this instance.", "play.svg", True
        else:
            tooltip, icon, enabled = "Loading...", "pending.svg", False

        ui.Button(
            image_url=get_icon(icon),
            width=20,
            height=20,
            tooltip=tooltip,
            style={"color": NOVAColor.ACTION_ACTIVE.color, "padding": 0},
            enabled=enabled,
            clicked_fn=lambda _self=self: (
                _self._instances_service.toggle_instance_status(
                    auth_config_id,
                    _self._instance,
                    callback=_self._on_toggle_status,
                )
            ),
        )

    def _sync_connected_motion_groups(self):
        """Load all stage motion group configs into the instances service."""
        try:
            service = get_motion_group_service()
            if not service:
                return
            self._instances_service.clear_connected_motion_groups()
            for prim_path in service.get_all_motion_group_prim_paths():
                try:
                    config: MotionGroupConfiguration = (
                        service.get_motion_group_configuration(prim_path)
                    )
                    if config:
                        self._instances_service.add_to_connected_motion_groups(
                            config.identifier, config
                        )
                        self._instances_service.set_selected_articulation(
                            config.identifier, config.prim_path
                        )
                except Exception as e:
                    carb.log_warn(
                        f"Could not get configuration for motion group {prim_path}: {e}"
                    )
        except Exception as e:
            carb.log_error(f"Failed to load motion group connections: {e}")

    def _fetch_cells(self):
        self._instance.cells = None
        weak_self = weakref.ref(self)

        async def _load():
            ref = weak_self()
            if ref is None:
                return
            try:
                if (
                    isinstance(ref._instance, NOVACloudInstance)
                    and ref._instance.status
                    and ref._instance.status.lower() != "running"
                ):
                    ref._instance.cells = []
                    return
                cells = (
                    await ref._instances_service.instances_api.fetch_cells_for_instance(
                        ref._instance
                    )
                )
                carb.log_info(
                    f"Loaded {len(cells)} cells for instance "
                    f"{ref._instance.display_name}"
                )
                ref._instance.cells = cells if cells is not None else []
            except Exception as e:
                carb.log_warn(
                    f"Failed to load cells for {ref._instance.display_name}: {e}"
                )
                ref._instance.cells = []
            finally:
                ref = weak_self()
                if ref is not None:
                    ref.rebuild()

        try:
            run_coroutine(_load())
        except Exception as e:
            carb.log_error(f"Error scheduling cell load: {e}")
            self._instance.cells = []

    def _on_open_in_browser(self):
        import webbrowser

        try:
            carb.log_info(f"Opening instance {self._instance.host} in browser")
            webbrowser.open(self._instance.host)
        except Exception as e:
            carb.log_error(f"Failed to open instance in browser: {e}")

    @staticmethod
    def _label(text: str):
        with ui.HStack():
            ui.Spacer(width=15)
            ui.Label(text, style={"color": NOVAColor.TEXT_SECONDARY.color})
