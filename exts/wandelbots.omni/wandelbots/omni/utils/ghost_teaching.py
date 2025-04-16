import carb

import omni.isaac.core.utils.prims as prims_utils
import omni.isaac.core.utils.stage as stage_utils
from omni.usd.commands import DeletePrimsCommand
from wandelbots.omni.datatypes import WSPose
from pxr import Sdf, Usd, UsdPhysics, UsdShade
from wandelbots.omni.datatypes import (
    GHOST_MATERIAL_MDL_FILE,
    SHADER_IDENTIFIER,
    GhostObjectSource,
    TCPSource,
)
from wandelbots.omni.router.v1.object import set_pose
from wandelbots.omni.utils.prim_utils import PrimUtils
import omni.usd


def find_prims_with_matching_custom_data(key: str):
    """
    Find all prims in the current stage that have the specified key (and a truthy value stored under the key)
    in their custom data.
    """
    for prim in stage_utils.traverse_stage():
        custom_data = prim.GetCustomData()
        if custom_data.get(key):
            yield prim


async def add_material_to_prim(prim: Usd.Prim):
    """
    Applies ghost material and shader to the prim given its path
    """
    stage = prim.GetStage()

    looks_path = prim.GetPath().AppendChild("Looks")
    if not stage.GetPrimAtPath(looks_path):
        stage.DefinePrim(looks_path, "Scope")

    material_path = looks_path.AppendChild("GhostMaterial")
    material = UsdShade.Material.Define(stage, material_path)

    shader_path = material_path.AppendChild("Shader")
    shader = UsdShade.Shader.Define(stage, shader_path)
    shader.SetSourceAsset(GHOST_MATERIAL_MDL_FILE, "mdl")
    shader.SetSourceAssetSubIdentifier(SHADER_IDENTIFIER)

    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    material.CreateDisplacementOutput().ConnectToSource(
        shader.ConnectableAPI(), "displacement"
    )
    material.CreateVolumeOutput().ConnectToSource(shader.ConnectableAPI(), "volume")
    UsdShade.MaterialBindingAPI(prim).Bind(
        material, UsdShade.Tokens.strongerThanDescendants
    )


def register_ghost_object(prim: Usd.Prim, source_ghost: False):
    """
    Registers custom data to ghost object to make them discoverable
    """
    custom_data = prim.GetCustomData()
    if "metadata" not in custom_data:
        custom_data["metadata"] = {}

    custom_data["metadata"].update({"is_ghost": True})
    custom_data["metadata"].update({"source_ghost": source_ghost})
    prim.SetCustomData(custom_data)


def remove_physics_attributes(prim):
    # If this is a joint prim, remove it and stop further recursion
    if prim.GetTypeName() in ["PhysicsPrismaticJoint", "PhysicsFixedJoint"]:
        # (I have no clue what the destructive=False flag does, but without it, it does not work)
        DeletePrimsCommand([prim.GetPath()], destructive=False).do()
        return

    # Delete all configured physics APIs
    if prim.HasAPI(UsdPhysics.RigidBodyAPI):
        prim.RemoveAPI(UsdPhysics.RigidBodyAPI)

    if prim.HasAPI(UsdPhysics.CollisionAPI):
        prim.RemoveAPI(UsdPhysics.CollisionAPI)

    # Recurse into child prims
    for child in prim.GetChildren():
        remove_physics_attributes(child)


def get_robot_prim_path(prim: Usd.Prim):
    # get parent prim and fetch first robot from that workspace
    parent_workspace_prim = prim.GetParent().GetParent()
    search_prims = prims_utils.get_all_matching_child_prims(
        parent_workspace_prim.GetPrimPath().pathString,
        lambda prim_path: "tool_" not in prim_path,
    )
    for prim in search_prims:
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            return prim.GetPrimPath().pathString

    carb.log_error("No robot found in the workspace")
    return None


def get_possible_ghost_object_sources() -> list[GhostObjectSource]:
    """
    Return the prim paths of all prims that are sources for ghost objects i.e. tools.
    These ghost object sources must follow a strict predicate `tool_`.
    """
    children_prims = prims_utils.get_all_matching_child_prims(
        "/", lambda x: "tool_" in x.lower() and "ghost" not in x.lower()
    )
    source_objects = [
        GhostObjectSource(name=prim.GetPath().name, prim_path=str(prim.GetPath()))
        for prim in set(children_prims)
        if prim.GetPath().name.lower().startswith("tool_")
    ]
    return source_objects


def prim_starts_with(prim_path: str, prefix: str) -> bool:
    return prim_path.lower().split("/")[-1].startswith(prefix)


def get_all_tcp_sources(base_prim_path: str) -> list[TCPSource]:
    """
    Return the prim paths of all tcps that are defined in the scene which follows a strict predicate `tcp_` or 'TCP_'
    """
    children_prims = prims_utils.get_all_matching_child_prims(
        base_prim_path, lambda x: prim_starts_with(x, "tcp_")
    )
    flange_prims = [
        prim
        for prim in children_prims
        if "flange" in prim.GetPrimPath().pathString.lower()
    ]
    tcp_sources = []
    for prim in set(children_prims):
        if prim in flange_prims:
            tcp_source = TCPSource(
                name=str(prim.GetPath()).rsplit("/", 1)[-1],
                prim_path=str(prim.GetPath()),
                value=WSPose(pose=[0, 0, 0, 0, 0, 0]),
            )
            tcp_sources.append(tcp_source)
            continue

        # ToDo: optimise this search workspace
        search_workspace = prim.GetParent().GetParent().GetParent()
        matching_flange_prims = prims_utils.get_all_matching_child_prims(
            search_workspace.GetPrimPath().pathString,
            lambda x: prim_starts_with(x, "tcp_flange"),
        )
        if len(matching_flange_prims) == 0:
            continue
        flange_prim = matching_flange_prims[0]
        flange_pose_to_prim = PrimUtils.get_relative_pose(
            prim.GetPrimPath().pathString, flange_prim.GetPrimPath().pathString
        )
        tcp_source = TCPSource(
            name=str(prim.GetPath()).rsplit("/", 1)[-1],
            prim_path=str(prim.GetPath()),
            value=flange_pose_to_prim,
            mode="normal",
        )
        tcp_sources.append(tcp_source)

    return tcp_sources


async def add_source_ghost_object(source_prim_path: str) -> None:
    """
    Create a ghost object from the prim under the specified path.
    This will clone the prim, apply the specified material and shift the origin of the prim.
    """
    source_ghost_base_path = "/".join(source_prim_path.split("/")[:-1])
    source_ghost_object_name = source_prim_path.split("/")[-1]
    stage = omni.usd.get_context().get_stage()
    stage.DefinePrim(f"{source_ghost_base_path}/source_ghosts", "Scope")
    stage.DefinePrim(f"{source_ghost_base_path}/poses", "Scope")

    clone_prim_path = (
        source_ghost_base_path + "/source_ghosts/" + source_ghost_object_name
    )
    tcp_sources = get_all_tcp_sources(base_prim_path=source_prim_path)
    for tcp_source in tcp_sources:
        tcp_name = tcp_source.prim_path.split("/")[-1]

        # copy prim
        target_path = stage_utils.get_next_free_path(clone_prim_path) + f"_{tcp_name}"
        stage = omni.usd.get_context().get_stage()
        source_prim = stage.GetPrimAtPath(source_prim_path)
        Sdf.CopySpec(
            stage.GetRootLayer(), source_prim_path, stage.GetRootLayer(), target_path
        )

        # fix visibility
        target_prim = stage.DefinePrim(target_path, source_prim.GetTypeName())
        visibility_attribute = target_prim.GetAttribute("visibility")
        visibility_attribute.Set("invisible")

        # fix transformation
        tcp_fix_paths = target_prim.GetChildren()
        for each in tcp_fix_paths:
            rel_pose = PrimUtils.get_relative_pose(
                each.GetPrimPath().pathString, tcp_source.prim_path, mode="inverse_first"
            )
            await set_pose(each.GetPrimPath().pathString, rel_pose)

        # add ghost material to object
        await add_material_to_prim(target_prim)

        # Remove all physics attributes from the prim and its children
        remove_physics_attributes(target_prim)

        # register prim
        register_ghost_object(target_prim, source_ghost=True)

    if not tcp_sources:
        raise ValueError(
            "TCP is not configured for the given tool. Source ghost cannot be created"
        )
