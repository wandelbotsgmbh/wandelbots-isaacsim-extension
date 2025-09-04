import asyncio
import os

from dotenv import load_dotenv
import numpy as np
from wandelbots_api_client.models import (
    CoordinateSystem,
    RotationAngles,
    RotationAngleTypes,
    Vector3d,
)

import nova
from nova import Nova
from nova.actions import Action
from nova.actions.motions import collision_free
from nova.cell import virtual_controller
from nova.program import ProgramPreconditions
from nova.types import Pose
import wandelbots_isaacsim_api as isaac_sim_api
import wandelbots_isaacsim_api.collision.utils as collision_utils
import wandelbots_isaacsim_api.trajectory as trajectory_utils
import wandelbots_api_client as nova_api


async def build_collision_world(
    nova: Nova, cell_name: str, robot_setup: nova_api.models.OptimizerSetup
) -> str:
    collision_api = nova._api_client.store_collision_components_api
    scene_api = nova._api_client.store_collision_scenes_api

    scene_colliders: dict[str, nova_api.models.Collider] = dict()

    # Sweep all colliders around the robot (which is located at 0,0,0)
    async with isaac_sim_api.ApiClient(
        isaac_sim_api.Configuration(host=os.environ["ISAACSIM_API_URL"])
    ) as isaac_sim_api_client:
        scene_colliders = await collision_utils.sweep_colliders(
            isaac_sim_api_client,
            isaac_sim_api.models.SphereSweepParameters(
                sweep_type="sphere",
                radius=100,
                position=[0, 0, 0],
                direction=[0, 0, 1],
            ),
        )

        print(f"{len(scene_colliders.keys())} colliders found in Isaac Sim")

    # The api does not allow slashes in the collider names, so we replace them with underscores
    for collider_id, collider in scene_colliders.items():
        await collision_api.store_collider(
            cell=cell_name, collider=collider_id.replace("/", "_"), collider2=collider
        )

    # define TCP collider geometry
    # The colliders can be used from the sweep as well, but this needs some offset
    tool_collider = nova_api.models.Collider(
        shape=nova_api.models.ColliderShape(
            nova_api.models.Box2(
                size_x=100, size_y=100, size_z=100, shape_type="box", box_type="FULL"
            )
        )
    )
    await collision_api.store_collision_tool(
        cell=cell_name, tool="tool_box", request_body={"tool_collider": tool_collider}
    )

    # define robot link geometries
    robot_link_colliders = await collision_api.get_default_link_chain(
        cell=cell_name, motion_group_model=robot_setup.motion_group_type
    )
    await collision_api.store_collision_link_chain(
        cell=cell_name, link_chain="robot_links", collider=robot_link_colliders
    )

    # assemble scene
    scene = nova_api.models.CollisionScene(
        colliders=scene_colliders,
        motion_groups={
            robot_setup.motion_group_type: nova_api.models.CollisionMotionGroup(
                tool={"tool_geometry": tool_collider}, link_chain=robot_link_colliders
            )
        },
    )
    scene_id = "collision_scene"
    await scene_api.store_collision_scene(
        cell_name, scene_id, nova_api.models.CollisionSceneAssembly(scene=scene)
    )
    return scene_id


@nova.program(
    name="collision_free_p2p",
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
    viewer=trajectory_utils.TrajectoryViewer(),
)
async def test() -> None:
    """
    Example of planning a collision free PTP motion with IsaacSim collision world.
    """

    async with Nova() as nova:
        cell = nova.cell()
        controller = await cell.controller("ur10e")

        # Adjust mounting to the mounting of your robot in the scene
        await nova._api_client.virtual_robot_setup_api.set_virtual_robot_mounting(
            cell=cell.cell_id,
            controller=controller.controller_id,
            id=0,
            coordinate_system=CoordinateSystem(
                coordinate_system="world",
                name="mounting",
                reference_uid="",
                position=Vector3d(x=0, y=0, z=0),
                rotation=RotationAngles(
                    angles=[0, 0, 0], type=RotationAngleTypes.EULER_ANGLES_EXTRINSIC_XYZ
                ),
            ),
        )

        # NC-1047
        await asyncio.sleep(5)

        # Connect to the controller and activate motion groups
        async with controller[0] as motion_group:
            tcp = "Flange"

            robot_setup: nova_api.models.OptimizerSetup = (
                await motion_group._get_optimizer_setup(tcp=tcp)
            )
            robot_setup.safety_setup.global_limits.tcp_velocity_limit = 5000

            collision_scene_id = await build_collision_world(
                nova, motion_group._cell, robot_setup
            )

            scene_api = nova._api_client.store_collision_scenes_api
            collision_scene = await scene_api.get_stored_collision_scene(
                cell=motion_group._cell, scene=collision_scene_id
            )

            # Pick some poses around colliders placed in the scene
            pose_0 = Pose((500, -900, 700, -np.pi, -0, 0))
            pose_1 = Pose((-400, 500, 150, np.pi, 0, 0))
            pose_2 = Pose((550, 500, 150, -np.pi, 0, 0))
            pose_3 = Pose((-500, -670, 220, -np.pi, 0, 0))

            poses_ordered = [pose_2, pose_1, pose_3, pose_0]

            actions: list[Action] = [
                collision_free(
                    collision_scene=collision_scene,
                    target=pose,
                )
                for pose in poses_ordered
            ]

            joint_trajectory: nova_api.models.JointTrajectory = (
                nova_api.models.JointTrajectory(
                    joint_positions=[], times=[], locations=[]
                )
            )

            joint_trajectory = await motion_group.plan(actions, tcp)

            motion_state_iter = motion_group.stream_execute(
                joint_trajectory,
                actions=actions,
                tcp=tcp,
            )
            print("Executing trajectory with collision avoidance")
            async for motion_state in motion_state_iter:
                pass
            print("Trajectory execution finished.")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(test())
