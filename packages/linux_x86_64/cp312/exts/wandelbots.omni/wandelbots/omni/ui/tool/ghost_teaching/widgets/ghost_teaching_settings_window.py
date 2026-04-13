import asyncio
from dataclasses import field
from typing import Any, Callable, Literal
import weakref
from attr import dataclass
import omni.ui as ui
import omni.kit.app
from omni.kit.async_engine import run_coroutine
import wandelbots.omni.ui.tool.action_planner.utils as planner_utils
from wandelbots.omni.manipulators import MotionStreamConfiguration
import wandelbots_api_client.v2.models as wb_models
import carb.settings
import wandelbots.omni.ui.colors as color_utils
from wandelbots.omni.ui.overlay.ghost_teaching.ghost_teaching_overlay import (
    CARB_MAX_JOINT_CONFIGS,
    CARB_OVERLAY_COLOR,
    CARB_OVERLAY_VISIBLE,
)
from wandelbots.omni.utils.teaching import CARB_SETTINGS_PREFIX

CARB_STAY_OPEN = f"{CARB_SETTINGS_PREFIX}/stay_open"
CARB_OPEN_WITH_GHOST_OBJECT = f"{CARB_SETTINGS_PREFIX}/open_with_ghost_object"
CARB_SELECT_GHOST_OBJECT_IN_SCENE = (
    f"{CARB_SETTINGS_PREFIX}/select_ghost_object_in_scene"
)
CARB_MOTION_COMMAND = f"{CARB_SETTINGS_PREFIX}/motion_command"


@dataclass
class Observable:
    property_changed_fn: Callable[[str, Any, Any], None] = field(
        init=False, repr=False, default=None
    )

    def _notify(self, property_name: str, old, new):
        if self.property_changed_fn and isinstance(self.property_changed_fn, Callable):
            self.property_changed_fn(property_name, old, new)

    def __setattr__(self, key, value):
        if key == "property_changed_fn":
            super().__setattr__(key, value)
            return
        if key in self.__dict__:
            old = self.__dict__[key]
            if old != value:
                super().__setattr__(key, value)
                self._notify(key, old, value)
        else:
            super().__setattr__(key, value)


@dataclass
class SettingsModel(Observable):
    # Tool bar settings
    stay_open: bool = False
    open_with_ghost_object: bool = True
    select_ghost_object_in_scene: bool = True
    # Ghost teaching settings
    auto_play_simulation: bool = True
    velocity: int = 200
    acceleration: int = 1000
    motion_command: Literal["joint_p2p", "cartesian_p2p", "line"] = "joint_p2p"
    # Overlay settings
    overlay_visible: bool = True
    overlay_color: str = "#A936DA16"
    max_joint_configs: int = 9


class GhostTeachingSettingsWindow(ui.Window):
    def __init__(
        self,
        model: SettingsModel,
        motion_stream_configuration: MotionStreamConfiguration,
    ):
        super().__init__(
            "Ghost Teaching Settings",
            visible=False,
            auto_resize=True,
            flags=ui.WINDOW_FLAGS_POPUP | ui.WINDOW_FLAGS_NO_COLLAPSE,
        )
        self.model = model
        self.motion_stream_configuration = motion_stream_configuration
        self._operation_limits: wb_models.OperationLimits = None
        self._show_task: asyncio.Task = None

    def show(self, x, y):
        if self._show_task and not self._show_task.done():
            self._show_task.cancel()
        self._show_task = run_coroutine(self._show_async(x, y))

    async def _show_async(self, x, y):
        self._operation_limits = await planner_utils.get_operation_limits(
            self.motion_stream_configuration
        )
        self._clamp_model_values()
        await omni.kit.app.get_app().next_update_async()
        self._build_ui()
        self.position_x, self.position_y = x, y
        self.visible = True
        self.focus()

    def _build_ui(self):
        def build_section(name, build_func):
            with ui.CollapsableFrame(name, height=0):
                with ui.HStack():
                    ui.Spacer(width=20)
                    with ui.Frame(margin=5):
                        with ui.VStack(spacing=8, width=ui.Fraction(1)):
                            build_func()

        with self.frame:
            with ui.VStack(height=0, spacing=8):
                build_section("Ghost teaching", self._build_ghost_teaching_settings)
                build_section("Tool bar", self._build_tool_bar_settings)
                build_section("Overlay", self._build_overlay_settings)

    def _build_tool_bar_settings(self):
        with ui.HStack(spacing=8):
            ui.Label("Stay open")
            ui.Spacer(width=ui.Fraction(1))
            int_drag = ui.CheckBox(
                model=ui.SimpleBoolModel(self.model.stay_open),
                name="Stay open",
                width=20,
                tooltip="If enabled, the ghost teaching tool bar will stay open even if no ghost object is selected",
            )

            def set_stay_open(m: ui.SimpleBoolModel):
                self.model.stay_open = m.get_value_as_bool()

            int_drag.model.add_value_changed_fn(set_stay_open)
        with ui.HStack(spacing=8):
            ui.Label("Open with ghost object", width=ui.Fraction(1))
            ui.Spacer(width=ui.Fraction(1))
            int_drag = ui.CheckBox(
                model=ui.SimpleBoolModel(self.model.open_with_ghost_object),
                name="Open with ghost object",
                width=20,
                tooltip="If enabled, the ghost teaching tool bar will open automatically when a ghost object is created",
            )

            def set_open_with_ghost_object(m: ui.SimpleBoolModel):
                self.model.open_with_ghost_object = m.get_value_as_bool()

            int_drag.model.add_value_changed_fn(set_open_with_ghost_object)

        with ui.HStack(spacing=8):
            ui.Label("Select in scene", width=ui.Fraction(1))
            ui.Spacer(width=ui.Fraction(1))
            int_drag = ui.CheckBox(
                model=ui.SimpleBoolModel(self.model.select_ghost_object_in_scene),
                name="Select in scene",
                width=20,
                tooltip="If enabled, the ghost object will be selected in the scene when selected in the tool bar",
            )

            def set_select_in_scene(m: ui.SimpleBoolModel):
                self.model.select_ghost_object_in_scene = m.get_value_as_bool()

            int_drag.model.add_value_changed_fn(set_select_in_scene)

    def _build_overlay_settings(self):
        settings = carb.settings.get_settings()
        with ui.HStack(spacing=8):
            ui.Label("Visible", width=ui.Fraction(1))
            ui.Spacer(width=ui.Fraction(1))
            int_drag = ui.CheckBox(
                model=ui.SimpleBoolModel(self.model.overlay_visible),
                name="Visible",
                width=20,
                tooltip="If enabled, the ghost object overlay will be visible in the viewport",
            )

            def set_overlay_visible(m: ui.SimpleBoolModel):
                self.model.overlay_visible = m.get_value_as_bool()
                settings.set(CARB_OVERLAY_VISIBLE, self.model.overlay_visible)

            int_drag.model.add_value_changed_fn(set_overlay_visible)

        with ui.HStack(spacing=8):
            ui.Label("Mesh color", width=ui.Fraction(1))
            ui.Spacer(width=ui.Fraction(1))

            def _overlay_color_changed(
                model: ui.AbstractItemModel,
                item: ui.AbstractItem,
                weak_self=weakref.ref(self),
            ):
                self_instance = weak_self()

                if not self_instance:
                    return

                color = []
                for item in model.get_item_children():
                    val = model.get_item_value_model(item).get_value_as_float()
                    color.append(val)

                self_instance.model.overlay_color = color_utils.float_array_to_hex(
                    color
                )
                settings.set(CARB_OVERLAY_COLOR, self_instance.model.overlay_color)

            color_picker = ui.ColorWidget(
                *color_utils.hex_to_float_array(self.model.overlay_color),
                width=120,
                tooltip="Color of the ghost object overlay",
            )
            color_picker.model.add_end_edit_fn(_overlay_color_changed)

        with ui.HStack(spacing=8):
            ui.Label("Max joint configs", width=ui.Fraction(1))
            ui.Spacer(width=ui.Fraction(1))
            int_drag = ui.IntDrag(
                model=ui.SimpleIntModel(
                    default_value=self.model.max_joint_configs,
                ),
                min=1,
                max=18,
                name="Max joint configs",
                width=ui.Pixel(120),
                tooltip="Maximum number of joint configurations to display in the overlay",
            )

            def set_max_joint_configs(
                m: ui.SimpleIntModel, weak_self=weakref.ref(self)
            ):
                instance = weak_self()
                if instance:
                    instance.model.max_joint_configs = m.get_value_as_int()
                    settings.set(
                        CARB_MAX_JOINT_CONFIGS, instance.model.max_joint_configs
                    )

            int_drag.model.add_value_changed_fn(set_max_joint_configs)

    def _build_ghost_teaching_settings(self):
        with ui.HStack(spacing=8):

            def to_label_string(value: int) -> str:
                return (
                    f"Velocity ({(float(value) / self.max_tcp_velocity * 100.0):.1f}%)"
                )

            label = ui.Label(to_label_string(self.model.velocity), width=ui.Fraction(1))
            int_drag = ui.IntDrag(
                model=ui.SimpleIntModel(
                    default_value=self.model.velocity,
                ),
                min=10,
                max=self.max_tcp_velocity,
                name="Velocity",
                width=ui.Pixel(120),
                tooltip="Velocity of motion",
            )

            def set_velocity(m: ui.SimpleIntModel):
                self.model.velocity = m.get_value_as_int()
                label.text = to_label_string(self.model.velocity)

            int_drag.model.add_value_changed_fn(set_velocity)
        with ui.HStack(spacing=8):
            ui.Label("Acceleration", width=ui.Fraction(1))
            int_drag = ui.IntDrag(
                model=ui.SimpleIntModel(
                    default_value=self.model.acceleration,
                ),
                min=10,
                name="Acceleration",
                width=ui.Pixel(120),
                tooltip="Acceleration of motion",
            )

            def set_acceleration(m: ui.SimpleIntModel):
                self.model.acceleration = m.get_value_as_int()

            int_drag.model.add_value_changed_fn(set_acceleration)
        _values = ["joint_p2p", "cartesian_p2p", "line"]
        _labels = ["Joint P2P", "Cartesian P2P", "Line"]
        with ui.HStack(spacing=8):
            ui.Label("Motion command", width=ui.Fraction(1))
            current_idx = (
                _values.index(self.model.motion_command)
                if self.model.motion_command in _values
                else 0
            )
            cmd_combo = ui.ComboBox(current_idx, *_labels, width=ui.Pixel(120))

            def set_motion_command(
                model, _, weak_self=weakref.ref(self), values=_values
            ):
                s = weak_self()
                if s:
                    s.model.motion_command = values[model.get_item_value_model().as_int]

            cmd_combo.model.add_item_changed_fn(set_motion_command)

    def _clamp_model_values(self):
        self.model.velocity = max(
            1,
            min(
                self.model.velocity,
                self.max_tcp_velocity,
            ),
        )

    @property
    def max_tcp_velocity(self) -> int:
        if self._operation_limits and self._operation_limits.manual_limits.tcp.velocity:
            return self._operation_limits.manual_limits.tcp.velocity
        # if there is no limit we go with the save jogging limit
        return 250

    @property
    def operation_limit_set(self) -> wb_models.LimitSet:
        return self._operation_limits.manual_limits


def load_ghost_teaching_carb_settings() -> SettingsModel:
    settings: carb.settings.ISettings = carb.settings.get_settings()

    settings_model = SettingsModel()
    if settings.get(CARB_STAY_OPEN) is not None:
        settings_model.select_ghost_object_in_scene = settings.get_as_bool(
            CARB_SELECT_GHOST_OBJECT_IN_SCENE
        )
    if settings.get(CARB_OPEN_WITH_GHOST_OBJECT) is not None:
        settings_model.open_with_ghost_object = settings.get_as_bool(
            CARB_OPEN_WITH_GHOST_OBJECT
        )
    if settings.get(CARB_SELECT_GHOST_OBJECT_IN_SCENE) is not None:
        settings_model.stay_open = settings.get_as_bool(CARB_STAY_OPEN)

    if settings.get(CARB_MOTION_COMMAND) is not None:
        settings_model.motion_command = settings.get_as_string(CARB_MOTION_COMMAND)

    if settings.get(CARB_OVERLAY_VISIBLE) is not None:
        settings_model.overlay_visible = settings.get_as_bool(CARB_OVERLAY_VISIBLE)
    if (
        settings.get(CARB_OVERLAY_COLOR) is not None
        and settings.get(CARB_OVERLAY_COLOR) != ""
    ):
        settings_model.overlay_color = settings.get_as_string(CARB_OVERLAY_COLOR)
    if settings.get(CARB_MAX_JOINT_CONFIGS) is not None:
        settings_model.max_joint_configs = settings.get_as_int(CARB_MAX_JOINT_CONFIGS)
    return settings_model


def save_ghost_teaching_carb_settings(model: SettingsModel):
    settings: carb.settings.ISettings = carb.settings.get_settings()
    settings.set(CARB_STAY_OPEN, model.stay_open)
    settings.set(CARB_OPEN_WITH_GHOST_OBJECT, model.open_with_ghost_object)
    settings.set(CARB_SELECT_GHOST_OBJECT_IN_SCENE, model.select_ghost_object_in_scene)
    settings.set(CARB_MOTION_COMMAND, model.motion_command)
    settings.set(CARB_OVERLAY_VISIBLE, model.overlay_visible)
    settings.set(CARB_OVERLAY_COLOR, model.overlay_color)
    settings.set(CARB_MAX_JOINT_CONFIGS, model.max_joint_configs)
