from typing import Callable
import asyncio
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
)
from wandelbots.omni.utils.auth import get_auth_token
from wandelbots.omni.utils.api import get_api_client_from_config
import wandelbots_api_client.v2 as wb
import wandelbots_api_client.v2.models as wb_models
import websockets
import json
from attr import dataclass
import omni.kit.notification_manager as nm
from pxr import Usd
import carb
from wandelbots.omni.manipulators import (
    get_motion_group_configuration_from_prim,
)
from wandelbots.omni.utils.teaching import GhostObject
from wandelbots.omni.visualization import (
    get_trajectory_builder,
    TrajectoryBuilder,
)
from wandelbots.omni.visualization.models import TrajectoryData
from wandelbots.omni.core.networks.reconnecting_websocket import _to_header_params
from wandelbots.omni.utils.api import get_base_headers


@dataclass
class PlanAction:
    ghost_object: GhostObject
    trajectory: wb_models.JointTrajectory | None = None


def _get_websocket_kwargs() -> dict:
    return _to_header_params(get_base_headers(get_auth_token()))


async def plan_path(
    motion_group_prim: Usd.Prim,
    tcp_name: str,
    collision_setup_name: str,
    plan_actions: list[PlanAction],
    planning_progress_fn: Callable[[float], None],
):
    carb.log_info("Planning path...")

    motion_group = get_motion_group_configuration_from_prim(motion_group_prim)

    stream_config = motion_group.motion_stream_configuration
    async with get_api_client_from_config(
        stream_config.get_api_configuration(get_auth_token(), version="v2")
    ) as api:
        tcps = await wb.VirtualControllerApi(api).list_virtual_controller_tcps(
            cell=stream_config.cell,
            controller=stream_config.controller,
            motion_group=stream_config.motion_group,
        )

        tcp: wb_models.RobotTcp = None
        for virtual_tcp in tcps:
            if virtual_tcp.id == tcp_name:
                tcp = virtual_tcp
                break

        motion_group_description: wb_models.MotionGroupDescription = (
            await wb.MotionGroupApi(api).get_motion_group_description(
                cell=stream_config.cell,
                controller=stream_config.controller,
                motion_group=stream_config.motion_group,
            )
        )

        planning_api = wb.TrajectoryPlanningApi(api)
        kinematics_api = wb.KinematicsApi(api)
        trajectory_builder: TrajectoryBuilder = get_trajectory_builder()
        collision_api = wb.StoreCollisionSetupsApi(api)
        collision_setup = await collision_api.get_stored_collision_setup(
            cell=stream_config.cell,
            setup=collision_setup_name,
        )
        collision_setups = {collision_setup_name: collision_setup}
        for action_idx in range(0, len(plan_actions) - 1):
            action_name = f"[{action_idx}->{action_idx + 1}]"
            motion_group_setup = wb_models.MotionGroupSetup(
                motion_group_model=motion_group_description.motion_group_model,
                tcp_offset=wb_models.Pose(
                    position=tcp.position, orientation=tcp.orientation
                ),
                collision_setups=collision_setups,
                cycle_time=8,
                global_limits=motion_group_description.operation_limits.auto_limits,
            )
            motion_group_setup.global_limits.tcp.velocity = 200
            motion_group_setup.global_limits.tcp.acceleration = 1000

            start_pose = plan_actions[action_idx].ghost_object.pose.to_nova_pose()
            target_pose = plan_actions[action_idx + 1].ghost_object.pose.to_nova_pose()

            inverse_kinematic_request = wb_models.InverseKinematicsRequest(
                tcp_poses=[
                    wb_models.Pose(
                        position=start_pose.position,
                        orientation=start_pose.orientation,
                    ),
                    wb_models.Pose(
                        position=target_pose.position,
                        orientation=target_pose.orientation,
                    ),
                ],
                motion_group_model=motion_group_setup.motion_group_model,
                tcp_offset=motion_group_setup.tcp_offset,
                mounting=motion_group_setup.mounting,
                collision_setups=collision_setups,
                joint_position_limits=[
                    limit.position for limit in motion_group_setup.global_limits.joints
                ],
            )
            joints_response = await kinematics_api.inverse_kinematics(
                cell=stream_config.cell,
                inverse_kinematics_request=inverse_kinematic_request,
            )

            if len(joints_response.joints[0]) == 0:
                message = f"{action_name} Could not find joint solution for start pose {start_pose}"
                carb.log_warn(message)
                nm.post_notification(
                    text=message,
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
                continue
            if len(joints_response.joints[1]) == 0:
                message = f"{action_name} Could not find joint solution for target pose {target_pose}"
                carb.log_warn(message)
                nm.post_notification(
                    text=message,
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
                continue
            start_joints = joints_response.joints[0][0]
            target_joints = joints_response.joints[1][0]
            carb.log_info(
                f"{action_name} Planning from {start_joints} to {target_joints}"
            )

            algorithm = wb_models.MidpointInsertionAlgorithm(
                algorithm_name="MidpointInsertionAlgorithm"
            )
            if True:
                algorithm = wb_models.RRTConnectAlgorithm(
                    algorithm_name="RRTConnectAlgorithm",
                )

            planning_response_raw = (
                await planning_api.plan_collision_free_without_preload_content(
                    cell=stream_config.cell,
                    plan_collision_free_request=wb_models.PlanCollisionFreeRequest(
                        start_joint_position=start_joints,
                        target=target_joints,
                        motion_group_setup=motion_group_setup,
                        algorithm=wb_models.CollisionFreeAlgorithm(algorithm),
                    ),
                )
            )
            data = await planning_response_raw.json()

            carb.log_info(f"{action_name} Received planning response")
            if planning_response_raw.status != 200:
                planning_failed = data["detail"][0]["data"]
                if "collisions" in planning_failed:
                    for collision in [
                        wb_models.Collision.from_dict(x)
                        for x in planning_failed["collisions"]
                    ]:
                        carb.log_warn(
                            f" - Collision with {collision.id_of_a} and {collision.id_of_b}"
                        )
                    carb.log_warn(
                        f" - Joint position: {planning_failed['joint_position']}"
                    )
                    nm.post_notification(
                        text=f"[{action_name}] Collision at [{planning_failed['joint_position']}]",
                        duration=5.0,
                        status=nm.NotificationStatus.WARNING,
                    )
                else:
                    carb.log_warn(f"{action_name} Planning failed: {planning_failed}")
                continue
            joint_trajectory = wb_models.JointTrajectory.from_dict(data["response"])
            plan_actions[action_idx + 1].trajectory = joint_trajectory

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


async def play_trajectory(
    motion_group_prim: Usd.Prim,
    action: PlanAction,
    tcp_name: str,
    motion_start_fn: Callable[[], None],
):
    carb.log_info("Playing trajectory...")
    if action.trajectory is None:
        carb.log_warn("No trajectory to play.")
        return

    motion_group = get_motion_group_configuration_from_prim(motion_group_prim)

    stream_config = motion_group.motion_stream_configuration
    api_client_config = stream_config.get_api_configuration(
        get_auth_token(), version="v2"
    )
    async with get_api_client_from_config(api_client_config) as api:
        controller_api = wb.ControllerApi(api)
        controller_state = await controller_api.get_current_robot_controller_state(
            cell=stream_config.cell, controller=stream_config.controller
        )

        if controller_state.mode != wb_models.RobotSystemMode.MODE_MONITOR:
            message = f"Controller is not in MODE_MONITOR mode. Current mode: {controller_state.mode}. Please switch to MONITOR mode or deactivate the motion group in robot pad."
            carb.log_warn(message)
            nm.post_notification(
                text=message,
                duration=10.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        robot_controller = await controller_api.get_robot_controller(
            cell=stream_config.cell, controller=stream_config.controller
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

        response = await wb.VirtualControllerApi(api).set_motion_group_state(
            cell=stream_config.cell,
            controller=stream_config.controller,
            motion_group=stream_config.motion_group,
            motion_group_joints=wb_models.MotionGroupJoints(
                positions=action.trajectory.joint_positions[0]
            ),
        )

    async with websockets.connect(
        f"{api_client_config.base_url_websocket}/cells/{stream_config.cell}/controllers/{stream_config.controller}/execution/trajectory",
        **_get_websocket_kwargs(),
    ) as websocket:
        await websocket.send(
            wb_models.InitializeMovementRequest(
                message_type="InitializeMovementRequest",
                trajectory=wb_models.InitializeMovementRequestTrajectory(
                    wb_models.TrajectoryData(
                        message_type="TrajectoryData",
                        data=action.trajectory,
                        motion_group=stream_config.motion_group,
                        tcp=tcp_name,
                    )
                ),
            ).to_json()
        )
        response = await websocket.recv()
        carb.log_verbose(f"Received: {response}")

        await websocket.send(wb_models.StartMovementRequest().to_json())
        while True:
            response: dict = json.loads(await websocket.recv())
            carb.log_verbose(f"Received: {response}")
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
    motion_group_configuration: MotionGroupConfiguration,
    robot_standstill_changed_fn: Callable[[bool], None],
):
    async def watch_motion_state():
        carb.log_verbose("Starting motion state watcher...")
        standstill = True
        stream_config = motion_group_configuration.motion_stream_configuration
        api_client_config = stream_config.get_api_configuration(
            get_auth_token(), version="v2"
        )

        async with websockets.connect(
            f"{api_client_config.base_url_websocket}/cells/{stream_config.cell}/controllers/{stream_config.controller}/motion-groups/{stream_config.motion_group}/state-stream",
            **_get_websocket_kwargs(),
        ) as websocket:
            while True:
                state = wb_models.MotionGroupState.from_dict(
                    json.loads(await websocket.recv())["result"]
                )
                if standstill != state.standstill:
                    robot_standstill_changed_fn(state.standstill)
                    standstill = state.standstill

    task = asyncio.get_event_loop().create_task(watch_motion_state())
    return MotionGroupStandstillSubscription(task=task)
