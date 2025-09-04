import asyncio

import numpy as np

import nova
from nova import Nova
from nova.actions import Action
from nova.actions.motions import cartesian_ptp
from nova.cell import virtual_controller
from nova.program import ProgramPreconditions
from nova.types import Pose
import wandelbots_isaacsim_api.trajectory as trajectory_utils
import wandelbots_api_client as nova_api
from nova.types.motion_settings import MotionSettings
import wandelbots_isaacsim_api as isaac_sim_api


@nova.program(
    name="viewer_example",
    preconditions=ProgramPreconditions(
        controllers=[
            virtual_controller(
                name="ur10e",
                manufacturer=nova_api.models.Manufacturer.UNIVERSALROBOTS,
                type=nova_api.models.VirtualControllerTypes.UNIVERSALROBOTS_MINUS_UR10E,
            )
        ],
        cleanup_controllers=False,
    ),
    viewer=trajectory_utils.TrajectoryViewer(
        trajectory_success=trajectory_utils.TrajectoryPlanResultConfiguration(
            name="PlannedTrajectory",
            options=isaac_sim_api.models.TrajectoryOptions(color=[0, 255, 0], width=20),
            optimization=trajectory_utils.TrajectoryDrawOptimization(
                min_time_delta_seconds=0.1,
                min_pose_distance_millimeters=10.0,
            ),
        ),
        trajectory_failure=trajectory_utils.TrajectoryPlanResultConfiguration(
            name="PlanningFailed",
            options=isaac_sim_api.models.TrajectoryOptions(color=[255, 0, 0], width=20),
            optimization=trajectory_utils.TrajectoryDrawOptimization(
                min_time_delta_seconds=0.1,
                min_pose_distance_millimeters=10.0,
            ),
        ),
    ),
)
async def test() -> None:
    """
    Example program to demonstrate the use of the Omniverse viewer with a UR10e controller.
    """

    async with Nova() as nova:
        cell = nova.cell()
        controller = await cell.controller("ur10e")

        # Connect to the controller and activate motion groups
        async with controller[0] as motion_group:
            tcp = "Flange"

            # Pick some poses
            pose_0 = Pose((500, -900, 700, -np.pi, -0, 0))
            pose_1 = Pose((-400, 500, 150, np.pi, 0, 0))
            pose_2 = Pose((550, 500, 150, -np.pi, 0, 0))
            pose_3 = Pose((-500, -670, 220, -np.pi, 0, 0))

            poses_ordered = [pose_2, pose_1, pose_3, pose_0]

            actions: list[Action] = [
                cartesian_ptp(
                    target=pose, settings=MotionSettings(tcp_velocity_limit=2000)
                )
                for pose in poses_ordered
            ]

            joint_trajectory = await motion_group.plan(actions, tcp)

            motion_state_iter = motion_group.stream_execute(
                joint_trajectory,
                actions=actions,
                tcp=tcp,
            )

            print("Executing trajectory...")
            async for _ in motion_state_iter:
                pass
            print("Trajectory execution finished.")


if __name__ == "__main__":
    asyncio.run(test())
