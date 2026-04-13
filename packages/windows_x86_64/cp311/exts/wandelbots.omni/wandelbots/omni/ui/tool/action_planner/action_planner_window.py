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
from wandelbots.omni.constants import EXTENSION_ID, EXTENSION_WINDOW_MENU_ROOT
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
import wandelbots.omni.ui.tool.action_planner.utils as plan_utils
import carb.events
import omni.kit.app
from omni.kit.async_engine import run_coroutine
import omni.kit.actions.core

WINDOW_MENU_ROOT = "Tools"


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
        self._tcp_selector: TcpSelector | None = None

        self._planning_progress_model = ui.SimpleFloatModel(0.0)
        self._planning = False

        self._plan_task: asyncio.Task | None = None

        self.window = ui.Window("Action Planner (Beta)", width=400, height=300)
        self.window.set_visibility_changed_fn(
            lambda _: omni.kit.menu.utils.refresh_menu_items(WINDOW_MENU_ROOT)
        )
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
                        clicked_fn=lambda obj=weakref.proxy(self): (
                            obj._deferred_build_ui()
                        ),
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
                            prim_picked_fn=lambda prim, obj=weakref.proxy(self): (
                                obj.assign_prim(prim)
                            ),
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
                            was_none: bool = self._selected_tcp is None,
                        ):
                            if was_none:
                                weak_self._deferred_build_ui()

                        self._tcp_selector = TcpSelector(
                            api_configuration=stream_config.get_api_configuration(),
                            cell=stream_config.cell,
                            controller=stream_config.controller,
                            motion_group=stream_config.motion_group,
                            tcp_changed_fn=assign_tcp,
                            selected_tcp=self._selected_tcp,
                            select_first_tcp_fallback=True,
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
                            api_configuration=stream_config.get_api_configuration(),
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
                                ghost_object_changed_fn=lambda new_ghost_object, idx=action_index, actions=self._plan_actions: (
                                    actions[idx].__setattr__(
                                        "ghost_object", new_ghost_object
                                    )
                                ),
                                remove_action_fn=lambda idx=action_index, obj=weakref.proxy(self): (
                                    obj._remove_action(idx)
                                ),
                                move_action_fn=lambda direction, idx=action_index, obj=weakref.proxy(self): (
                                    obj._shift_action(idx, idx + direction)
                                ),
                                play_trajectory_fn=lambda _, action=plan_action, obj=weakref.proxy(self): (
                                    obj._request_play_trajectory(action)
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
                            clicked_fn=lambda obj=weakref.proxy(self): (
                                obj._cancel_plan()
                            ),
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
                self.motion_group_configuration.motion_stream_configuration,
                standstill_changed,
                lambda _: None,
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
            and self._selected_tcp is not None
            and len(self._plan_actions) >= 2
            and not self._planning
            and self._play_motion_state is None
        )

    def reset(self):
        self._reference_prim = None
        self._motion_group_prim = None
        self._collision_setup_name = None
        self._planning_progress_model.set_value(0.0)
        self._tcp_selector = None
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
            tcp_name=self._selected_tcp,
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

        motion_group = get_motion_group_configuration_from_prim(self._motion_group_prim)
        if motion_group is None:
            carb.log_error(
                f"Motion group prim {self._motion_group_prim.GetPath()} is not configured with motion group api for play trajectory."
            )
            return

        await plan_utils.play_trajectory(
            motion_group_stream_configuration=motion_group.motion_stream_configuration,
            trajectory=action.trajectory,
            tcp_name=self._selected_tcp,
            motion_start_fn=motion_started,
            continue_fn=lambda: True,
            force_motion_group_state=True,
        )

    def __del__(self):
        self._stage_event_subscription = None
        self._standstill_subscription = None
        self._play_motion_state = None
        if self._plan_task:
            self._plan_task.cancel()
            self._plan_task = None

    @property
    def _selected_tcp(self) -> str | None:
        return self._tcp_selector.selected_tcp if self._tcp_selector else None


@dataclass
class ActionPlannerWindowSubscription:
    action_planner_window: ActionPlannerWindow = None
    menu_subscriptions: list = None

    def __del__(self):
        # Need to explicitly hide the action_planner_window because the docking causes issues on deletion
        if self.action_planner_window:
            self.action_planner_window.window.visible = False

        # Dropping the menu items is not enough we need to explicitly remove them
        omni.kit.menu.utils.remove_menu_items(self.menu_subscriptions, WINDOW_MENU_ROOT)


def register_action_planner_window():
    action_planner_window = ActionPlannerWindow()

    def toggle_visibility():
        action_planner_window.window.visible = not action_planner_window.window.visible

    def _is_visible(
        toolbar: Callable[[], ActionPlannerWindow | None] = weakref.ref(
            action_planner_window
        ),
    ):
        return toolbar().window.visible if toolbar() else False

    ext_id = EXTENSION_ID
    name = "Action Planner (Beta)"
    action_name = "toggle_action_planner_window"
    action_unique = f"{ext_id}_{name}_{action_name}"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(ext_id, action_unique)
    action_registry.register_action(
        ext_id, action_unique, toggle_visibility, display_name=name, tag="MenuItem"
    )

    return ActionPlannerWindowSubscription(
        action_planner_window,
        omni.kit.menu.utils.add_menu_items(
            [
                omni.kit.menu.utils.MenuItemDescription(
                    name=EXTENSION_WINDOW_MENU_ROOT,
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
