from typing import Callable
import asyncio
from wandelbots.omni.utils.api import get_api_client_from_config
import wandelbots_api_client.v2 as wb
import wandelbots_api_client.v2.models as wb_models
import websockets
import json
from attr import dataclass
import omni.kit.notification_manager as nm
from pxr import Usd
import omni.usd
import carb
from wandelbots.omni.manipulators import (
    get_motion_group_configuration_from_prim,
    MotionStreamConfiguration,
)
from wandelbots.omni.utils.teaching import GhostObject, GhostObjectUtils
from wandelbots.omni.visualization import (
    get_trajectory_builder,
    TrajectoryBuilder,
)
from wandelbots.omni.visualization.models import TrajectoryData
from wandelbots.omni.core.networks.reconnecting_websocket import _to_header_params
from wandelbots.omni.utils.api import get_base_headers
from wandelbots.omni.datatypes import WSPose, JointPositions


@dataclass
class PlanAction:
    ghost_object: GhostObject
    trajectory: wb_models.JointTrajectory | None = None


MotionCommand = (
    wb_models.PathCartesianPTP
    | wb_models.PathCircle
    | wb_models.PathCubicSpline
    | wb_models.PathJointPTP
    | wb_models.PathLine
)


def _get_websocket_kwargs(access_token: str | None = None) -> dict:
    return _to_header_params(get_base_headers(access_token))


async def _resolve_joints_for_pose(
    kinematics_api: wb.KinematicsApi,
    cell: str,
    nova_pose: wb_models.Pose,
    ik_common: dict,
    preferred: list[float] | None,
) -> list[float] | None:
    response = await kinematics_api.inverse_kinematics(
        cell=cell,
        inverse_kinematics_request=wb_models.InverseKinematicsRequest(
            tcp_poses=[nova_pose],
            reference_joint_position=preferred,
            **ik_common,
        ),
    )
    if not response.joints or not response.joints[0]:
        return None
    return response.joints[0][0]


def _handle_plan_failure(action_name: str, data: dict):
    detail = data.get("detail")
    if not detail:
        carb.log_warn(f"{action_name} Planning failed: {data}")
        return
    planning_failed = detail[0].get("data", {})
    if "collisions" in planning_failed:
        for collision in [
            wb_models.Collision.from_dict(x) for x in planning_failed["collisions"]
        ]:
            carb.log_warn(
                f" - Collision with {collision.id_of_a} and {collision.id_of_b}"
            )
        carb.log_warn(f" - Joint position: {planning_failed['joint_position']}")
        nm.post_notification(
            text=f"[{action_name}] Collision at [{planning_failed['joint_position']}]",
            duration=5.0,
            status=nm.NotificationStatus.WARNING,
        )
    else:
        carb.log_warn(f"{action_name} Planning failed: {planning_failed}")


async def plan_path(
    motion_group_prim: Usd.Prim,
    tcp_name: str,
    collision_setup_name: str,
    plan_actions: list[PlanAction],
    planning_progress_fn: Callable[[float], None],
):
    carb.log_info("Planning path...")

    if not plan_actions:
        carb.log_warn("No actions to plan.")
        return

    motion_group = get_motion_group_configuration_from_prim(motion_group_prim)
    stream_config = motion_group.motion_stream_configuration

    async with get_api_client_from_config(stream_config.get_api_configuration()) as api:
        # 1. Fetch motion group description (model, TCP map, joint limits, operation limits).
        motion_group_description: wb_models.MotionGroupDescription = (
            await wb.MotionGroupApi(api).get_motion_group_description(
                cell=stream_config.cell,
                controller=stream_config.controller,
                motion_group=stream_config.motion_group,
            )
        )

        # 2. Resolve TCP offset
        tcp_offset_pose = (
            motion_group_description.tcps[tcp_name].pose
            if tcp_name in motion_group_description.tcps
            else None
        )
        if tcp_offset_pose is None:
            carb.log_warn(
                f"Selected TCP '{tcp_name}' not found. Available TCPs: {list(motion_group_description.tcps.keys())}"
            )
            nm.post_notification(
                text=f"Selected TCP '{tcp_name}' not found. Please select a valid TCP.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        collision_setup = await wb.StoreCollisionSetupsApi(
            api
        ).get_stored_collision_setup(
            cell=stream_config.cell, setup=collision_setup_name
        )

        # 3. Build MotionGroupSetup once outside the loop — TCP, collision setup and limits are shared by every segment.
        motion_group_setup = wb_models.MotionGroupSetup(
            motion_group_model=motion_group_description.motion_group_model,
            tcp_offset=tcp_offset_pose,
            collision_setups={collision_setup_name: collision_setup},
            cycle_time=8,
            global_limits=motion_group_description.operation_limits.auto_limits,
        )
        motion_group_setup.global_limits.tcp.velocity = 200
        motion_group_setup.global_limits.tcp.acceleration = 1000

        # 4. Collect shared IK kwargs once — same model, TCP and limits for every IK call.
        ik_common = dict(
            motion_group_model=motion_group_setup.motion_group_model,
            tcp_offset=motion_group_setup.tcp_offset,
            mounting=motion_group_setup.mounting,
            collision_setups=motion_group_setup.collision_setups,
            joint_position_limits=[
                limit.position for limit in motion_group_setup.global_limits.joints
            ],
        )

        planning_api = wb.TrajectoryPlanningApi(api)
        kinematics_api = wb.KinematicsApi(api)
        trajectory_builder: TrajectoryBuilder = get_trajectory_builder()
        stage = omni.usd.get_context().get_stage()

        for action_idx in range(len(plan_actions) - 1):
            action_name = f"[{action_idx}->{action_idx + 1}]"

            start_action = plan_actions[action_idx]
            target_action = plan_actions[action_idx + 1]

            # 5. Read preferred joint configs stored on each ghost prim by the overlay.
            start_preferred = GhostObjectUtils.get_preferred_joint_values(
                stage.GetPrimAtPath(start_action.ghost_object.prim_path)
            )
            target_preferred = GhostObjectUtils.get_preferred_joint_values(
                stage.GetPrimAtPath(target_action.ghost_object.prim_path)
            )

            # 6. IK with reference biases toward the preferred config; returns closest valid solution if the ghost moved.
            start_joints = await _resolve_joints_for_pose(
                kinematics_api,
                stream_config.cell,
                start_action.ghost_object.pose.to_nova_pose(),
                ik_common,
                start_preferred,
            )
            if start_joints is None:
                message = f"{action_name} Could not find joint solution for start pose"
                carb.log_warn(message)
                nm.post_notification(
                    text=message, duration=5.0, status=nm.NotificationStatus.WARNING
                )
                continue

            target_joints = await _resolve_joints_for_pose(
                kinematics_api,
                stream_config.cell,
                target_action.ghost_object.pose.to_nova_pose(),
                ik_common,
                target_preferred,
            )
            if target_joints is None:
                message = f"{action_name} Could not find joint solution for target pose"
                carb.log_warn(message)
                nm.post_notification(
                    text=message, duration=5.0, status=nm.NotificationStatus.WARNING
                )
                continue

            carb.log_info(
                f"{action_name} Planning from {start_joints} to {target_joints}"
            )

            # 7. RRTConnect: find a collision-free joint-space path between the two resolved configs.
            planning_response_raw = (
                await planning_api.plan_collision_free_without_preload_content(
                    cell=stream_config.cell,
                    plan_collision_free_request=wb_models.PlanCollisionFreeRequest(
                        start_joint_position=start_joints,
                        target=target_joints,
                        motion_group_setup=motion_group_setup,
                        algorithm=wb_models.CollisionFreeAlgorithm(
                            wb_models.RRTConnectAlgorithm(
                                algorithm_name="RRTConnectAlgorithm"
                            )
                        ),
                    ),
                )
            )
            data = await planning_response_raw.json()

            if planning_response_raw.status != 200:
                _handle_plan_failure(action_name, data)
                continue

            joint_trajectory = wb_models.JointTrajectory.from_dict(data["response"])
            target_action.trajectory = joint_trajectory

            # 8. FK on the planned trajectory to compute TCP poses for viewport preview.
            poses_response = await kinematics_api.forward_kinematics(
                cell=stream_config.cell,
                forward_kinematics_request=wb_models.ForwardKinematicsRequest(
                    motion_group_model=motion_group_description.motion_group_model,
                    joint_positions=joint_trajectory.joint_positions,
                    tcp_offset=motion_group_setup.tcp_offset,
                    mounting=motion_group_setup.mounting,
                ),
            )

            try:
                trajectory_name = f"planner_{action_idx}_{action_idx + 1}"
                if trajectory_name in [
                    x.name for x in trajectory_builder.list_trajectories()
                ]:
                    trajectory_builder.remove_trajectory(trajectory_name)

                trajectory_builder.create_trajectory(
                    TrajectoryData(
                        name=trajectory_name,
                        parent_prim_path=motion_group_prim.GetPath().pathString,
                        poses=[pose.position for pose in poses_response.tcp_poses],
                    )
                )
            except Exception as e:
                carb.log_warn(f"Could not create trajectory: {e}")
            if planning_progress_fn is not None:
                planning_progress_fn(float(action_idx + 1) / len(plan_actions))

    if planning_progress_fn is not None:
        planning_progress_fn(1.0)


async def get_motion_group_pose(
    motion_stream_config: MotionStreamConfiguration,
    tcp_offset: wb_models.TcpOffset,
) -> tuple[WSPose, JointPositions]:
    async with motion_stream_config.get_api_client() as api:
        motion_group_description = await wb.MotionGroupApi(
            api
        ).get_motion_group_description(
            cell=motion_stream_config.cell,
            controller=motion_stream_config.controller,
            motion_group=motion_stream_config.motion_group,
        )

        motion_group_state = await wb.MotionGroupApi(
            api
        ).get_current_motion_group_state(
            cell=motion_stream_config.cell,
            controller=motion_stream_config.controller,
            motion_group=motion_stream_config.motion_group,
        )

        motion_group_pose = (
            await wb.KinematicsApi(api).forward_kinematics(
                cell=motion_stream_config.cell,
                forward_kinematics_request=wb_models.ForwardKinematicsRequest(
                    motion_group_model=motion_group_description.motion_group_model,
                    joint_positions=[motion_group_state.joint_position],
                    tcp_offset=tcp_offset.pose,
                    mounting=motion_group_description.mounting,
                ),
            )
        ).tcp_poses[0]

        return WSPose(
            pose=[
                motion_group_pose.position[0],
                motion_group_pose.position[1],
                motion_group_pose.position[2],
                motion_group_pose.orientation[0],
                motion_group_pose.orientation[1],
                motion_group_pose.orientation[2],
            ]
        ), motion_group_state.joint_position


async def get_tcp_offset_by_name(
    motion_stream_config: MotionStreamConfiguration, tcp_name: str
) -> wb_models.TcpOffset:
    async with get_api_client_from_config(
        motion_stream_config.get_api_configuration()
    ) as api:
        motion_group_description = await wb.MotionGroupApi(
            api
        ).get_motion_group_description(
            cell=motion_stream_config.cell,
            controller=motion_stream_config.controller,
            motion_group=motion_stream_config.motion_group,
        )

        for tcp_id, tcp_offset in motion_group_description.tcps.items():
            if tcp_id == tcp_name:
                return tcp_offset
    return None


async def pose_to_joint_positions(
    motion_stream_configuration: MotionStreamConfiguration,
    tcp_offset: wb_models.TcpOffset,
    target_pose: WSPose,
) -> list[list[float | int]]:
    async with get_api_client_from_config(
        motion_stream_configuration.get_api_configuration()
    ) as api:
        motion_group_description: wb_models.MotionGroupDescription = (
            await wb.MotionGroupApi(api).get_motion_group_description(
                cell=motion_stream_configuration.cell,
                controller=motion_stream_configuration.controller,
                motion_group=motion_stream_configuration.motion_group,
            )
        )

        motion_group_setup = wb_models.MotionGroupSetup(
            motion_group_model=motion_group_description.motion_group_model,
            tcp_offset=tcp_offset.pose,
            cycle_time=8,
            global_limits=motion_group_description.operation_limits.auto_limits,
        )

        kinematics_api = wb.KinematicsApi(api)
        inverse_kinematic_request = wb_models.InverseKinematicsRequest(
            tcp_poses=[target_pose.to_nova_pose()],
            motion_group_model=motion_group_setup.motion_group_model,
            tcp_offset=motion_group_setup.tcp_offset,
            mounting=motion_group_setup.mounting,
            joint_position_limits=[
                limit.position for limit in motion_group_setup.global_limits.joints
            ],
        )
        return (
            await kinematics_api.inverse_kinematics(
                cell=motion_stream_configuration.cell,
                inverse_kinematics_request=inverse_kinematic_request,
            )
        ).joints


async def get_operation_limits(
    motion_stream_configuration: MotionStreamConfiguration,
) -> wb_models.OperationLimits:
    async with get_api_client_from_config(
        motion_stream_configuration.get_api_configuration()
    ) as api:
        motion_group_description: wb_models.MotionGroupDescription = (
            await wb.MotionGroupApi(api).get_motion_group_description(
                cell=motion_stream_configuration.cell,
                controller=motion_stream_configuration.controller,
                motion_group=motion_stream_configuration.motion_group,
            )
        )
        return motion_group_description.operation_limits


async def plan_motion_group_move_to(
    motion_stream_configuration: MotionStreamConfiguration,
    tcp_offset: wb_models.TcpOffset,
    start_joints: JointPositions,
    global_limits: wb_models.LimitSet,
    motion_commands: list[MotionCommand],
    cycle_time: int = 8,
) -> wb_models.JointTrajectory:
    carb.log_verbose("Planning path...")

    stream_config = motion_stream_configuration
    async with get_api_client_from_config(stream_config.get_api_configuration()) as api:
        motion_group_description: wb_models.MotionGroupDescription = (
            await wb.MotionGroupApi(api).get_motion_group_description(
                cell=stream_config.cell,
                controller=stream_config.controller,
                motion_group=stream_config.motion_group,
            )
        )

        planning_api = wb.TrajectoryPlanningApi(api)
        motion_group_setup = wb_models.MotionGroupSetup(
            motion_group_model=motion_group_description.motion_group_model,
            tcp_offset=tcp_offset.pose,
            cycle_time=cycle_time,
            global_limits=global_limits,
        )
        carb.log_info(f"Planning from {start_joints} to ...")
        for motion_command in motion_commands:
            carb.log_info(f" - {motion_command}")

        planning_response_raw = (
            await planning_api.plan_trajectory_without_preload_content(
                cell=stream_config.cell,
                plan_trajectory_request=wb_models.PlanTrajectoryRequest(
                    start_joint_position=start_joints,
                    motion_commands=motion_commands,
                    motion_group_setup=motion_group_setup,
                ),
            )
        )
        data = await planning_response_raw.json()
        if "error_feedback" in data["response"]:
            carb.log_warn("Planning failed")
            carb.log_warn(data["response"]["error_feedback"])
            raise RuntimeError(data["response"]["error_feedback"])

        carb.log_info("Received planning response")
        if planning_response_raw.status != 200:
            carb.log_warn(f"Planning failed: {planning_response_raw.status} - {data}")
            raise RuntimeError("Planning failed, see log for more info.")
        return wb_models.JointTrajectory.from_dict(data["response"])


async def create_joint_p2p_command_from_pose(
    motion_stream_configuration: MotionStreamConfiguration,
    tcp: str,
    target_pose: WSPose,
) -> MotionCommand:
    tcp_offset = await get_tcp_offset_by_name(motion_stream_configuration, tcp)

    target_joint_positions = await pose_to_joint_positions(
        motion_stream_configuration,
        tcp_offset=tcp_offset,
        target_pose=target_pose,
    )

    if len(target_joint_positions[0]) == 0:
        carb.log_error(f"Could not find joint solution for target pose {target_pose}")
        return None

    return wb_models.MotionCommand(
        path=wb_models.MotionCommandPath(
            wb_models.PathJointPTP(target_joint_position=target_joint_positions[0][0])
        )
    )


async def play_trajectory(
    motion_group_stream_configuration: MotionStreamConfiguration,
    trajectory: wb_models.JointTrajectory,
    tcp_name: str,
    motion_start_fn: Callable[[], None],
    continue_fn: Callable[[], bool],
    force_motion_group_state: bool = False,
):
    carb.log_info("Playing trajectory...")
    if trajectory is None:
        carb.log_warn("No trajectory to play.")
        return
    api_client_config = motion_group_stream_configuration.get_api_configuration()
    async with get_api_client_from_config(api_client_config) as api:
        controller_api = wb.ControllerApi(api)
        controller_state = await controller_api.get_current_robot_controller_state(
            cell=motion_group_stream_configuration.cell,
            controller=motion_group_stream_configuration.controller,
        )

        if (
            force_motion_group_state
            and controller_state.mode != wb_models.RobotSystemMode.MODE_MONITOR
        ):
            message = f"Controller is not in MODE_MONITOR mode. Current mode: {controller_state.mode}. Please switch to MONITOR mode or deactivate the motion group in robot pad."
            carb.log_warn(message)
            nm.post_notification(
                text=message,
                duration=10.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        robot_controller = await controller_api.get_robot_controller(
            cell=motion_group_stream_configuration.cell,
            controller=motion_group_stream_configuration.controller,
        )

        if not isinstance(
            robot_controller.configuration.actual_instance, wb_models.VirtualController
        ):
            message = "Due to initial position handling, only virtual controllers are currently supported."
            carb.log_warn(message)
            nm.post_notification(
                text=message,
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        if force_motion_group_state:
            response = await wb.VirtualControllerApi(api).set_motion_group_state(
                cell=motion_group_stream_configuration.cell,
                controller=motion_group_stream_configuration.controller,
                motion_group=motion_group_stream_configuration.motion_group,
                motion_group_joints=wb_models.MotionGroupJoints(
                    positions=trajectory.joint_positions[0]
                ),
            )
    message_queue = [
        wb_models.InitializeMovementRequest(
            message_type="InitializeMovementRequest",
            trajectory=wb_models.InitializeMovementRequestTrajectory(
                wb_models.TrajectoryData(
                    message_type="TrajectoryData",
                    data=trajectory,
                    motion_group=motion_group_stream_configuration.motion_group,
                    tcp=tcp_name,
                )
            ),
        ).to_json(),
        wb_models.StartMovementRequest().to_json(),
    ]

    async with websockets.connect(
        f"{api_client_config.base_url_websocket}/cells/{motion_group_stream_configuration.cell}/controllers/{motion_group_stream_configuration.controller}/execution/trajectory",
        **_get_websocket_kwargs(api_client_config.access_token),
    ) as websocket:
        while continue_fn():
            if len(message_queue) == 0:
                await asyncio.sleep(1)
                continue
            await websocket.send(message_queue.pop(0))
            response: dict = json.loads(await websocket.recv())
            carb.log_verbose(f"Received: {response}")
            if (
                "message" in response["result"]
                and len(response["result"]["message"]) > 0
            ):
                raise RuntimeError(response["result"]["message"])
            if response["result"]["kind"] == "START_RECEIVED":
                motion_start_fn()


@dataclass
class MotionGroupStandstillSubscription:
    task: asyncio.Task

    def __del__(self):
        carb.log_verbose("Cancelling motion state watcher task...")
        self.task.cancel()
        self.task = None


def subscribe_motion_group_standstill_state(
    motion_stream_configuration: MotionStreamConfiguration,
    standstill_changed_fn: Callable[[bool], None],
    standstill_init_fn: Callable[[bool], None],
):
    async def watch_motion_state():
        carb.log_verbose("Starting motion state watcher...")
        standstill = None
        stream_config = motion_stream_configuration
        api_client_config = stream_config.get_api_configuration()

        async with websockets.connect(
            f"{api_client_config.base_url_websocket}/cells/{stream_config.cell}/controllers/{stream_config.controller}/motion-groups/{stream_config.motion_group}/state-stream",
            **_get_websocket_kwargs(api_client_config.access_token),
        ) as websocket:
            while True:
                state = wb_models.MotionGroupState.from_dict(
                    json.loads(await websocket.recv())["result"]
                )
                if standstill is None:
                    standstill_init_fn(state.standstill)
                    standstill = state.standstill
                elif standstill != state.standstill:
                    standstill_changed_fn(state.standstill)
                    standstill = state.standstill

    task = asyncio.get_event_loop().create_task(watch_motion_state())
    return MotionGroupStandstillSubscription(task=task)
