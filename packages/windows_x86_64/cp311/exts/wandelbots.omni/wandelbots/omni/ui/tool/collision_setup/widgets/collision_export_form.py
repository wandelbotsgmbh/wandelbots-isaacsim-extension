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
from wandelbots.omni.manipulators import (
    get_motion_group_configuration_from_prim,
    is_prim_motion_group,
    MotionGroupConfiguration,
)
import omni.usd
from wandelbots.omni.ui.widgets import (
    PrimPicker,
    PrimPickerDialogProperties,
)
from wandelbots.omni.ui.utils import defer_call, weak_cb
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.widgets.form_row import form_row, message_row
from wandelbots.omni.ui.widgets.instance_picker import InstancePicker
from wandelbots.omni.ui.widgets.styled_checkbox import styled_checkbox
from wandelbots.omni.ui.wb_theme import (
    TOOLTIP_RESET,
    BUTTON_HEIGHT,
    BUTTON_PRIMARY_STYLE,
    BUTTON_STYLE,
    FIELD_STYLE,
    FORM_HEADER_GAP,
    FORM_SIDE_MARGIN,
    PROGRESS_BAR_STYLE,
    SECTION_GAP,
    SPACING_MD,
    SPACING_SM,
    build_tooltip,
)
from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVAInstance
import carb.events
from omni.kit.async_engine import run_coroutine
import wandelbots.usd as wb_schema  # type: ignore
import wandelbots.omni.ui.overlay as overlay
import wandelbots.omni.ui.overlay.collision_world.collision_world_overlay as collision_overlay


class CollisionExportForm:
    def __init__(self):
        self._stage: Usd.Stage = omni.usd.get_context().get_stage()

        self._stabilization_delay_model = ui.SimpleFloatModel(1.0)
        self._motion_group_prim: Usd.Prim = None
        self._tool_prim: Usd.Prim | None = None
        self._collision_setup_name = ui.SimpleStringModel("collision_setup")
        self._export_progress_model = ui.SimpleFloatModel(0.0)
        self._auto_load_collision_setup = ui.SimpleBoolModel(True)
        self._export_tool_colliders = ui.SimpleBoolModel(False)
        self._export_link_colliders = ui.SimpleBoolModel(False)

        self._instance_picker = InstancePicker(
            tooltip="NOVA instance the collision setup will be stored to.",
            instance_changed_fn=weak_cb(self, "_on_instance_changed"),
            instances_refreshed_fn=weak_cb(self, "_deferred_build_ui"),
        )

        self._self_collision = ui.SimpleBoolModel(True)
        self._collision_sweep_parameters_input: CollisionSweepParametersInput | None = (
            None
        )

        self._input_errors: list[str] = []
        self._export_task: asyncio.Future | None = None
        # Collapsed state per section title, persisted across the
        # frame.clear() rebuilds this form performs on every state change.
        self._section_collapsed: dict[str, bool] = {}

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
        self._instance_picker.refresh()
        run_coroutine(self._prefill_form()).add_done_callback(
            lambda _: self._deferred_build_ui()
        )

    def _make_section(self, title: str, default_collapsed: bool) -> CollapsibleSection:
        def _on_collapsed_changed(
            value: bool, section_title=title, weak_self=weakref.proxy(self)
        ):
            weak_self._section_collapsed[section_title] = value

        return CollapsibleSection(
            title,
            collapsed=self._section_collapsed.get(title, default_collapsed),
            on_collapsed_changed=_on_collapsed_changed,
        )

    def _build_ui(self):
        self.frame.clear()
        if self._stage is None:
            with self.frame:
                ui.Label("No stage loaded.", height=30)
            return

        with self.frame:
            with ui.VStack(spacing=SECTION_GAP):
                self._build_target_section()
                self._build_sweep_section()
                self._build_robot_and_tool_section()
                self._build_options_section()
                self._build_input_error_labels()
                ui.Spacer(height=SPACING_SM)
                self._build_action_row()
                ui.Spacer(height=ui.Fraction(1))

    def _build_target_section(self):
        section = self._make_section("Target", default_collapsed=False)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            self._instance_picker.build_row()
            with form_row(
                "Collision setup name",
                tooltip="Name/Id for the collision setup in NOVA",
            ):
                ui.StringField(
                    model=self._collision_setup_name,
                    height=20,
                    style={**FIELD_STYLE, **TOOLTIP_RESET},
                    tooltip_fn=lambda: build_tooltip(
                        "Name/Id for the collision setup in NOVA"
                    ),
                )
            ui.Spacer(height=SPACING_SM)

    def _build_sweep_section(self):
        section = self._make_section("Sweep", default_collapsed=False)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            # The sweep widget's rows carry their own form_row insets, so they
            # already align with the other sections' rows.
            self._collision_sweep_parameters_input = CollisionSweepParametersInput(
                default_parameters=self.sweep_parameters
            )
            ui.Spacer(height=SPACING_SM)

    def _build_robot_and_tool_section(self):
        motion_group_configuration: MotionGroupConfiguration | None = (
            get_motion_group_configuration_from_prim(self._motion_group_prim)
            if self._motion_group_prim and self._motion_group_prim.IsValid()
            else None
        )
        section = self._make_section("Robot & Tool", default_collapsed=False)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            self._build_robot_and_tool_rows(motion_group_configuration)
            ui.Spacer(height=SPACING_SM)

    def _build_options_section(self):
        section = self._make_section("Options", default_collapsed=True)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            self._build_options_rows()
            ui.Spacer(height=SPACING_SM)

    def _build_input_error_labels(self):
        for error in self._input_errors:
            message_row(error, NOVAColor.ERROR_MAIN.color)

    def _build_action_row(self):
        if self.exporting:
            self._build_export_progress_row()
            return
        with ui.HStack(height=BUTTON_HEIGHT, spacing=0):
            ui.Spacer(width=ui.Fraction(1))
            ui.Button(
                "Export Collision Setup",
                width=0,
                height=BUTTON_HEIGHT,
                clicked_fn=lambda obj=weakref.proxy(self): (
                    obj._request_export_collisions()
                ),
                style={
                    **BUTTON_PRIMARY_STYLE,
                    "Button:disabled": {
                        "background_color": NOVAColor.ACTION_DISABLED_BACKGROUND.color
                    },
                },
                enabled=len(self.get_input_errors()) == 0,
            )
            ui.Spacer(width=FORM_SIDE_MARGIN)

    def _build_export_progress_row(self):
        with ui.HStack(height=BUTTON_HEIGHT, spacing=SPACING_MD):
            ui.Spacer(width=FORM_SIDE_MARGIN)
            with ui.VStack(width=ui.Fraction(1)):
                ui.Spacer()
                ui.ProgressBar(
                    self._export_progress_model,
                    height=4,
                    style=PROGRESS_BAR_STYLE,
                )
                ui.Spacer()
            ui.Button(
                text="Cancel",
                width=0,
                height=BUTTON_HEIGHT,
                style=BUTTON_STYLE,
                clicked_fn=lambda weak_self=weakref.proxy(self): (
                    weak_self._cancel_export()
                ),
            )
            ui.Spacer(width=FORM_SIDE_MARGIN)

    def _build_robot_and_tool_rows(
        self, motion_group_configuration: MotionGroupConfiguration | None
    ):
        with form_row(
            "Motion group prim",
            tooltip=(
                "Optional. If a robot is physically present in the scene, "
                "select its motion group prim so its own body isn't "
                "accidentally swept in as a static obstacle."
            ),
        ):

            def assign_prim(
                prim: Usd.Prim,
                weak_self: CollisionExportForm = weakref.proxy(self),
            ):
                weak_self._motion_group_prim = prim
                # The tool prim and the export ticks belong to the previous
                # motion group, so a change clears them.
                weak_self._tool_prim = None
                weak_self._export_tool_colliders.set_value(False)
                weak_self._export_link_colliders.set_value(False)
                if prim:
                    run_coroutine(weak_self._prefill_form()).add_done_callback(
                        lambda _: weak_self._deferred_build_ui()
                    )
                else:
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

        if motion_group_configuration is not None:
            with form_row(
                "Tool prim",
                tooltip="Optional. Prim representing the tool for which to export collisions",
            ):

                def is_tool_of_motion_group(prim: Usd.Prim) -> bool:
                    if motion_group_configuration is None:
                        return False
                    return (
                        prim.HasAPI(wb_schema.ToolAPI)
                        and SchemaUtils.find_tool_linked_motion_group(prim).GetPath()
                        == self._motion_group_prim.GetPath()
                    )

                def assign_tool_prim(
                    prim: Usd.Prim,
                    weak_self: CollisionExportForm = weakref.proxy(self),
                ):
                    weak_self._tool_prim = prim
                    if prim is None:
                        # Without a tool there is nothing to export, so the tick
                        # must not survive behind the disabled checkbox.
                        weak_self._export_tool_colliders.set_value(False)
                    weak_self._deferred_build_ui()

                self._tool_prim_picker = PrimPicker(
                    stage=self._stage,
                    prim_picked_fn=assign_tool_prim,
                    prim=self._tool_prim,
                    dialog_properties=PrimPickerDialogProperties(
                        filter_fn=is_tool_of_motion_group,
                        title="Select Tool",
                    ),
                )

            with form_row(
                "Export tool colliders",
                tooltip=(
                    "Export the selected tool's colliders relative to the "
                    "flange frame, so they ride the robot during collision "
                    "checking. Requires a tool prim. The tool is excluded "
                    "from the static colliders either way."
                ),
            ):
                with ui.HStack(height=18):
                    tool_checkbox = styled_checkbox(
                        model=self._export_tool_colliders, width=18, height=18
                    )
                    tool_checkbox.enabled = (
                        self._tool_prim is not None and self._tool_prim.IsValid()
                    )
                    ui.Spacer()

            with form_row(
                "Export additional link colliders",
                tooltip=(
                    "Export colliders of additional equipment attached to "
                    "the robot's links, e.g. dress packs, cable carriers, "
                    "valve blocks. The robot's own link geometry is never "
                    "exported - consumers always use NOVA's collision model "
                    "for it."
                ),
            ):
                with ui.HStack(height=18):
                    styled_checkbox(
                        model=self._export_link_colliders, width=18, height=18
                    )
                    ui.Spacer()

    def _build_options_rows(self):
        with form_row(
            "Self collision detection",
            tooltip=(
                "Check the robot's links (and tool) against each other during "
                "collision checking; adjacent links are always excluded."
            ),
        ):
            with ui.HStack(height=18):
                styled_checkbox(model=self._self_collision, width=18, height=18)
                ui.Spacer()

        with form_row(
            "Stabilization delay [s]",
            tooltip="Delay (in s) after which the collision pose fetching is triggered",
        ):
            ui.FloatDrag(
                model=self._stabilization_delay_model,
                min=0,
                step=0.1,
                height=20,
                style={**FIELD_STYLE, **TOOLTIP_RESET},
                tooltip_fn=lambda: build_tooltip(
                    "Delay (in s) after which the collision pose fetching is triggered"
                ),
            )

        with form_row(
            "Auto load",
            tooltip=(
                "Automatically load the exported collision setup into the "
                "Collision World Overlay"
            ),
        ):
            with ui.HStack(height=18):
                styled_checkbox(
                    model=self._auto_load_collision_setup, width=18, height=18
                )
                ui.Spacer()

    def _deferred_build_ui(self):
        defer_call(self._build_ui)

    def refresh_instances(self) -> None:
        """Refetch the instance list in the background. The owning window calls
        this whenever it becomes visible, so instances added or removed via
        Connect-to-NOVA since the constructor's fetch show up."""
        self._instance_picker.refresh()

    def _on_instance_changed(self) -> None:
        # The robot and tool selection belongs to the previous instance's cell,
        # so exporting it into another instance's store would be wrong.
        self._motion_group_prim = None
        self._tool_prim = None
        self._export_tool_colliders.set_value(False)
        self._export_link_colliders.set_value(False)
        self._deferred_build_ui()

    def get_input_errors(self) -> list[str]:
        errors = []
        if self._instance_picker.instance is None:
            errors.append("No NOVA instance selected")
        if self._collision_setup_name.as_string.strip() == "":
            errors.append("Collision setup name is empty")
        if self.sweep_parameters is None:
            errors.append("Select a sweep type")
        return errors

    def _cancel_export(self):
        if self.exporting:
            self._export_task.cancel()
            self._export_task = None
            self._export_progress_model.set_value(0.0)
            self._deferred_build_ui()

    def reset(self):
        self._motion_group_prim = None
        self._tool_prim = None
        self._collision_setup_name.set_value("collision_setup")
        self._export_progress_model.set_value(0.0)
        self._self_collision.set_value(True)
        self._export_tool_colliders.set_value(False)
        self._export_link_colliders.set_value(False)
        self._instance_picker.reset_selection()
        if self.exporting:
            self._export_task.cancel()
            self._export_task = None
        run_coroutine(self._prefill_form()).add_done_callback(
            lambda _: self._deferred_build_ui()
        )

    async def _prefill_form(self):
        if self._stage is None:
            return

        # The motion group is not auto-prefilled: the user picks it explicitly
        # to enable robot stripping and the tool and link exports.
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
        instance = self._instance_picker.instance

        carb.log_info("Exporting collisions...")
        carb.log_info(f"  Instance: {instance.display_name if instance else 'None'}")
        carb.log_info(f"  Collision setup name: {self._collision_setup_name.as_string}")
        carb.log_info("  Sweep type: Sphere")
        carb.log_info(f"  Sweep: {self.sweep_parameters}")
        carb.log_info("  Static collider frame: stage world")
        carb.log_info(
            "  Motion group prim: "
            f"{self._motion_group_prim.GetPath().pathString if self._motion_group_prim else 'None'}"
        )
        carb.log_info(
            f"  Tool prim: {self._tool_prim.GetPath().pathString if self._tool_prim else 'None'}"
        )
        carb.log_info(f"  Export tool colliders: {self._export_tool_colliders.as_bool}")
        carb.log_info(
            f"  Export additional link colliders: {self._export_link_colliders.as_bool}"
        )
        carb.log_info(f"  Self collision detection: {self._self_collision.as_bool}")

        (
            collision_setup,
            cell_id,
        ) = await get_collision_export_service().export_collision_sweep_to_nova(
            instance=instance,
            tool_prim=self._tool_prim,
            collision_setup_id=self._collision_setup_name.as_string,
            sweep_parameters=self.sweep_parameters,
            motion_group_prim=self._motion_group_prim,
            export_tool_colliders=self._export_tool_colliders.as_bool,
            export_link_colliders=self._export_link_colliders.as_bool,
            self_collision=self._self_collision.as_bool,
            progress_callback_fn=lambda v: self._export_progress_model.set_value(v),
            stabilization_wait_time=self._stabilization_delay_model.as_float,
        )

        nm.post_notification(
            text=f"Collision export completed.\n{len(collision_setup.colliders.keys())} colliders\n{len(collision_setup.tool.keys()) if collision_setup.tool else 0} tool colliders",
            duration=5.0,
        )

        if self._auto_load_collision_setup.as_bool:
            self._request_load_collision_setup(instance, cell_id)

    def _request_load_collision_setup(self, instance: NOVAInstance, cell_id: str):
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
            # Static colliders live in the stage world frame, which is where the
            # overlay's None fallback anchors them.
            base_prim=None,
            collision_setup_name=self._collision_setup_name.as_string,
            api_configuration=get_instances_api().get_api_configuration_for_instance(
                instance
            ),
            cell=cell_id,
            motion_group_prim=self._motion_group_prim,
        )

    @property
    def sweep_parameters(self) -> SphereSweepParameters | None:
        if self._collision_sweep_parameters_input:
            return self._collision_sweep_parameters_input.parameters
        else:
            return None
