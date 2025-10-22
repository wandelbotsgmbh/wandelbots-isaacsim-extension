from dataclasses import dataclass
from typing import cast
import omni.kit.notification_manager as nm
import asyncio
from pxr import Usd
import omni
import weakref
import carb
import omni.ui as ui
import omni.kit.menu.utils
from wandelbots.omni.utils.shims.menu import make_menu_item_description
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.manipulators import (
    get_motion_group_configuration_from_prim,
    is_prim_motion_group,
    MotionGroupConfiguration,
)
from wandelbots.omni.utils.teaching import GhostObjectUtils, GhostObject
from wandelbots.omni.ui.utils import get_icon
import omni.usd
from wandelbots.omni.ui.widgets import (
    TcpSelector,
    PrimPicker,
    PrimPickerDialogProperties,
)
from .widgets import CollisionSetupSelector, ActionItem, PlanAction
from wandelbots.omni.utils.auth import get_auth_token
import wandelbots.omni.ui.tool.action_planner.utils as plan_utils
import carb.events
import omni.kit.app
from omni.kit.async_engine import run_coroutine


@dataclass
class PlayMotionState:
    task: asyncio.Task
    motion_started: bool = False

    def __del__(self):
        if self.task and not self.task.done():
            self.task.cancel()


class ActionPlannerWindow:
    def __init__(self):
        self.window = None

        self._plan_actions: list[PlanAction] = []
        self._motion_group_prim: Usd.Prim = None
        self._collision_setup_name: str | None = None
        self._tcp_name: str | None = None

        self._planning_progress_model = ui.SimpleFloatModel(0.0)
        self._planning = False

        self._plan_task: asyncio.Task | None = None

        self.window = ui.Window("Action Planner (Beta)", width=400, height=300)
        self.window.visible = False
        self.window.deferred_dock_in("Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE)
        self._stage = omni.usd.get_context().get_stage()
        self._standstill_subscription = None
        self._play_motion_state: PlayMotionState | None = None

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

        self._standstill: bool | None = None

        self._build_ui()

    def _build_ui(self):
        self.window.frame.clear()

        if self._stage is None:
            with self.window.frame:
                ui.Label("No stage loaded.", height=30)
            return

        with self.window.frame:
            with ui.VStack(spacing=4):
                with ui.HStack(height=30):
                    ui.Spacer(width=ui.Fraction(1))

                    if self._play_motion_state:
                        ui.Button(
                            "Stop",
                            clicked_fn=lambda obj=weakref.proxy(self): obj._stop_play(),
                        )
                    ui.Button(
                        image_url=get_icon("refresh.svg"),
                        width=ui.Pixel(30),
                        height=ui.Pixel(30),
                        style={
                            "color": NOVAColor.ACTION_ACTIVE.color,
                        },
                        tooltip="Click to refresh pose data",
                        clicked_fn=lambda obj=weakref.proxy(
                            self
                        ): obj._deferred_build_ui(),
                    )
                    ui.Button(
                        clicked_fn=lambda obj=weakref.proxy(self): obj._add_action(),
                        image_url=get_icon("add.svg"),
                        width=ui.Pixel(30),
                        height=ui.Pixel(30),
                        enabled=not self._planning
                        and self._motion_group_prim is not None,
                        style={
                            ":disabled": {"background_color": NOVAColor.DIVIDER.color},
                        },
                    )
                    ui.Button(
                        "Plan",
                        clicked_fn=lambda obj=weakref.proxy(self): obj._request_plan(),
                        style={
                            "background_color": NOVAColor.PRIMARY_MAIN.color,
                            "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                            ":hovered": {
                                "background_color": NOVAColor.PRIMARY_DARK.color
                            },
                            ":disabled": {"background_color": NOVAColor.DIVIDER.color},
                        },
                        width=70,
                        enabled=self._can_plan(),
                    )
                    ui.Spacer(width=15)
                with ui.VGrid(
                    column_count=2,
                    row_height=30,
                    height=0,
                    style={"VGrid": {"margin": 15}},
                ):
                    ui.Label(
                        "Motion group", tooltip="The motion group to plan actions for"
                    )
                    with ui.HStack(height=20):
                        self._motion_group_prim_picker = PrimPicker(
                            stage=self._stage,
                            prim_picked_fn=lambda prim,
                            obj=weakref.proxy(self): obj.assign_prim(prim),
                            prim=self._motion_group_prim,
                            dialog_properties=PrimPickerDialogProperties(
                                filter_fn=is_prim_motion_group,
                                title="Select Motion Group Prim",
                            ),
                        )
                    if self.motion_group_configuration is None:
                        return
                    ui.Label("TCP name", tooltip="TCP of motion group")
                    with ui.HStack(height=20):
                        stream_config = (
                            self.motion_group_configuration.motion_stream_configuration
                        )

                        def assign_tcp(
                            tcp: str,
                            weak_self: ActionPlannerWindow = weakref.proxy(self),
                        ):
                            was_none = weak_self._tcp_name is None
                            weak_self._tcp_name = tcp
                            if was_none:
                                weak_self._deferred_build_ui()

                        TcpSelector(
                            api_configuration=stream_config.get_api_configuration(
                                token=get_auth_token()
                            ),
                            cell=stream_config.cell,
                            controller=stream_config.controller,
                            motion_group=stream_config.motion_group,
                            tcp_changed_fn=assign_tcp,
                            selected_tcp=self._tcp_name,
                        )
                    ui.Label(
                        "Collision setup", tooltip="Collision setup to use for planning"
                    )
                    with ui.HStack(height=20):
                        stream_config = (
                            self.motion_group_configuration.motion_stream_configuration
                        )

                        def assign_collision_setup(
                            collision_setup: str,
                            weak_self: ActionPlannerWindow = weakref.proxy(self),
                        ):
                            weak_self._collision_setup_name = collision_setup

                        CollisionSetupSelector(
                            api_configuration=stream_config.get_api_configuration(
                                token=get_auth_token()
                            ),
                            cell=stream_config.cell,
                            collision_setup_changed_fn=assign_collision_setup,
                            selected_collision_setup=self._collision_setup_name,
                        )

                ui.Line(style={"color": 0x338A8777}, width=ui.Fraction(1), height=1)

                if self._motion_group_prim is None:
                    ui.Label(
                        "Select a robot prim with a motion group first",
                        style={"color": NOVAColor.TEXT_SECONDARY.color},
                    )
                    return

                with ui.ScrollingFrame(
                    vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                    width=ui.Percent(100),
                    height=ui.Fraction(1),
                ):
                    with ui.VStack(spacing=4):
                        ghost_objects: dict[str, GhostObject] = {
                            ghost_object.prim_path: ghost_object
                            for ghost_object in GhostObjectUtils.get_ghost_objects(
                                relative_to_prim=self._motion_group_prim.GetPath().pathString
                            )
                        }

                        for action_index, plan_action in enumerate(self._plan_actions):
                            # Refreshes ghost object if it was moved
                            plan_action.ghost_object = ghost_objects.get(
                                plan_action.ghost_object.prim_path,
                                plan_action.ghost_object,
                            )
                            ActionItem(
                                plan_action,
                                ghost_objects=list(ghost_objects.values()),
                                is_first=action_index == 0,
                                is_last=action_index == len(self._plan_actions) - 1,
                                ghost_object_changed_fn=lambda new_ghost_object,
                                idx=action_index,
                                actions=self._plan_actions: actions[idx].__setattr__(
                                    "ghost_object", new_ghost_object
                                ),
                                remove_action_fn=lambda idx=action_index,
                                obj=weakref.proxy(self): obj._remove_action(idx),
                                move_action_fn=lambda direction,
                                idx=action_index,
                                obj=weakref.proxy(self): obj._shift_action(
                                    idx, idx + direction
                                ),
                                play_trajectory_fn=lambda _,
                                action=plan_action,
                                obj=weakref.proxy(self): obj._request_play_trajectory(
                                    action
                                ),
                            )
                        ui.Spacer(height=ui.Fraction(1))

                if self._planning:
                    with ui.HStack(height=0):
                        ui.ProgressBar(
                            self._planning_progress_model, height=ui.Pixel(10)
                        )
                        ui.Button(
                            text="Cancel",
                            alignment=ui.Alignment.CENTER,
                            clicked_fn=lambda obj=weakref.proxy(
                                self
                            ): obj._cancel_plan(),
                            width=ui.Pixel(70),
                        )

    def _deferred_build_ui(self):
        async def wait_one_frame_and_build():
            await omni.kit.app.get_app().next_update_async()
            self._build_ui()

        run_coroutine(wait_one_frame_and_build())

    @property
    def motion_group_configuration(self) -> MotionGroupConfiguration | None:
        return (
            get_motion_group_configuration_from_prim(self._motion_group_prim)
            if self._motion_group_prim
            else None
        )

    def assign_prim(
        self,
        prim: Usd.Prim,
    ):
        if self._motion_group_prim == prim:
            return
        self._motion_group_prim = prim

        if self.motion_group_configuration is None:
            self._deferred_build_ui()
            return

        def standstill_changed(
            standstill: bool,
            weak_self: ActionPlannerWindow = weakref.proxy(self),
        ):
            motion_group_stopped = weak_self._standstill is False and standstill
            weak_self._standstill = standstill

            # Free planner play websocket if robot is in standstill
            if (
                weak_self._play_motion_state
                and weak_self._play_motion_state.motion_started
                and motion_group_stopped
            ):
                carb.log_info("Motion group reached standstill, stopping play...")
                weak_self._stop_play()
            weak_self._deferred_build_ui()

        self._standstill_subscription = (
            plan_utils.subscribe_motion_group_standstill_state(
                self.motion_group_configuration,
                standstill_changed,
            )
        )

        self._deferred_build_ui()

    def _stop_play(self):
        if not self._play_motion_state:
            return
        carb.log_info("Stopping play task...")
        self._play_motion_state = None

    def _can_plan(self) -> bool:
        return (
            self._motion_group_prim is not None
            and self._collision_setup_name is not None
            and self._tcp_name is not None
            and len(self._plan_actions) >= 2
            and not self._planning
            and self._play_motion_state is None
        )

    def reset(self):
        self._reference_prim = None
        self._motion_group_prim = None
        self._collision_setup_name = None
        self._planning_progress_model.set_value(0.0)
        self._tcp_name = None
        self._exporting = False
        self._deferred_build_ui()

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type == int(omni.usd.StageEventType.OPENED):
            self._stage = omni.usd.get_context().get_stage()
            self.reset()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._stage = None
            self.reset()

    def _shift_action(self, old_index: int, new_index: int):
        action = self._plan_actions.pop(old_index)
        self._plan_actions.insert(new_index, action)
        for action in self._plan_actions:
            action.trajectory = None
        self._deferred_build_ui()

    def _remove_action(self, index: int):
        self._plan_actions.pop(index)
        self._deferred_build_ui()

    def _add_action(self):
        ghost_objects: list[GhostObject] = GhostObjectUtils.get_ghost_objects(
            relative_to_prim=self._motion_group_prim.GetPath().pathString
        )
        self._plan_actions.append(PlanAction(ghost_object=ghost_objects[0]))
        self._deferred_build_ui()

    def _request_plan(self):
        self._planning = True
        self._deferred_build_ui()
        self._plan_task = asyncio.get_event_loop().create_task(self._plan_path())
        self._plan_task.add_done_callback(
            lambda _, obj=weakref.proxy(self): obj._plan_finished()
        )

    def _cancel_plan(self):
        if not self._plan_task:
            return
        self._plan_task.cancel()

    def _plan_finished(self):
        carb.log_info("Plan task completed.")
        self._planning = False
        self._planning_progress_model.set_value(0.0)
        self._plan_task = None
        self._deferred_build_ui()

    async def _plan_path(self):
        carb.log_info("Planning path...")

        await plan_utils.plan_path(
            motion_group_prim=self._motion_group_prim,
            collision_setup_name=self._collision_setup_name,
            tcp_name=self._tcp_name,
            plan_actions=self._plan_actions,
            planning_progress_fn=self._planning_progress_model.set_value,
        )

        nm.post_notification(
            text="Path planned",
            duration=5.0,
        )

    def _request_play_trajectory(self, action: PlanAction):
        if self._play_motion_state is not None:
            carb.log_warn("A trajectory is already being played.")
            return

        self._play_motion_state = PlayMotionState(
            task=asyncio.get_event_loop().create_task(self._play_trajectory(action))
        )
        self._play_motion_state.task.add_done_callback(
            lambda _, obj=weakref.proxy(self): obj._play_finished()
        )
        self._deferred_build_ui()

    def _play_finished(self):
        carb.log_info("Play trajectory task completed.")
        self._play_motion_state = None
        self._deferred_build_ui()

    async def _play_trajectory(self, action: PlanAction):
        carb.log_info("Playing trajectory...")

        def motion_started(weak_self: ActionPlannerWindow = weakref.proxy(self)):
            weak_self._play_motion_state.motion_started = True

        await plan_utils.play_trajectory(
            motion_group_prim=self._motion_group_prim,
            action=action,
            tcp_name=self._tcp_name,
            motion_start_fn=motion_started,
        )

    def __del__(self):
        self._stage_event_subscription = None
        self._standstill_subscription = None
        self._play_motion_state = None
        if self._plan_task:
            self._plan_task.cancel()
            self._plan_task = None


def register_action_planner_window():
    action_planner_window: ActionPlannerWindow = ActionPlannerWindow()

    def _open_window():
        if action_planner_window:
            action_planner_window.window.visible = True

    menu_items = [
        omni.kit.menu.utils.MenuItemDescription(
            name="Wandelbots NOVA",
            sub_menu=[
                make_menu_item_description(
                    "wandelbots.omni",
                    "Action Planner (Beta)",
                    lambda: _open_window(),
                )
            ],
        )
    ]

    return (
        action_planner_window,
        omni.kit.menu.utils.add_menu_items(
            menu_items,
            "Tools",
        ),
    )
