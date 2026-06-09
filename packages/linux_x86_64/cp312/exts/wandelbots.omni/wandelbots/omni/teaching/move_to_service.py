import asyncio
import carb
import enum
from typing import Callable, Optional
import wandelbots.omni.ui.tool.planner_utils as planner_utils
import omni.kit.notification_manager as nm
from wandelbots.omni.manipulators import MotionStreamConfiguration
from wandelbots.omni.ui.tool.trajectory_planner.service.execution_service import (
    ExecutionLifecycle,
    ExecutionService,
)
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


_execution_service = ExecutionService()


async def execute_move_to(
    configuration: MoveToExecuteSettings,
    stop_event: asyncio.Event | None = None,
    on_state_change: Optional[Callable[[MoveToState], None]] = None,
    on_motion_start: Optional[Callable[[], None]] = None,
    on_stopped: Optional[Callable[[], None]] = None,
) -> bool:
    """Execute a move to a target position.

    Args:
        configuration: Settings for the move execution
        stop_event: Event that, when set, aborts execution
        on_state_change: Optional callback when state changes (receives MoveToState enum)
        on_motion_start: Optional callback when motion starts
        on_stopped: Optional callback when motion is stopped/cancelled
    """
    if stop_event and stop_event.is_set():
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

    if stop_event and stop_event.is_set():
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

    if stop_event and stop_event.is_set():
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

    if stop_event and stop_event.is_set():
        carb.log_info("Move to execution cancelled after planning")
        if on_stopped:
            on_stopped()
        return False

    if len(trajectory.locations) <= 2:
        carb.log_info("Already at target")
        return True

    if configuration.auto_play_simulation:
        import omni.timeline

        timeline: omni.timeline.Timeline = omni.timeline.get_timeline_interface()
        if not timeline.is_playing():
            timeline.play()
        while not timeline.is_playing():
            await asyncio.sleep(0.1)

    try:
        if on_state_change:
            on_state_change(MoveToState.EXECUTE)
        if on_motion_start:
            on_motion_start()

        api_config = configuration.motion_stream_configuration.get_api_configuration()
        await _execution_service.execute_trajectory(
            api_configuration=api_config,
            cell=configuration.motion_stream_configuration.cell,
            controller=configuration.motion_stream_configuration.controller,
            motion_group=configuration.motion_stream_configuration.motion_group,
            joint_trajectory=trajectory,
            tcp_name=configuration.tcp,
            lifecycle=ExecutionLifecycle(stop_event=stop_event),
            patch_start=True,
        )

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
