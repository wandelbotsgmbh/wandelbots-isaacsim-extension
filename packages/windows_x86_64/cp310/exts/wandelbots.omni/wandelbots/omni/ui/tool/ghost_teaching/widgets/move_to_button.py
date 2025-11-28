import enum
import weakref
import carb
import asyncio
from typing import Callable
import omni.ui as ui
from omni.kit.async_engine import run_coroutine
import wandelbots.omni.ui.tool.action_planner.utils as planner_utils
import omni.kit.notification_manager as nm
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.manipulators import MotionStreamConfiguration
from dataclasses import dataclass


class MoveToState(enum.Enum):
    IDLE = 0
    CONFIGURATION = 1
    PLAN = 2
    EXECUTE = 3


@dataclass
class MoveToExecuteSettings:
    motion_stream_configuration: MotionStreamConfiguration
    tcp: str
    motion_command: planner_utils.MotionCommand
    auto_play_simulation: bool = True
    velocity: float = 500
    acceleration: float = 1000


class MoveToButton:
    def __init__(
        self, configure_execution_fn: Callable[[], MoveToExecuteSettings | None]
    ):
        """

        Args:
            configure_execution_fn (Callable[[], MoveToExecuteSettings  |  None]): Configuration may be none if no valid configuration could be created
        """
        self._move_to_task = None
        self._move_to_pressed = False
        self._move_to_state = MoveToState.IDLE
        self._configure_execution_fn = configure_execution_fn
        self._standstill_subscription: planner_utils.MotionGroupStandstillSubscription = None

        self._build_ui()

    def __del__(self):
        self._move_to_pressed = False
        self._stop_move_to()

    def _build_ui(self):
        self._move_to_pressed = False  # rebuilding the button does not trigger the release event, so we reset the state here
        self._button = ui.Button(
            self._get_state_button_text(),
            height=ui.Fraction(1),
            style={
                "margin": 0,
                "background_color": NOVAColor.PRIMARY_MAIN.color,
                "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                ":hovered": {"background_color": NOVAColor.PRIMARY_DARK.color},
                ":disabled": {"background_color": NOVAColor.DIVIDER.color},
            },
            width=90,
            mouse_pressed_fn=lambda x,
            y,
            button,
            modifier,
            weak_self=weakref.proxy(self): weak_self._move_to_mouse_pressed(
                x, y, button, modifier
            ),
            mouse_released_fn=lambda x,
            y,
            button,
            modifier,
            weak_self=weakref.proxy(self): weak_self._move_to_mouse_released(
                x, y, button, modifier
            ),
        )

    def _get_state_button_text(self) -> str:
        if self._move_to_state == MoveToState.IDLE:
            return "Move to"
        elif self._move_to_state == MoveToState.CONFIGURATION:
            return "Configuring..."
        elif self._move_to_state == MoveToState.PLAN:
            return "Planning..."
        elif self._move_to_state == MoveToState.EXECUTE:
            return "Executing..."

    def _set_move_to_state(self, state: MoveToState):
        if state == self._move_to_state:
            return
        self._move_to_state = state
        if self._button:
            self._button.text = self._get_state_button_text()

    def _move_to_mouse_pressed(
        self,
        x: float,
        y: float,
        button: int,
        modifier,
    ):
        carb.log_info("Move to...")
        self._move_to_pressed = True
        configuration = self._configure_execution_fn()
        if not configuration:
            return
        self._move_to_task = run_coroutine(
            self._execute_planning_configuration(configuration)
        )

        def on_done(future: asyncio.Future):
            try:
                future.result()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                carb.log_error(f"Error during move to ghost object: {str(e)}")
            self._set_move_to_state(MoveToState.IDLE)
            self._standstill_subscription = None

        self._move_to_task.add_done_callback(on_done)

    def _move_to_mouse_released(
        self,
        x: float,
        y: float,
        button: int,
        modifier,
    ):
        self._move_to_pressed = False
        self._stop_move_to()
        carb.log_info("Move to stopped")

    def _stop_move_to(self):
        if self._move_to_task is not None:
            self._move_to_task.cancel()

    async def _execute_planning_configuration(
        self, configuration: MoveToExecuteSettings
    ):
        self._set_move_to_state(MoveToState.CONFIGURATION)
        if configuration is None:
            carb.log_error("No configuration provided for planning")
            return
        tcp_offset = await planner_utils.get_tcp_offset_by_name(
            configuration.motion_stream_configuration, configuration.tcp
        )
        carb.log_info(f"Planning with tcp offset: {tcp_offset}")
        if tcp_offset is None:
            nm.post_notification(
                f"{configuration.tcp} TCP not found in {configuration.motion_stream_configuration.motion_group}",
            )
            return

        _, motion_group_joint_positions = await planner_utils.get_motion_group_pose(
            configuration.motion_stream_configuration, tcp_offset
        )
        carb.log_info(f"Current motion group pose: {motion_group_joint_positions}")
        carb.log_info(f"Target ghost object pose: {configuration.motion_command}")

        operation_limits = await planner_utils.get_operation_limits(
            configuration.motion_stream_configuration
        )

        global_limits = operation_limits.manual_limits
        global_limits.tcp.velocity = configuration.velocity
        global_limits.tcp.acceleration = configuration.acceleration

        self._set_move_to_state(MoveToState.PLAN)
        try:
            trajectory = await planner_utils.plan_motion_group_move_to(
                configuration.motion_stream_configuration,
                tcp_offset,
                start_joints=motion_group_joint_positions,
                global_limits=global_limits,
                motion_commands=[configuration.motion_command],
            )
        except RuntimeError:
            nm.post_notification(
                "Failed to plan motion to ghost object. See log for details",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return
        carb.log_info(f"Planned trajectory: {len(trajectory.joint_positions)} steps")

        if configuration.auto_play_simulation:
            import omni.timeline

            timeline: omni.timeline.Timeline = omni.timeline.get_timeline_interface()
            if not timeline.is_playing():
                timeline.play()
            while not timeline.is_playing():
                await asyncio.sleep(0.1)

        try:
            # Handlers for certain motion state changes
            def continue_fn(weak_self: Callable[[], MoveToButton] = weakref.ref(self)):
                self_instance = weak_self()
                return self_instance is not None and self_instance._move_to_pressed

            def standstill_fn(
                standstill: bool,
                weak_self: Callable[[], MoveToButton] = weakref.ref(self),
            ):
                carb.log_verbose(f"Standstill state changed: {standstill}")
                self_instance = weak_self()
                if self_instance is None:
                    return
                if standstill:
                    self_instance._stop_move_to()

            def motion_start_fn(
                weak_self: Callable[[], MoveToButton] = weakref.ref(self),
            ):
                carb.log_info("Motion started")
                self_instance = weak_self()
                if self_instance is None:
                    return
                self_instance._set_move_to_state(MoveToState.EXECUTE)
                self_instance._standstill_subscription = (
                    planner_utils.subscribe_motion_group_standstill_state(
                        configuration.motion_stream_configuration,
                        standstill_changed_fn=standstill_fn,
                        standstill_init_fn=lambda standstill: carb.log_verbose(
                            f"Initial standstill state: {standstill}",
                        ),
                    )
                )

            if len(trajectory.locations) <= 2:
                carb.log_info("No motion needed, already at target")
                return

            await planner_utils.play_trajectory(
                configuration.motion_stream_configuration,
                trajectory,
                configuration.tcp,
                continue_fn=continue_fn,
                motion_start_fn=motion_start_fn,
            )
        except RuntimeError as e:
            nm.post_notification(
                f"Failed to execute planned motion\n{str(e)}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            carb.log_warn(str(e))
        except Exception as e:
            carb.log_error(
                f"Unexpected error during motion execution: {str(e)}",
            )
