import asyncio
import carb
import enum
from typing import Callable, Optional
import wandelbots.omni.ui.tool.action_planner.utils as planner_utils
import omni.kit.notification_manager as nm
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


async def execute_move_to(
    configuration: MoveToExecuteSettings,
    continue_fn: Optional[Callable[[], bool]] = None,
    on_state_change: Optional[Callable[[MoveToState], None]] = None,
    on_motion_start: Optional[Callable[[], None]] = None,
    on_stopped: Optional[Callable[[], None]] = None,
    stop_on_standstill: bool = True,
) -> bool:
    """
    Execute a move to a target position.

    Args:
        configuration: Settings for the move execution
        continue_fn: Optional callback to check if execution should continue (defaults to always True)
        on_state_change: Optional callback when state changes (receives MoveToState enum)
        on_motion_start: Optional callback when motion starts
        on_stopped: Optional callback when motion is stopped/cancelled
        stop_on_standstill: If True, automatically stop when robot reaches standstill (default: True)

    Returns:
        True if execution completed successfully, False otherwise
    """
    # Check if we should continue before starting
    if not continue_fn():
        carb.log_info("Move to execution cancelled before starting")
        if on_stopped:
            on_stopped()
        return False

    if on_state_change:
        on_state_change(MoveToState.CONFIGURATION)

    if configuration is None:
        carb.log_error("No configuration provided for planning")
        return False

    tcp_offset = await planner_utils.get_tcp_offset_by_name(
        configuration.motion_stream_configuration, configuration.tcp
    )
    carb.log_info(f"Planning with tcp offset: {tcp_offset}")
    if tcp_offset is None:
        nm.post_notification(
            f"{configuration.tcp} TCP not found in {configuration.motion_stream_configuration.motion_group}",
        )
        return False

    # Check if we should continue after getting TCP offset
    if not continue_fn():
        carb.log_info("Move to execution cancelled after TCP offset lookup")
        if on_stopped:
            on_stopped()
        return False

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

    # Check if we should continue before planning
    if not continue_fn():
        carb.log_info("Move to execution cancelled before planning")
        if on_stopped:
            on_stopped()
        return False

    if on_state_change:
        on_state_change(MoveToState.PLAN)

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
        return False

    carb.log_info(f"Planned trajectory: {len(trajectory.joint_positions)} steps")

    # Check if we should continue after planning
    if not continue_fn():
        carb.log_info("Move to execution cancelled after planning")
        if on_stopped:
            on_stopped()
        return False

    if configuration.auto_play_simulation:
        import omni.timeline

        timeline: omni.timeline.Timeline = omni.timeline.get_timeline_interface()
        if not timeline.is_playing():
            timeline.play()
        while not timeline.is_playing():
            await asyncio.sleep(0.1)

    try:
        # Handlers for certain motion state changes
        should_stop = False  # Flag to track if we should stop due to standstill

        def motion_start_callback():
            carb.log_info("Motion started")
            if on_state_change:
                on_state_change(MoveToState.EXECUTE)
            if on_motion_start:
                on_motion_start()

        standstill_subscription = None

        def standstill_callback(standstill: bool):
            nonlocal should_stop
            carb.log_verbose(f"Standstill state changed: {standstill}")
            if standstill and stop_on_standstill:
                carb.log_info("Standstill detected, stopping motion")
                should_stop = True

        def motion_continue_fn():
            if should_stop:
                return False
            return continue_fn()

        def motion_start_with_subscription():
            nonlocal standstill_subscription
            motion_start_callback()
            if stop_on_standstill:
                standstill_subscription = (
                    planner_utils.subscribe_motion_group_standstill_state(
                        configuration.motion_stream_configuration,
                        standstill_changed_fn=standstill_callback,
                        standstill_init_fn=lambda standstill: carb.log_verbose(
                            f"Initial standstill state: {standstill}",
                        ),
                    )
                )

        if len(trajectory.locations) <= 2:
            carb.log_info("Already at target")
            return True

        await planner_utils.play_trajectory(
            configuration.motion_stream_configuration,
            trajectory,
            configuration.tcp,
            continue_fn=motion_continue_fn,
            motion_start_fn=motion_start_with_subscription,
        )

        # Clean up standstill subscription after trajectory completes
        if standstill_subscription is not None:
            standstill_subscription = None

        # Check if we stopped due to standstill
        if should_stop:
            carb.log_info("Motion stopped due to standstill")
            if on_stopped:
                on_stopped()
            return True

        carb.log_info("Move to target completed successfully")
        return True

    except asyncio.CancelledError:
        carb.log_info("Move to target was cancelled")
        if on_stopped:
            on_stopped()
        raise

    except RuntimeError as e:
        nm.post_notification(
            f"Failed to execute planned motion\n{str(e)}",
            duration=5.0,
            status=nm.NotificationStatus.WARNING,
        )
        carb.log_warn(str(e))
        return False

    except Exception as e:
        carb.log_error(
            f"Unexpected error during motion execution: {str(e)}",
        )
        return False
