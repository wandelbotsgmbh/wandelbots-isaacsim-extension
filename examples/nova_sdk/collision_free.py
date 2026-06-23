import asyncio
import os
from typing import cast

from dotenv import load_dotenv
import numpy as np

import nova
from nova import Nova, api, run_program
from nova.actions import Action
from nova.actions.motions import Motion, collision_free
from nova.cell import virtual_controller
from nova.program import ProgramPreconditions
from nova.types import MotionSettings, Pose
import wandelbots_isaacsim_api as isaac_sim_api
import wandelbots_isaacsim_api.collision.utils as collision_utils
import wandelbots_api_client as nova_api_client


def convert_collider_to_dict(collider: nova_api_client.models.Collider) -> dict:
    """Convert wandelbots_api_client Collider to a dict suitable for nova.api"""
    # The ColliderShape wrapper needs to be unwrapped to get the actual shape
    shape_instance = collider.shape.actual_instance
    shape_dict = shape_instance.model_dump(mode="json", by_alias=True)

    pose_dict = None
    if collider.pose:
        pose_dict = collider.pose.model_dump(mode="json", by_alias=True)

    result = {"shape": shape_dict}
    if pose_dict:
        result["pose"] = pose_dict
    if collider.margin is not None:
        result["margin"] = collider.margin
    return result


async def build_collision_world(
    nova: Nova,
    cell_name: str,
    motion_group_description: api.models.MotionGroupDescription,
) -> str:
    store_collision_components_api = nova.api.store_collision_components_api
    store_collision_setups_api = nova.api.store_collision_setups_api
    motion_group_models_api = nova.api.motion_group_models_api

    motion_group_model = motion_group_description.motion_group_model.root
    scene_colliders: dict[str, api.models.Collider] = dict()

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
    # Convert Isaac Sim colliders to Nova API colliders by serializing to dict
    converted_colliders: dict[str, dict] = {}
    for collider_id, collider in scene_colliders.items():
        # Unwrap ColliderShape and convert to dict for nova.api
        collider_dict = convert_collider_to_dict(collider)
        converted_colliders[collider_id] = collider_dict
        await store_collision_components_api.store_collider(
            cell=cell_name,
            collider=collider_id.replace("/", "_"),
            collider2=collider_dict,
        )

    # define TCP collider geometry
    # The colliders can be used from the sweep as well, but this needs some offset
    tool_collider = api.models.Collider(
        shape=api.models.Box(
            size_x=100,
            size_y=100,
            size_z=100,
            shape_type="box",
            box_type=api.models.BoxType.FULL,
        )
    )
    await store_collision_components_api.store_collision_tool(
        cell=cell_name, tool="tool_box", request_body={"tool_collider": tool_collider}
    )

    # define robot link geometries
    robot_link_colliders = (
        await motion_group_models_api.get_motion_group_collision_model(
            motion_group_model=motion_group_model
        )
    )
    await store_collision_components_api.store_collision_link_chain(
        cell=cell_name, link_chain="robot_links", collider=robot_link_colliders
    )

    # assemble scene
    collision_setup = api.models.CollisionSetup(
        colliders=api.models.ColliderDictionary(converted_colliders),
        link_chain=api.models.LinkChain(
            list(api.models.Link(link) for link in robot_link_colliders)
        ),
    )
    scene_id = "collision_scene"
    await store_collision_setups_api.store_collision_setup(
        cell=cell_name, setup="collision_scene", collision_setup=collision_setup
    )
    return scene_id


@nova.program(
    name="collision_free_p2p",
    preconditions=ProgramPreconditions(
        controllers=[
            virtual_controller(
                name="ur10e",
                manufacturer=api.models.Manufacturer.UNIVERSALROBOTS,
                type=api.models.VirtualControllerTypes.UNIVERSALROBOTS_UR10E,
            )
        ],
        cleanup_controllers=False,
    ),
)
async def test(ctx: nova.ProgramContext) -> None:
    """
    Example of planning a collision free PTP motion with IsaacSim collision world.
    """
    nova = ctx.nova
    cell = nova.cell()
    controller = await cell.controller("ur10e")

    # Adjust mounting to the mounting of your robot in the scene
    await nova.api.virtual_robot_setup_api.set_virtual_controller_mounting(
        cell=cell.cell_id,
        controller=controller.id,
        motion_group=f"0@{controller.id}",
        coordinate_system=api.models.CoordinateSystem(
            name="mounting",
            coordinate_system="world",
            position=api.models.Vector3d([0, 0, 0]),
            orientation=api.models.Orientation([0, 0, 0]),
            orientation_type=api.models.OrientationType.EULER_ANGLES_EXTRINSIC_XYZ,
        ),
    )

    # NC-1047
    await asyncio.sleep(5)

    # Connect to the controller and activate motion groups
    async with controller[0] as motion_group:
        tcp = "Flange"

        motion_group_description: api.models.MotionGroupDescription = (
            await motion_group.get_description()
        )
        collision_scene_id = await build_collision_world(
            nova, cell.cell_id, motion_group_description
        )

        store_collision_setups_api = nova.api.store_collision_setups_api
        collision_setup = await store_collision_setups_api.get_stored_collision_setup(
            cell=cell.cell_id, setup=collision_scene_id
        )

        # Pick some poses around colliders placed in the scene
        pose_0 = Pose((500, -900, 700, -np.pi, -0, 0))
        pose_1 = Pose((-400, 500, 150, np.pi, 0, 0))
        pose_2 = Pose((550, 500, 150, -np.pi, 0, 0))
        pose_3 = Pose((-500, -670, 220, -np.pi, 0, 0))

        poses_ordered = [pose_2, pose_1, pose_3, pose_0]

        actions: list[Action] = [
            collision_free(
                collision_setup=collision_setup,
                target=pose,
                settings=MotionSettings(tcp_velocity_limit=200),
            )
            for pose in poses_ordered
        ]

        joint_trajectory: api.models.JointTrajectory = api.models.JointTrajectory(
            joint_positions=[], times=[], locations=[]
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
    run_program(test)
