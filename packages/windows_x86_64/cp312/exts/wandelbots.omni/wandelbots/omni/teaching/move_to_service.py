import asyncio
import carb
import enum
import numpy as np
import time
from typing import Callable, Optional
import omni.kit.app
import omni.timeline
import wandelbots.omni.ui.tool.planner_utils as planner_utils
import wandelbots_api_client.v2.models as wb_models
import omni.kit.notification_manager as nm
from wandelbots.omni.manipulators import (
    MotionStreamConfiguration,
    get_motion_group_service,
)
from wandelbots.omni.ui.tool.trajectory_planner.service.execution_service import (
    ExecutionLifecycle,
    ExecutionService,
)
from wandelbots.omni.utils.math import rotvec_angle_between
from dataclasses import dataclass


class MoveToState(enum.Enum):
    IDLE = 0
    CONFIGURATION = 1
    PLAN = 2
    EXECUTE = 3


# A joint PTP that landed is well inside this; the residual is reported either
# way, so the threshold only decides whether the move counts as "arrived".
JOINT_REACHED_TOLERANCE_RAD = 5e-3

# The same question for a Cartesian or line move, which has no joint target to
# check: how close the TCP has to be to count as arrived. NOVA works in
# millimetres, so these are millimetres and radians of the tool frame.
TCP_REACHED_TOLERANCE_MM = 1.0
TCP_REACHED_TOLERANCE_RAD = 5e-3


@dataclass
class MoveToDiagnostics:
    """What actually happened during a move - beyond the bool return.

    ``execute_move_to`` returning True used to cover three very different
    outcomes: the robot moved and arrived, the planner produced a degenerate
    trajectory that was assumed to mean "already there", and the robot moved in
    NOVA while the articulation in the scene never followed because no motion
    stream was live. Callers that report to a user need to tell those apart, so
    every step records what it observed here.
    """

    # None = not checked (auto_play_simulation off, or the move never got there).
    stream_live: bool | None = None
    # The scene has a robot set up to follow this motion group at all.
    streamable: bool = False
    already_at_target: bool = False
    planned_steps: int = 0
    target_joint_position: list[float] | None = None
    start_joint_position: list[float] | None = None
    final_joint_position: list[float] | None = None
    # Largest per-axis difference between target and final state, in rad.
    max_joint_residual: float | None = None
    # Cartesian and line commands have no joint target, so they are verified
    # against the TCP pose instead: distance in mm and angle in rad.
    target_tcp_pose: list[float] | None = None
    tcp_position_residual_mm: float | None = None
    tcp_orientation_residual_rad: float | None = None
    # None = could not be verified (no joint target, or the read-back failed).
    reached: bool | None = None
    failure: str = ""

    @property
    def scene_followed(self) -> bool:
        """Whether the robot IN THE SCENE can have followed this move."""
        return bool(self.stream_live) or not self.streamable


def _target_joint_position(motion_command) -> list[float] | None:
    """Joint target of a joint-PTP command, or None for any other command.

    Only a joint PTP has a target the final state can be verified against; a
    Cartesian path is verified by whoever knows the pose, not here.
    """
    path = getattr(motion_command, "path", None)
    # Generated oneOf wrappers carry the real path in actual_instance; anything
    # that already IS the path (or a wrapper with none set) is used as-is.
    path = getattr(path, "actual_instance", None) or path
    target = getattr(path, "target_joint_position", None)
    # An empty target is not a target: nothing could be verified against it.
    return list(target) if target else None


def _target_tcp_pose(motion_command) -> list[float] | None:
    """Target TCP pose of a Cartesian or line command, as [x, y, z, rx, ry, rz].

    None for a joint PTP, which is verified against its joint target instead.
    """
    path = getattr(motion_command, "path", None)
    path = getattr(path, "actual_instance", None) or path
    pose = getattr(path, "target_pose", None)
    pose = getattr(pose, "actual_instance", None) or pose
    position = getattr(pose, "position", None)
    orientation = getattr(pose, "orientation", None)
    if not position or orientation is None:
        return None
    return [float(v) for v in position] + [float(v) for v in orientation]


def _tcp_residuals(target: list[float] | None, actual) -> tuple[float, float] | None:
    """(distance in mm, angle in rad) between a target TCP pose and the actual
    one, or None when either is missing."""
    actual_pose = getattr(actual, "pose", None)
    if not target or not actual_pose or len(target) != 6 or len(actual_pose) != 6:
        return None
    distance = float(
        np.linalg.norm(
            np.asarray(target[:3], dtype=np.float64)
            - np.asarray(actual_pose[:3], dtype=np.float64)
        )
    )
    return distance, rotvec_angle_between(target[3:], list(actual_pose[3:]))


def _tcp_reached(residuals: tuple[float, float] | None) -> bool | None:
    """Whether a TCP residual counts as arrived. None = nothing to judge."""
    if residuals is None:
        return None
    distance_mm, angle_rad = residuals
    return (
        distance_mm <= TCP_REACHED_TOLERANCE_MM
        and angle_rad <= TCP_REACHED_TOLERANCE_RAD
    )


def _describe_gap(
    joint_residual: float | None, tcp_residuals: tuple[float, float] | None
) -> str:
    """How far the robot is from the target, in whatever unit could be read."""
    if joint_residual is not None:
        return f"{joint_residual:.4f} rad in joint space"
    if tcp_residuals is not None:
        distance_mm, angle_rad = tcp_residuals
        return f"{distance_mm:.2f} mm and {angle_rad:.4f} rad at the TCP"
    return "an unknown distance"


def _max_joint_residual(
    target: list[float] | None, actual: list[float] | None
) -> float | None:
    if not target or not actual or len(target) != len(actual):
        return None
    return max(abs(float(a) - float(b)) for a, b in zip(target, actual))


@dataclass
class MoveToExecuteSettings:
    motion_stream_configuration: MotionStreamConfiguration
    tcp: str
    motion_command: planner_utils.MotionCommand
    auto_play_simulation: bool = True
    velocity: float = 500
    acceleration: float = 1000
    # Override for the motion group's mounting (world->base offset/orientation).
    # None = use whatever the backend's own description reports.
    mounting_override: wb_models.Pose | None = None


_execution_service = ExecutionService()

# Guard for the play hand-off: without a stage (or with playback blocked) the
# timeline never reports playing, and the move must not hang on that.
_PLAY_TIMEOUT_S = 5.0


async def ensure_simulation_streaming(
    stream_configuration: MotionStreamConfiguration,
) -> bool:
    """Play the simulation and wait until the robot's motion stream is live.

    NOVA owns the robot's state; the articulation in the scene only follows it
    through the motion stream, which is built asynchronously on the timeline
    PLAY event. Anything written to the controller before the stream carries
    joint state leaves the robot in the scene standing still (or, with an
    external joint stream, waiting for joints nobody sends), so callers wait
    for it here. Returns whether the stream is live.
    """
    timeline: omni.timeline.Timeline = omni.timeline.get_timeline_interface()
    if not timeline.is_playing():
        timeline.play()
    app = omni.kit.app.get_app()
    play_deadline = time.monotonic() + _PLAY_TIMEOUT_S
    while not timeline.is_playing():
        if time.monotonic() >= play_deadline:
            carb.log_warn("Timeline did not start playing - executing anyway.")
            return False
        await app.next_update_async()
    # One frame for the PLAY handlers: the stream start pass is kicked off from
    # that event, so checking before it ran would race it (and PhysX needs a
    # step to build the view the articulation is driven through anyway).
    await app.next_update_async()

    service = get_motion_group_service()
    if service is None:
        return False
    if await service.ensure_stream_live(stream_configuration):
        return True

    if not service.has_streamable_motion_group(stream_configuration):
        # Caller-visible: nothing in the scene follows this motion group, so a
        # missing stream is expected rather than a defect.
        # Real controller, or a robot not enabled for simulation: there is no
        # stream to miss, so this is not worth reporting.
        return False
    carb.log_warn(
        f"No live motion stream for {stream_configuration.cell}/"
        f"{stream_configuration.controller}/{stream_configuration.motion_group} - "
        f"the robot in the scene may not follow the controller."
    )
    nm.post_notification(
        "Robot stream not connected - the controller has the new state, "
        "the robot in the scene may not follow.",
        duration=5.0,
        status=nm.NotificationStatus.WARNING,
    )
    return False


async def execute_move_to(
    configuration: MoveToExecuteSettings,
    stop_event: asyncio.Event | None = None,
    on_state_change: Optional[Callable[[MoveToState], None]] = None,
    on_motion_start: Optional[Callable[[], None]] = None,
    on_stopped: Optional[Callable[[], None]] = None,
    diagnostics: MoveToDiagnostics | None = None,
) -> bool:
    """Execute a move to a target position.

    Args:
        configuration: Settings for the move execution
        stop_event: Event that, when set, aborts execution
        on_state_change: Optional callback when state changes (receives MoveToState enum)
        on_motion_start: Optional callback when motion starts
        on_stopped: Optional callback when motion is stopped/cancelled
        diagnostics: Optional record filled in as the move progresses - pass one
            to find out why a move failed, and whether the scene followed

    Returns False when nothing went wrong on the way but the robot verifiably
    stopped short of the target. When the final state cannot be read back at
    all the move counts as successful: an unanswerable check is not evidence
    of a shortfall.
    """
    diag = diagnostics if diagnostics is not None else MoveToDiagnostics()
    diag.target_joint_position = _target_joint_position(configuration.motion_command)
    diag.target_tcp_pose = _target_tcp_pose(configuration.motion_command)
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
        diag.failure = (
            f"TCP '{configuration.tcp}' is not defined on "
            f"{configuration.motion_stream_configuration.motion_group}"
        )
        nm.post_notification(diag.failure)
        return False

    if stop_event and stop_event.is_set():
        carb.log_info("Move to execution cancelled after TCP offset lookup")
        if on_stopped:
            on_stopped()
        return False

    (
        start_tcp_pose,
        motion_group_joint_positions,
    ) = await planner_utils.get_motion_group_pose(
        configuration.motion_stream_configuration, tcp_offset
    )
    diag.start_joint_position = list(motion_group_joint_positions)
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
            mounting_override=configuration.mounting_override,
        )
    except RuntimeError as plan_error:
        diag.failure = f"Planning failed: {plan_error}"
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

    diag.planned_steps = len(trajectory.joint_positions or [])

    # A two-location trajectory is what the planner returns when start and
    # target coincide - but it is ALSO what it returns for a target it could not
    # path to. Treating both as "already at target" reported success while the
    # robot never moved, which is exactly the surprise this guard now removes:
    # the claim is only made when the current joint state really is the target.
    if len(trajectory.locations) <= 2:
        residual = _max_joint_residual(
            diag.target_joint_position, motion_group_joint_positions
        )
        # A Cartesian or line command has no joint target, so the same question
        # is asked of the TCP pose. Without either check there is no evidence
        # the robot is at the target, and the degenerate plan is a planning
        # failure rather than a no-op.
        tcp_residuals = _tcp_residuals(diag.target_tcp_pose, start_tcp_pose)
        diag.tcp_position_residual_mm, diag.tcp_orientation_residual_rad = (
            tcp_residuals if tcp_residuals else (None, None)
        )
        at_joint_target = (
            residual is not None and residual <= JOINT_REACHED_TOLERANCE_RAD
        )
        if at_joint_target or _tcp_reached(tcp_residuals):
            diag.already_at_target = True
            diag.final_joint_position = list(motion_group_joint_positions)
            diag.max_joint_residual = residual
            diag.reached = True
            carb.log_info("Already at target - nothing to execute")
            return True
        diag.failure = (
            "The planner returned a degenerate trajectory while the robot is "
            f"{_describe_gap(residual, tcp_residuals)} from the target - no "
            "motion was executed"
        )
        carb.log_warn(diag.failure)
        nm.post_notification(
            "Could not plan a motion to this target - the robot did not move.",
            duration=5.0,
            status=nm.NotificationStatus.WARNING,
        )
        return False

    service = get_motion_group_service()
    if service is not None:
        diag.streamable = service.has_streamable_motion_group(
            configuration.motion_stream_configuration
        )
    if configuration.auto_play_simulation:
        diag.stream_live = await ensure_simulation_streaming(
            configuration.motion_stream_configuration
        )

    if stop_event and stop_event.is_set():
        # Waiting for the stream is the one step that can take seconds, so
        # re-check before committing to the motion.
        carb.log_info("Move to execution cancelled while waiting for the simulation")
        if on_stopped:
            on_stopped()
        return False

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

        # The execution call returning is not proof of arrival: it can be cut
        # short, and with no live stream the scene's robot never followed at
        # all. Read the state back so callers can say what really happened.
        try:
            final_tcp_pose, final_joints = await planner_utils.get_motion_group_pose(
                configuration.motion_stream_configuration, tcp_offset
            )
            diag.final_joint_position = list(final_joints)
            diag.max_joint_residual = _max_joint_residual(
                diag.target_joint_position, final_joints
            )
            tcp_residuals = _tcp_residuals(diag.target_tcp_pose, final_tcp_pose)
            diag.tcp_position_residual_mm, diag.tcp_orientation_residual_rad = (
                tcp_residuals if tcp_residuals else (None, None)
            )
            if diag.max_joint_residual is not None:
                diag.reached = diag.max_joint_residual <= JOINT_REACHED_TOLERANCE_RAD
            else:
                # Cartesian and line moves are judged at the TCP; None stays
                # None, which means the check could not answer.
                diag.reached = _tcp_reached(tcp_residuals)
            if diag.reached is False:
                diag.failure = (
                    "The robot stopped "
                    f"{_describe_gap(diag.max_joint_residual, tcp_residuals)} "
                    "short of the target"
                )

                carb.log_warn(diag.failure)
        except Exception as verify_error:
            # Verification is a read-back, never a reason to call the move failed.
            carb.log_warn(f"Could not verify the final joint state: {verify_error}")

        carb.log_info(
            "Move to target completed"
            + (
                f" (residual {diag.max_joint_residual:.4f} rad)"
                if diag.max_joint_residual is not None
                else ""
            )
        )
        # A verified shortfall is a failed move. None means the read-back could
        # not answer, which is not evidence of anything and stays a success.
        return diag.reached is not False

    except asyncio.CancelledError:
        carb.log_info("Move to target was cancelled")
        if on_stopped:
            on_stopped()
        raise

    except RuntimeError as e:
        diag.failure = f"Execution failed: {e}"
        nm.post_notification(
            f"Failed to execute planned motion\n{str(e)}",
            duration=5.0,
            status=nm.NotificationStatus.WARNING,
        )
        carb.log_warn(str(e))
        return False

    except Exception as e:
        diag.failure = f"Unexpected error during execution: {e}"
        carb.log_error(
            f"Unexpected error during motion execution: {str(e)}",
        )
        return False
