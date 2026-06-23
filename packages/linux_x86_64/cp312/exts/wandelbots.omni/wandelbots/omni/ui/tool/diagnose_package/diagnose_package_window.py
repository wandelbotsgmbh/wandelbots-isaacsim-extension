import asyncio
import weakref
from datetime import datetime

import carb
import omni.ui as ui
import omni.kit.notification_manager as nm
from omni.kit.async_engine import run_coroutine
from isaacsim.gui.components.ui_utils import get_style

from wandelbots.omni.ui.base import BaseUIBuilder
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.styles import TOOLTIP_STYLE, ICON_BTN_STYLE
from wandelbots.omni.ui.utils import get_icon
from wandelbots.omni.ui.widgets.switch import Switch
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import NOVAInstance
from wandelbots.omni.diagnose import create_diagnose_package, get_isaac_sim_log_path

_GUTTER = 10

_TRANSPARENT = ui.color("#00000000")

_PANEL_STYLE = {
    "background_color": NOVAColor.BACKGROUND_PAPER_DARK.color,
    "border_radius": 4,
}

_ROW_STYLE = {
    "background_color": NOVAColor.BACKGROUND_PAPER_DARK.color,
    "border_radius": 2,
}

_PRIMARY_BUTTON_STYLE = {
    "Button": {
        "background_color": NOVAColor.PRIMARY_MAIN.color,
        "border_radius": 3,
        "font_size": 15,
    },
    "Button:hovered": {"background_color": NOVAColor.PRIMARY_DARK.color},
    "Button:disabled": {
        "background_color": NOVAColor.SECONDARY_DARK.color,
        "color": NOVAColor.TEXT_DISABLED.color,
    },
    **TOOLTIP_STYLE,
}


class DiagnosePackageUIBuilder(BaseUIBuilder):
    """Window to create a NOVA + Isaac Sim diagnose package."""

    def __init__(self):
        super().__init__(
            title="Wandelbots NOVA | Diagnose Package",
            width=420,
            height=520,
        )
        self._instances_service = NOVAInstancesService()
        self._instances: list[NOVAInstance] = []
        self._selection: dict[str, bool] = {}
        self._switches: list[Switch] = []
        self._stage_switches: list[Switch] = []
        self._additional_info_model = ui.SimpleStringModel("")
        self._include_stage_tree = False
        self._include_motion_groups = False
        self._instances_container: ui.VStack | None = None
        self._create_button: ui.Button | None = None
        self._status_label: ui.Label | None = None
        self._progress_bar: ui.ProgressBar | None = None
        self._progress_model = ui.SimpleFloatModel(0.0)
        self._task: asyncio.Future | None = None
        self._style = get_style()
        self._style.update(
            {
                "color": NOVAColor.TEXT_PRIMARY.color,
                **TOOLTIP_STYLE,
            }
        )

    def _gather_instances(self) -> list[NOVAInstance]:
        """Collect currently reachable/running instances (cloud + custom)."""
        api = self._instances_service.instances_api
        instances: list[NOVAInstance] = []

        for cloud_instances in api.get_cloud_instances().values():
            for instance in cloud_instances:
                if instance.is_reachable and instance.is_running:
                    instances.append(instance)

        for instance in api.get_custom_instances():
            if instance.is_reachable and instance.is_running:
                instances.append(instance)

        return instances

    def build_ui(self):
        with self._window.frame:
            with ui.VStack(spacing=0, style=self._style):
                with ui.ScrollingFrame(
                    vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                    horizontal_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                    style={"background_color": _TRANSPARENT},
                ):
                    with ui.VStack(spacing=8, height=0):
                        ui.Spacer(height=8)
                        self._build_title()
                        self._build_instances_section()
                        self._build_log_section()
                        self._build_stage_data_section()
                        self._build_additional_info_section()
                        ui.Spacer(height=4)
                self._build_footer()

        self._refresh_instances()

    def _build_title(self):
        with ui.HStack(height=0):
            ui.Spacer(width=_GUTTER)
            ui.Label(
                "Bundle the diagnosis package of the selected NOVA instances and "
                "the current Isaac Sim session log into one zip next to your scene.",
                style={"font_size": 13, "color": NOVAColor.TEXT_SECONDARY.color},
                word_wrap=True,
                height=0,
            )
            ui.Spacer(width=_GUTTER)

    def _build_section_header(self, title: str, show_refresh: bool = False):
        with ui.HStack(height=20, spacing=4):
            ui.Spacer(width=_GUTTER)
            ui.Label(
                title,
                style={"font_size": 13, "color": NOVAColor.TEXT_SECONDARY.color},
                width=0,
            )
            ui.Spacer()
            if show_refresh:
                ui.Button(
                    image_url=get_icon("refresh.svg"),
                    width=20,
                    height=20,
                    style=ICON_BTN_STYLE,
                    tooltip="Refresh the list of connected instances.",
                    clicked_fn=lambda ws=weakref.ref(self): (
                        ws()._refresh_instances() if ws() else None
                    ),
                )
            ui.Spacer(width=_GUTTER)

    def _build_instances_section(self):
        with ui.VStack(spacing=4, height=0):
            self._build_section_header("NOVA INSTANCES", show_refresh=True)
            with ui.HStack(height=0):
                ui.Spacer(width=_GUTTER)
                with ui.ZStack(height=0):
                    ui.Rectangle(style=_PANEL_STYLE)
                    with ui.VStack(height=0):
                        ui.Spacer(height=4)
                        self._instances_container = ui.VStack(spacing=2, height=0)
                        ui.Spacer(height=4)
                ui.Spacer(width=_GUTTER)

    def _build_log_section(self):
        log_path = get_isaac_sim_log_path()
        with ui.VStack(spacing=4, height=0):
            self._build_section_header("ISAAC SIM SESSION LOG")
            with ui.HStack(height=0):
                ui.Spacer(width=_GUTTER)
                with ui.ZStack(height=0):
                    ui.Rectangle(style=_PANEL_STYLE)
                    with ui.HStack(height=0):
                        ui.Spacer(width=8)
                        with ui.VStack(height=0):
                            ui.Spacer(height=8)
                            if log_path:
                                ui.Label(
                                    log_path,
                                    style={
                                        "font_size": 13,
                                        "color": NOVAColor.TEXT_PRIMARY.color,
                                    },
                                    word_wrap=True,
                                    height=0,
                                    tooltip="This log file will be included in the package.",
                                )
                            else:
                                ui.Label(
                                    "Log file could not be located — only the NOVA "
                                    "diagnosis data will be included.",
                                    style={
                                        "font_size": 13,
                                        "color": NOVAColor.WARNING_MAIN.color,
                                    },
                                    word_wrap=True,
                                    height=0,
                                )
                            ui.Spacer(height=8)
                        ui.Spacer(width=8)
                ui.Spacer(width=_GUTTER)

    def _build_stage_data_section(self):
        with ui.VStack(spacing=4, height=0):
            self._build_section_header("EXTRACT FROM STAGE (OPT-IN)")
            with ui.HStack(height=0):
                ui.Spacer(width=_GUTTER)
                with ui.ZStack(height=0):
                    ui.Rectangle(style=_PANEL_STYLE)
                    with ui.VStack(height=0):
                        ui.Spacer(height=4)
                        self._build_stage_toggle_row(
                            "Stage tree",
                            "Include the USD stage hierarchy as stage_tree.txt.",
                            self._on_stage_tree_toggled,
                        )
                        self._build_stage_toggle_row(
                            "Motion groups in Stage",
                            "Include the configured motion groups as motion_groups.json.",
                            self._on_motion_groups_toggled,
                        )
                        ui.Spacer(height=4)
                ui.Spacer(width=_GUTTER)

    def _build_stage_toggle_row(self, label: str, tooltip: str, on_toggle):
        with ui.HStack(height=28, spacing=6, alignment=ui.Alignment.CENTER):
            ui.Spacer(width=10)
            ui.Label(
                label,
                style={"font_size": 15, "color": NOVAColor.TEXT_PRIMARY.color},
                alignment=ui.Alignment.LEFT_CENTER,
            )
            ui.Spacer()
            model = ui.SimpleBoolModel(False)
            model.add_value_changed_fn(
                lambda m, cb=on_toggle: cb(m.get_value_as_bool())
            )
            with ui.VStack(width=0):
                ui.Spacer()
                self._stage_switches.append(
                    Switch(height=18, model=model, tooltip=tooltip)
                )
                ui.Spacer()
            ui.Spacer(width=10)

    def _on_stage_tree_toggled(self, value: bool):
        self._include_stage_tree = value

    def _on_motion_groups_toggled(self, value: bool):
        self._include_motion_groups = value

    def _build_additional_info_section(self):
        with ui.VStack(spacing=4, height=0):
            self._build_section_header("ADDITIONAL INFORMATION")
            with ui.HStack(height=0):
                ui.Spacer(width=_GUTTER)
                with ui.ZStack(height=0):
                    ui.Rectangle(style=_PANEL_STYLE)
                    with ui.HStack(height=0):
                        ui.Spacer(width=8)
                        with ui.VStack(height=0, spacing=6):
                            ui.Spacer(height=8)
                            ui.Label(
                                "Please provide additional information:",
                                style={
                                    "font_size": 13,
                                    "color": NOVAColor.TEXT_SECONDARY.color,
                                },
                                word_wrap=True,
                                height=0,
                            )
                            ui.StringField(
                                self._additional_info_model,
                                multiline=True,
                                height=80,
                            )
                            ui.Spacer(height=8)
                        ui.Spacer(width=8)
                ui.Spacer(width=_GUTTER)

    def _build_footer(self):
        with ui.VStack(spacing=6, height=0):
            ui.Line(style={"color": NOVAColor.DIVIDER.color}, height=1)
            with ui.HStack(height=0):
                ui.Spacer(width=_GUTTER)
                self._status_label = ui.Label(
                    "",
                    style={"font_size": 12, "color": NOVAColor.TEXT_SECONDARY.color},
                    word_wrap=True,
                    height=0,
                    visible=False,
                )
                ui.Spacer(width=_GUTTER)
            with ui.HStack(height=0):
                ui.Spacer(width=_GUTTER)
                self._progress_bar = ui.ProgressBar(
                    self._progress_model,
                    height=4,
                    visible=False,
                    style={
                        "color": NOVAColor.PRIMARY_MAIN.color,
                        "background_color": NOVAColor.PROGRESS_BAR_BACKGROUND.color,
                        "border_radius": 2,
                        "secondary_color": NOVAColor.PROGRESS_BAR_BACKGROUND.color,
                        "font_size": 1,
                    },
                )
                ui.Spacer(width=_GUTTER)
            with ui.HStack(height=0):
                ui.Spacer(width=_GUTTER)
                self._create_button = ui.Button(
                    "Create Package",
                    height=32,
                    style=_PRIMARY_BUTTON_STYLE,
                    tooltip="Download the diagnose data and save the zip next to the scene.",
                    clicked_fn=lambda ws=weakref.ref(self): (
                        ws()._on_create_clicked() if ws() else None
                    ),
                )
                ui.Spacer(width=_GUTTER)
            ui.Spacer(height=10)

    def _refresh_instances(self, *_):
        self._instances = self._gather_instances()
        self._selection = {inst.host: False for inst in self._instances}
        self._rebuild_instances_container()
        self._update_create_button_enabled()

    def _rebuild_instances_container(self):
        if self._instances_container is None:
            return
        self._switches.clear()
        self._instances_container.clear()
        with self._instances_container:
            if not self._instances:
                with ui.HStack(height=40):
                    ui.Spacer(width=10)
                    ui.Label(
                        "No reachable instances. Connect one via 'Connected "
                        "Instances' first.",
                        style={
                            "font_size": 13,
                            "color": NOVAColor.TEXT_SECONDARY.color,
                        },
                        word_wrap=True,
                        alignment=ui.Alignment.LEFT_CENTER,
                    )
                    ui.Spacer(width=10)
                return

            for instance in self._instances:
                self._build_instance_row(instance)

    def _build_instance_row(self, instance: NOVAInstance):
        host = instance.host
        selected = self._selection.get(host, False)
        with ui.ZStack(height=36):
            ui.Rectangle(style=_ROW_STYLE)
            with ui.VStack():
                ui.Spacer()
                with ui.HStack(height=0, spacing=6, alignment=ui.Alignment.CENTER):
                    ui.Spacer(width=10)
                    ui.Label(
                        instance.display_name,
                        style={"font_size": 15, "color": NOVAColor.TEXT_PRIMARY.color},
                        width=0,
                        alignment=ui.Alignment.LEFT_CENTER,
                    )
                    ui.Spacer()
                    ui.Label(
                        instance.host,
                        style={
                            "font_size": 13,
                            "color": NOVAColor.TEXT_SECONDARY.color,
                        },
                        width=0,
                        alignment=ui.Alignment.RIGHT_CENTER,
                    )
                    ui.Spacer(width=6)
                    ui.Circle(
                        radius=4,
                        width=8,
                        size_policy=ui.CircleSizePolicy.FIXED,
                        alignment=ui.Alignment.CENTER,
                        style={"background_color": NOVAColor.SUCCESS_MAIN.color},
                        tooltip=f"Reachable · {instance.status}",
                    )
                    ui.Spacer(width=10)
                    model = ui.SimpleBoolModel(selected)
                    model.add_value_changed_fn(
                        lambda m, h=host, ws=weakref.ref(self): (
                            ws()._on_toggle_changed(h, m.get_value_as_bool())
                            if ws()
                            else None
                        )
                    )
                    with ui.VStack(width=0):
                        ui.Spacer()
                        self._switches.append(
                            Switch(
                                height=18,
                                model=model,
                                tooltip="Include this instance in the diagnose package.",
                            )
                        )
                        ui.Spacer()
                    ui.Spacer(width=10)
                ui.Spacer()

    def _on_toggle_changed(self, host: str, value: bool):
        self._selection[host] = value
        self._update_create_button_enabled()

    def _selected_instances(self) -> list[NOVAInstance]:
        return [inst for inst in self._instances if self._selection.get(inst.host)]

    def _update_create_button_enabled(self):
        if self._create_button is not None:
            self._create_button.enabled = len(self._selected_instances()) > 0

    def _set_status(self, message: str, is_error: bool = False):
        if self._status_label is None:
            return
        self._status_label.text = message
        self._status_label.visible = bool(message)
        self._status_label.style = {
            "font_size": 12,
            "color": (
                NOVAColor.ERROR_MAIN.color
                if is_error
                else NOVAColor.SUCCESS_LIGHT.color
            ),
        }

    def _on_progress(self, message: str, fraction: float):
        self._set_status(message)
        self._progress_model.set_value(fraction)
        if self._progress_bar is not None:
            self._progress_bar.visible = True

    def _hide_progress(self):
        self._progress_model.set_value(0.0)
        if self._progress_bar is not None:
            self._progress_bar.visible = False

    def _on_create_clicked(self):
        selected = self._selected_instances()
        if not selected:
            return
        if self._create_button is not None:
            self._create_button.enabled = False
        self._set_status("Creating diagnose package…")
        self._progress_model.set_value(0.0)
        if self._progress_bar is not None:
            self._progress_bar.visible = True

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._task = run_coroutine(self._create_package(selected, timestamp))

        def _on_done(future: asyncio.Future):
            try:
                future.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                carb.log_error(f"Diagnose package creation failed: {exc}")
                self._set_status(f"Failed: {exc}", is_error=True)
                nm.post_notification(
                    f"Diagnose package failed: {exc}",
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
            finally:
                self._hide_progress()
                self._update_create_button_enabled()
                self._task = None

        self._task.add_done_callback(_on_done)

    async def _create_package(self, instances: list[NOVAInstance], timestamp: str):
        result = await create_diagnose_package(
            instances,
            timestamp,
            additional_info=self._additional_info_model.get_value_as_string(),
            include_stage_tree=self._include_stage_tree,
            include_motion_groups=self._include_motion_groups,
            progress_cb=self._on_progress,
        )

        if result.failed_instances:
            failed = ", ".join(result.failed_instances)
            nm.post_notification(
                f"Some instances failed: {failed}",
                duration=6.0,
                status=nm.NotificationStatus.WARNING,
            )

        self._set_status(f"Saved to {result.path}")
        nm.post_notification(
            f"Diagnose package created: {result.path}",
            duration=6.0,
            status=nm.NotificationStatus.INFO,
        )
