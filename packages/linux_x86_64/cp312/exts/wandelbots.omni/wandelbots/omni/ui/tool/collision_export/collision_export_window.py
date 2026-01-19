from dataclasses import dataclass
from typing import Callable, cast
import omni.kit.notification_manager as nm
import asyncio
from pxr import Usd
import omni
import weakref
import carb
import omni.ui as ui
import omni.kit.menu.utils
import omni.kit.actions.core
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.core.collision.collision_export_service import (
    get_collision_export_service,
    SphereSweepParameters,
)
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.manipulators import (
    get_motion_group_configuration_from_prim,
    is_prim_motion_group,
    MotionGroupConfiguration,
)
import omni.usd
from wandelbots.omni.ui.widgets import (
    PrimPicker,
    PrimPickerDialogProperties,
    TcpSelector,
    CoordinatesInput,
    CoordinateInputFieldModel,
)
import carb.events
from omni.kit.async_engine import run_coroutine

WINDOW_MENU_ROOT = "Tools"


class SphereRadiusModel(ui.SimpleFloatModel):
    def min(self):
        return 0


class CollisionExportWindow:
    def __init__(self):
        self.window = None
        self._stage = omni.usd.get_context().get_stage()

        self._sphere_radius_model = SphereRadiusModel(10)
        self._position_models = [
            ui.SimpleFloatModel(0.0),
            ui.SimpleFloatModel(0.0),
            ui.SimpleFloatModel(0.0),
        ]
        self._reference_prim: Usd.Prim = None
        self._motion_group_prim: Usd.Prim = None
        self._collision_setup_name = ui.SimpleStringModel("collision_setup")
        self._export_progress_model = ui.SimpleFloatModel(0.0)

        self._tcp_sphere_radius = SphereRadiusModel(0)
        self._tcp_selector: TcpSelector = None

        self._self_collision = ui.SimpleBoolModel(False)

        self._input_errors: list[str] = []
        self._exporting = False

        self.window = ui.Window("Collision Export (Beta)", width=400, height=300)
        self.window.set_visibility_changed_fn(
            lambda _: omni.kit.menu.utils.refresh_menu_items(WINDOW_MENU_ROOT)
        )
        self.window.visible = False
        self.window.deferred_dock_in("Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE)

        self._stage_event_subscription = (
            cast(
                omni.usd.UsdContext,
                omni.usd.get_context(),
            )
            .get_stage_event_stream()
            .create_subscription_to_pop(
                lambda event, weak_self=weakref.proxy(self): weak_self._on_stage_event(
                    event
                ),
                name="collision_export_window_stage_event",
            )
        )

        self._build_ui()

    def _build_ui(self):
        self.window.frame.clear()
        if self._stage is None:
            with self.window.frame:
                ui.Label("No stage loaded.", height=30)
            return

        motion_group_configuration: MotionGroupConfiguration = (
            get_motion_group_configuration_from_prim(self._motion_group_prim)
            if self._motion_group_prim
            else None
        )

        with self.window.frame:
            with ui.ScrollingFrame(
                vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                width=ui.Percent(100),
                height=ui.Percent(100),
            ):
                with ui.VStack(spacing=4):
                    ui.Label("Collision export settings", height=30)

                    with ui.VGrid(column_count=2, row_height=ui.Pixel(30)):
                        ui.Label(
                            "Collision setup name",
                            tooltip="Name/Id for the collision setup in NOVA",
                        )
                        with ui.HStack(height=20):
                            ui.StringField(model=self._collision_setup_name, height=20)
                        ui.Label(
                            "Sweep type",
                            tooltip="Shape used for sweep collision detection",
                        )
                        ui.ComboBox(0, "Sphere")

                        ui.Label(
                            "Sweep radius",
                            tooltip="Radius for sphere sweep collision detection",
                        )
                        ui.FloatDrag(
                            model=self._sphere_radius_model,
                            min=0,
                            step=0.01,
                            suffix="m",
                            tooltip="Radius for sphere sweep collision detection",
                        )
                        ui.Label("Position", tooltip="World position of the sweep")
                        CoordinatesInput(
                            fields=[
                                CoordinateInputFieldModel(
                                    model=self._position_models[0],
                                    label="X",
                                    tooltip="X position",
                                ),
                                CoordinateInputFieldModel(
                                    model=self._position_models[1],
                                    label="Y",
                                    tooltip="Y position",
                                ),
                                CoordinateInputFieldModel(
                                    model=self._position_models[2],
                                    label="Z",
                                    tooltip="Z position",
                                ),
                            ]
                        )
                        ui.Label(
                            "Reference prim",
                            tooltip="Prim used as coordinate reference for the collider positions",
                        )
                        with ui.HStack(height=20):

                            def assign_prim(
                                prim: Usd.Prim,
                                weak_self: CollisionExportWindow = weakref.proxy(self),
                            ):
                                weak_self._reference_prim = prim

                            self._reference_prim_picker = PrimPicker(
                                stage=self._stage,
                                prim_picked_fn=assign_prim,
                                prim=self._reference_prim,
                                dialog_properties=PrimPickerDialogProperties(
                                    filter_fn=PrimUtils.prim_has_transform,
                                    title="Select Reference Prim",
                                ),
                            )
                        ui.Label(
                            "Motion group prim",
                            tooltip="Prim representing the robot motion group for which to export collisions",
                        )
                        with ui.HStack(height=20):

                            def assign_prim(
                                prim: Usd.Prim,
                                weak_self: CollisionExportWindow = weakref.proxy(self),
                            ):
                                weak_self._motion_group_prim = prim
                                weak_self._deferred_build_ui()

                            self._motion_group_prim_picker = PrimPicker(
                                stage=self._stage,
                                prim_picked_fn=assign_prim,
                                prim=self._motion_group_prim,
                                dialog_properties=PrimPickerDialogProperties(
                                    filter_fn=is_prim_motion_group,
                                    title="Select Motion Group",
                                ),
                            )

                        if motion_group_configuration is None:
                            return
                        ui.Label(
                            "TCP name",
                            tooltip="Tool center point (TCP) used for collision export",
                        )

                        with ui.HStack(height=20):
                            stream_config = (
                                motion_group_configuration.motion_stream_configuration
                            )

                            def assign_tcp(
                                tcp: str,
                                weak_self: CollisionExportWindow = weakref.proxy(self),
                                previous_selection_none=self._selected_tcp is None,
                            ):
                                if previous_selection_none:
                                    weak_self._deferred_build_ui()

                            self._tcp_selector = TcpSelector(
                                api_configuration=stream_config.get_api_configuration(),
                                cell=stream_config.cell,
                                controller=stream_config.controller,
                                motion_group=stream_config.motion_group,
                                tcp_changed_fn=assign_tcp,
                                selected_tcp=self._selected_tcp,
                                select_first_tcp_fallback=False,
                            )
                        if self._selected_tcp is None:
                            return
                        ui.Label(
                            "TCP sphere radius",
                            tooltip="Radius of a sphere which will be attached at the tcp to simulate the tool collider",
                        )
                        ui.FloatDrag(
                            width=ui.Fraction(1),
                            model=self._tcp_sphere_radius,
                            step=0.1,
                            suffix="m",
                            tooltip="TCP sphere radius",
                        )

                        ui.Label("Self collision detection")
                        ui.CheckBox(model=self._self_collision)

                        for error in self._input_errors:
                            ui.Spacer()
                            ui.Label(error, style={"color": NOVAColor.ERROR_MAIN.color})

                        ui.Line(style={"color": 0x338A8777}, width=ui.Fraction(1))
                        ui.Line(style={"color": 0x338A8777}, width=ui.Fraction(1))

                        ui.Spacer()
                        ui.Button(
                            "Export Collisions",
                            clicked_fn=lambda obj=weakref.proxy(
                                self
                            ): obj._request_export_collisions(),
                            style={
                                "background_color": NOVAColor.PRIMARY_MAIN.color,
                                "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                                ":hovered": {
                                    "background_color": NOVAColor.PRIMARY_DARK.color
                                },
                                ":disabled": {
                                    "background_color": NOVAColor.DIVIDER.color
                                },
                            },
                            enabled=not self._exporting,
                        )
                    if self._exporting:
                        ui.ProgressBar(self._export_progress_model, height=ui.Pixel(10))
                    ui.Spacer(height=ui.Fraction(1))

    def _deferred_build_ui(self):
        async def wait_one_frame_and_build():
            await omni.kit.app.get_app().next_update_async()
            self._build_ui()

        run_coroutine(wait_one_frame_and_build())

    def get_input_errors(self) -> list[str]:
        errors = []
        if self._reference_prim is None:
            errors.append("Reference prim not set")
        if self._motion_group_prim is None:
            errors.append("Motion group prim not set")
        if self._collision_setup_name.as_string.strip() == "":
            errors.append("Collision setup name is empty")
        if self._sphere_radius_model.as_float <= 0:
            errors.append("Sphere radius must be greater than 0")
        if self._selected_tcp is None:
            errors.append("TCP name not set")
        if self._tcp_sphere_radius.as_float < 0:
            errors.append("TCP sphere radius must be 0 or greater")
        return errors

    def reset(self):
        self._sphere_radius_model.set_value(10)
        for model in self._position_models:
            model.set_value(0.0)
        self._reference_prim = None
        self._motion_group_prim = None
        self._collision_setup_name.set_value("collision_setup")
        self._export_progress_model.set_value(0.0)
        self._tcp_sphere_radius.set_value(0)
        self._self_collision.set_value(False)
        self._exporting = False
        self._tcp_selector = None
        self._deferred_build_ui()

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type == int(omni.usd.StageEventType.OPENED):
            self._stage = omni.usd.get_context().get_stage()
            self.reset()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._stage = None
            self.reset()

    def _request_export_collisions(self):
        self._input_errors = self.get_input_errors()
        if self._input_errors:
            carb.log_warn("Collision export failed due to input errors:")
            for error in self._input_errors:
                carb.log_warn(f" - {error}")
            self._input_errors = self._input_errors
            self._deferred_build_ui()
            return

        self._exporting = True
        self._deferred_build_ui()
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # Create a new event loop if one doesn't exist
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        task = loop.create_task(self._export_collisions())
        task.add_done_callback(lambda _, a=weakref.proxy(self): a._export_finished())

    def _export_finished(self):
        carb.log_info("Collision export task completed.")
        self._exporting = False
        self._export_progress_model.set_value(0.0)
        self._deferred_build_ui()

    async def _export_collisions(self):
        carb.log_info("Exporting collisions...")
        carb.log_info(f"  Collision setup name: {self._collision_setup_name.as_string}")
        carb.log_info("  Sweep type: Sphere")
        carb.log_info(f"  Sweep radius: {self._sphere_radius_model.as_float}")
        carb.log_info(
            f"  Position: {self._position_models[0].as_float}, {self._position_models[1].as_float}, {self._position_models[2].as_float}"
        )
        carb.log_info(f"  Reference prim: {self._reference_prim.GetPath().pathString}")
        carb.log_info(
            f"  Motion group prim: {self._motion_group_prim.GetPath().pathString}"
        )
        carb.log_info(f"  TCP name: {self._selected_tcp}")
        carb.log_info(f"  TCP sphere radius: {self._tcp_sphere_radius.as_float}")
        carb.log_info(f"  Self collision detection: {self._self_collision.as_bool}")

        colliders = await get_collision_export_service().export_collision_sweep_to_nova(
            reference_prim=self._reference_prim,
            tcp_id=self._selected_tcp,
            collision_setup_id=self._collision_setup_name.as_string,
            sweep_parameters=SphereSweepParameters(
                sweep_type="sphere",
                radius=self._sphere_radius_model.as_float,
                position=[
                    self._position_models[0].as_float,
                    self._position_models[1].as_float,
                    self._position_models[2].as_float,
                ],
            ),
            motion_group_prim=self._motion_group_prim,
            tcp_sphere_radius=self._tcp_sphere_radius.as_float,
            self_collision=self._self_collision.as_bool,
            progress_callback_fn=lambda v: self._export_progress_model.set_value(v),
        )

        nm.post_notification(
            text=f"Collision export completed.\n{len(colliders.keys())} colliders",
            duration=5.0,
        )

    def destroy(self):
        if self.window:
            self.window.visible = False
            self.window = None

    @property
    def _selected_tcp(self) -> str | None:
        return self._tcp_selector.selected_tcp if self._tcp_selector else None


@dataclass
class CollisionExportWindowSubscription:
    collision_export_window: CollisionExportWindow = None
    menu_subscriptions: list = None

    def __del__(self):
        # Need to explicitly hide the collision_export_window because the docking causes issues on deletion
        if self.collision_export_window:
            self.collision_export_window.window.visible = False

        # Dropping the menu items is not enough we need to explicitly remove them
        omni.kit.menu.utils.remove_menu_items(self.menu_subscriptions, WINDOW_MENU_ROOT)


def register_collision_export_window():
    collision_export_window = CollisionExportWindow()

    def toggle_visibility():
        collision_export_window.window.visible = (
            not collision_export_window.window.visible
        )

    def _is_visible(
        toolbar: Callable[[], CollisionExportWindow | None] = weakref.ref(
            collision_export_window
        ),
    ):
        return toolbar().window.visible if toolbar() else False

    ext_id = "wandelbots.omni"
    name = "Collision Export (Beta)"
    action_name = "toggle_collision_export_window"
    action_unique = f"{ext_id}_{name}_{action_name}"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(ext_id, action_unique)
    action_registry.register_action(
        ext_id, action_unique, toggle_visibility, display_name=name, tag="MenuItem"
    )

    return CollisionExportWindowSubscription(
        collision_export_window,
        omni.kit.menu.utils.add_menu_items(
            [
                omni.kit.menu.utils.MenuItemDescription(
                    name="Wandelbots NOVA",
                    sub_menu=[
                        omni.kit.menu.utils.MenuItemDescription(
                            name=name,
                            onclick_action=(ext_id, action_unique),
                            ticked_fn=_is_visible,
                        )
                    ],
                )
            ],
            WINDOW_MENU_ROOT,
        ),
    )
