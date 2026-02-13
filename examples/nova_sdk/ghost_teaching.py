"""Script to fetch ghost object poses and generate ghost_objects.py enum file."""

import re

import wandelbots_isaacsim_api as isaac_sim_api
from nova.types import Pose


def sanitize_enum_name(prim_path: str) -> str:
    """Convert prim_path to a valid Python enum name.

    Example:
    /World/cell/workspace_m10id12/poses/tool_surface_gripper_Pose_01
    -> TOOL_SURFACE_GRIPPER_POSE_01
    """
    # Extract the last part after the final slash
    name = prim_path.split("/")[-1]

    # Convert to uppercase and replace invalid characters with underscores
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    name = name.upper()

    # Ensure it doesn't start with a digit
    if name[0].isdigit():
        name = f"POSE_{name}"

    return name


async def fetch_poses(isaacsim_api_url: str, reference_prim: str) -> dict[str, Pose]:
    async with isaac_sim_api.ApiClient(
        isaac_sim_api.Configuration(host=isaacsim_api_url)
    ) as isaac_sim_api_client:
        teaching_api = isaac_sim_api.TeachingApi(isaac_sim_api_client)
        ghost_objects = await teaching_api.list_ghost_objects(reference_prim)

        print(f"Ghost objects: {ghost_objects}")
        found_poses = {
            ghost_object.prim_path: Pose(tuple(ghost_object.pose.pose))
            for ghost_object in ghost_objects
        }

        return found_poses


async def generate_ghost_objects_file(
    isaacsim_api_url: str,
    reference_prim: str,
    output_file_path: str = "ghost_objects.py",
):
    """Fetch poses and generate ghost_objects.py with enum."""
    if not isaacsim_api_url or isaacsim_api_url == "":
        print("Error: Please provide the url to the isaac sim api.")
        return

    print(f"Fetching poses from {isaacsim_api_url}...")
    poses = await fetch_poses(isaacsim_api_url, reference_prim)
    print(f"Fetched {len(poses)} poses")

    if not poses:
        print("No poses found!")
        return

    # Generate the enum file content
    lines = [
        '"""Auto-generated ghost object poses as enum."""',
        "from enum import Enum",
        "from nova.types import Pose",
        "",
        "",
        "class GhostObjects(Enum):",
        '    """Enum containing all ghost object poses from IsaacSim."""',
        "",
    ]

    # Add each pose as an enum member
    for prim_path, pose in poses.items():
        enum_name = sanitize_enum_name(prim_path)
        position = pose.position
        orientation = pose.orientation

        # Format the pose tuple
        pose_tuple = f"({position[0]}, {position[1]}, {position[2]}, {orientation[0]}, {orientation[1]}, {orientation[2]})"

        lines.append(f"    {enum_name} = Pose{pose_tuple}  # {prim_path}")

    # Write to file
    with open(output_file_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nGenerated {output_file_path} with {len(poses)} poses")
    print(
        f"Example enum usage: GhostObjects.{sanitize_enum_name(list(poses.keys())[0])}.value"
    )
