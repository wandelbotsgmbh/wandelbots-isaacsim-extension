from typing import cast
import omni.kit.notification_manager as nm
import asyncio
from pxr import Usd
import omni
import weakref
import carb
import omni.ui as ui
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.core.collision.collision_export_service import (
    get_collision_export_service,
    SphereSweepParameters,
)
from wandelbots.omni.ui.tool.collision_setup.widgets.collision_sweep_parameters_input import (
    CollisionSweepParametersInput,
)
from wandelbots.omni.usd.schema_utils import SchemaUtils
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.manipulators import (
    get_motion_group_configuration_from_prim,
    is_prim_motion_group,
    get_scene_motion_group_prim_paths,
    MotionGroupConfiguration,
)
import omni.usd
from wandelbots.omni.ui.widgets import (
    PrimPicker,
    PrimPickerDialogProperties,
)
import carb.events
from omni.kit.async_engine import run_coroutine
import wandelbots.usd as wb_schema  # type: ignore
import wandelbots.omni.ui.overlay as overlay
import wandelbots.omni.ui.overlay.collision_world.collision_world_overlay as collision_overlay

WINDOW_MENU_ROOT = "Tools"


class SphereRadiusModel(ui.SimpleFloatModel):
    def min(self):
        return 0


class CollisionExportForm:
    def __init__(self):
        self._stage: Usd.Stage = omni.usd.get_context().get_stage()

        self._stabilization_delay_model = ui.SimpleFloatModel(1.0)
        self._reference_prim: Usd.Prim = None
        self._motion_group_prim: Usd.Prim = None
        self._tool_prim: Usd.Prim | None = None
        self._collision_setup_name = ui.SimpleStringModel("collision_setup")
        self._export_progress_model = ui.SimpleFloatModel(0.0)
        self._auto_load_collision_setup = ui.SimpleBoolModel(True)

        self._self_collision = ui.SimpleBoolModel(False)
        self._collision_sweep_parameters_input: CollisionSweepParametersInput | None = (
            None
        )

        self._input_errors: list[str] = []
        self._export_task: asyncio.Future | None = None

        self.frame = ui.Frame(height=0)

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
        run_coroutine(self._prefill_form()).add_done_callback(
            lambda _: self._deferred_build_ui()
        )

    def _build_ui(self):
        self.frame.clear()
        if self._stage is None:
            with self.frame:
                ui.Label("No stage loaded.", height=30)
            return

        motion_group_configuration: MotionGroupConfiguration = (
            get_motion_group_configuration_from_prim(self._motion_group_prim)
            if self._motion_group_prim
            else None
        )

        with self.frame:
            with ui.VStack(spacing=4):
                with ui.VGrid(column_count=2, row_height=ui.Pixel(30)):
                    ui.Label(
                        "Collision setup name",
                        tooltip="Name/Id for the collision setup in NOVA",
                    )
                    with ui.HStack(height=20):
                        ui.StringField(model=self._collision_setup_name, height=20)

                self._collision_sweep_parameters_input = CollisionSweepParametersInput(
                    default_parameters=self.sweep_parameters
                )

                with ui.VGrid(column_count=2, row_height=ui.Pixel(30)):
                    ui.Label(
                        "Reference prim",
                        tooltip="Prim used as coordinate reference for the collider positions",
                    )
                    with ui.HStack(height=20):

                        def assign_prim(
                            prim: Usd.Prim,
                            weak_self: CollisionExportForm = weakref.proxy(self),
                        ):
                            weak_self._reference_prim = prim
                            if prim:
                                run_coroutine(
                                    weak_self._prefill_form()
                                ).add_done_callback(
                                    lambda _: weak_self._deferred_build_ui()
                                )
                            else:
                                weak_self._deferred_build_ui()

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
                            weak_self: CollisionExportForm = weakref.proxy(self),
                        ):
                            weak_self._motion_group_prim = prim
                            if prim:
                                run_coroutine(
                                    weak_self._prefill_form()
                                ).add_done_callback(
                                    lambda _: weak_self._deferred_build_ui()
                                )
                            else:
                                self._tool_prim = None
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
                        "Tool prim",
                        tooltip="Prim representing the tool for which to export collisions",
                    )
                    with ui.HStack(height=20):

                        def is_tool_of_motion_group(prim: Usd.Prim) -> bool:
                            if motion_group_configuration is None:
                                return False
                            return (
                                prim.HasAPI(wb_schema.ToolAPI)
                                and SchemaUtils.find_tool_linked_motion_group(
                                    prim
                                ).GetPath()
                                == self._motion_group_prim.GetPath()
                            )

                        def assign_prim(
                            prim: Usd.Prim,
                            weak_self: CollisionExportForm = weakref.proxy(self),
                        ):
                            weak_self._tool_prim = prim
                            weak_self._deferred_build_ui()

                        self._tool_prim_picker = PrimPicker(
                            stage=self._stage,
                            prim_picked_fn=assign_prim,
                            prim=self._tool_prim,
                            dialog_properties=PrimPickerDialogProperties(
                                filter_fn=is_tool_of_motion_group,
                                title="Select Tool",
                            ),
                        )

                    if self._tool_prim is None:
                        return

                    ui.Label("Self collision detection")
                    with ui.HStack(height=20, style={"margin": 2}):
                        ui.CheckBox(model=self._self_collision)

                    ui.Label(
                        "Stabilization delay",
                        tooltip="Delay (in s) after which the collision pose fetching is triggered",
                    )
                    with ui.HStack(height=20):
                        ui.FloatDrag(
                            model=self._stabilization_delay_model,
                            min=0,
                            step=0.1,
                            suffix="s",
                            tooltip="Delay (in s) after which the collision pose fetching is triggered",
                        )

                    ui.Label("Auto load")
                    with ui.HStack(height=20, style={"margin": 2}):
                        ui.CheckBox(
                            model=self._auto_load_collision_setup,
                            tooltip="Automatically load the exported collision setup into the Collision World Overlay",
                        )

                    for error in self._input_errors:
                        ui.Spacer()
                        ui.Label(error, style={"color": NOVAColor.ERROR_MAIN.color})

                    ui.Line(style={"color": 0x338A8777}, width=ui.Fraction(1))
                    ui.Line(style={"color": 0x338A8777}, width=ui.Fraction(1))

                    ui.Spacer()

                    if not self.exporting:
                        ui.Button(
                            "Export Collisions",
                            clicked_fn=lambda obj=weakref.proxy(self): (
                                obj._request_export_collisions()
                            ),
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
                            enabled=not self.exporting
                            and len(self.get_input_errors()) == 0,
                        )
                if self.exporting:
                    with ui.HStack():
                        ui.ProgressBar(self._export_progress_model, height=ui.Pixel(10))
                        ui.Button(
                            text="Cancel",
                            alignment=ui.Alignment.CENTER,
                            clicked_fn=lambda weak_self=weakref.proxy(self): (
                                weak_self._cancel_export()
                            ),
                            width=ui.Pixel(70),
                        )
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
        if self.sweep_parameters is None:
            errors.append("Select a sweep type")
        if self._tool_prim is None:
            errors.append("Tool prim not set")
        return errors

    def _cancel_export(self):
        if self.exporting:
            self._export_task.cancel()
            self._export_task = None
            self._export_progress_model.set_value(0.0)
            self._deferred_build_ui()

    def reset(self):
        self._reference_prim = None
        self._motion_group_prim = None
        self._tool_prim = None
        self._collision_setup_name.set_value("collision_setup")
        self._export_progress_model.set_value(0.0)
        self._self_collision.set_value(False)
        if self.exporting:
            self._export_task.cancel()
            self._export_task = None
        run_coroutine(self._prefill_form()).add_done_callback(
            lambda _: self._deferred_build_ui()
        )

    async def _prefill_form(self):
        if self._stage is None:
            return

        raw_motion_group_prims: list[Usd.Prim] = [
            self._stage.GetPrimAtPath(prim_path)
            for prim_path in get_scene_motion_group_prim_paths(self._stage)
        ]

        motion_group_prims: list[Usd.Prim] = []

        if self._motion_group_prim:
            motion_group_prims = [self._motion_group_prim]
        else:
            motion_group_prims = [
                prim
                for prim in raw_motion_group_prims
                if prim and get_motion_group_configuration_from_prim(prim)
            ]

        if self._reference_prim:
            if not self._motion_group_prim and get_motion_group_configuration_from_prim(
                self._reference_prim
            ):
                motion_group_prims = [self._reference_prim]

        if len(motion_group_prims) == 0 or len(motion_group_prims) > 1:
            return

        motion_group_prim = motion_group_prims[0]

        if self._motion_group_prim is None:
            self._motion_group_prim = motion_group_prim

        if self._reference_prim is None:
            self._reference_prim = self._motion_group_prim

        if self._motion_group_prim and self._tool_prim is None:
            tool_prims = SchemaUtils.list_motion_group_tools(self._motion_group_prim)
            if len(tool_prims) == 1:
                self._tool_prim = tool_prims[0]

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

        self._export_task = run_coroutine(self._export_collisions())
        self._deferred_build_ui()
        self._export_task.add_done_callback(
            lambda future, a=weakref.proxy(self): a._export_finished(future)
        )

    @property
    def exporting(self) -> bool:
        return self._export_task is not None and not self._export_task.done()

    def _export_finished(self, future: asyncio.Future):
        try:
            future.result()  # to raise any exceptions that happened during export
        except asyncio.CancelledError:
            carb.log_info("Collision export task was cancelled.")
        except Exception as e:
            import traceback

            traceback.print_exc()
            carb.log_warn(f"Collision export task failed with error: {e}")
            nm.post_notification(
                text=f"Collision export failed: {e}",
                status=nm.NotificationStatus.WARNING,
            )
        else:
            carb.log_info("Collision export task completed.")
        self._export_task = None
        self._export_progress_model.set_value(0.0)
        self._deferred_build_ui()

    async def _export_collisions(self):
        carb.log_info("Exporting collisions...")
        carb.log_info(f"  Collision setup name: {self._collision_setup_name.as_string}")
        carb.log_info("  Sweep type: Sphere")
        carb.log_info(f"  Sweep: {self.sweep_parameters}")
        carb.log_info(f"  Reference prim: {self._reference_prim.GetPath().pathString}")
        carb.log_info(
            f"  Motion group prim: {self._motion_group_prim.GetPath().pathString}"
        )
        carb.log_info(f"  Tool prim: {self._tool_prim.GetPath().pathString}")
        carb.log_info(f"  Self collision detection: {self._self_collision.as_bool}")

        collision_setup = (
            await get_collision_export_service().export_collision_sweep_to_nova(
                reference_prim=self._reference_prim,
                tool_prim=self._tool_prim,
                collision_setup_id=self._collision_setup_name.as_string,
                sweep_parameters=self.sweep_parameters,
                motion_group_prim=self._motion_group_prim,
                self_collision=self._self_collision.as_bool,
                progress_callback_fn=lambda v: self._export_progress_model.set_value(v),
                stabilization_wait_time=self._stabilization_delay_model.as_float,
            )
        )

        nm.post_notification(
            text=f"Collision export completed.\n{len(collision_setup.colliders.keys())} colliders\n{len(collision_setup.tool.keys()) if collision_setup.tool else 0} tool colliders",
            duration=5.0,
        )

        if self._auto_load_collision_setup.as_bool:
            self._request_load_collision_setup()

    def _request_load_collision_setup(self):
        collision_world_overlay: overlay.CollisionWorldOverlay = (
            overlay.get_overlay_registry().get_overlay(
                overlay.COLLISION_WORLD_OVERLAY_NAME
            )
        )

        if not collision_world_overlay:
            nm.post_notification(
                text="Collision World Overlay is not registered.",
                status=nm.NotificationStatus.WARNING,
            )
            return

        collision_world_overlay.selection = collision_overlay.CollisionSetupSelection(
            motion_group_prim=self._motion_group_prim,
            base_prim=self._reference_prim,
            collision_setup_name=self._collision_setup_name.as_string,
        )

    @property
    def sweep_parameters(self) -> SphereSweepParameters | None:
        if self._collision_sweep_parameters_input:
            return self._collision_sweep_parameters_input.parameters
        else:
            return None
