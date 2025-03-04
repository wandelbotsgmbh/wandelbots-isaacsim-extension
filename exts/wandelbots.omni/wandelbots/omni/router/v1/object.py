from typing import Dict, Literal, Union

import omni.isaac.core.utils.prims as prims_utils
from wandelbots.omni.datatypes import (
    COORDINATE_SYSTEM,
    ROTATION_TYPES,
    CustomPrimData,
    Pose,
    WSPose,
)
from wandelbots.omni.environment import host_database
from wandelbots.omni.utils.prim_utils import PrimUtils
from wandelbots.omni.utils.scene import SceneUtils
from wandelbots.omni.utils.synthetic_data import SyntheticDataUtils
from fastapi import APIRouter, status
from fastapi.exceptions import HTTPException
from pxr import Usd

object_router = APIRouter(prefix="/object", tags=["object"])


async def get_object() -> Usd.Prim:
    """
    Fetches the object
    Returns:
        object prim path

    """
    try:
        return PrimUtils.get_object()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


async def get_stage_units() -> float:
    """
    Fetches the stage units
    Returns:
        stage units in scale factor. E.g. 1 for meters, 0.01 for cm, 0.001 for mm

    """
    return SceneUtils.get_stage_units()


@object_router.get(path="/get_pose", response_model=Pose, operation_id="get_pose")
async def get_pose(
    prim_path: str,
    coordinate_system: COORDINATE_SYSTEM = "local",
    rotation_type: ROTATION_TYPES = "cartesian",
) -> Pose:
    """
    Read the pose of the object in wandelscript (WS) notation (rotvec) in either world coordinates or local coordinates
    Args:
        prim_path: prim path of the object
        coordinate_system: pose in either world coordinates or local coordinates
        rotation_type: pose in either quaternions or cartesian vectors

    Returns:
        6D pose rotation vector in wandelscript format. This pose can be ["m", "cm", "mm"] depending on the stage units set

    """
    return PrimUtils.get_pose(
        prim_path=prim_path,
        coordinate_system=coordinate_system,
        rotation_type=rotation_type,
    )


@object_router.get(
    path="/get_relative_pose", operation_id="get_relative_pose", response_model=Pose
)
async def get_relative_pose(
    prim_path_1: str,
    prim_path_2: str,
    mode: Literal[
        "normal", "inverse_first", "inverse_second", "inverse_both"
    ] = "normal",
    rotation_type: ROTATION_TYPES = "cartesian",
) -> Pose:
    """
    Fetches the relative pose between two objects given their prim paths.
    Args:
        prim_path_1: prim path of the first object
        prim_path_2: prim path of the second object
        mode: The mode of operation. Options are:
              "normal" for prim1::prim2
              "inverse_first" for ~prim1::prim2
              "inverse_second" for prim1::~prim2
              "inverse_both" for ~prim1::~prim2
        rotation_type: pose in either quaternions or cartesian vectors
    Returns:
        relative pose of the object in either cartesian vectors or quaternions
    """
    return PrimUtils.get_relative_pose(
        prim_path_1=prim_path_1,
        prim_path_2=prim_path_2,
        mode=mode,
        rotation_type=rotation_type,
    )


@object_router.post(
    path="/set_relative_pose",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_relative_pose",
    response_model=None,
)
async def set_relative_pose(
    prim_path: str, relative_pose: WSPose, object_first: bool = False
) -> None:
    """
    Applies a relative pose transformation to a given prim using Cartesian coordinates.

    Args:
        prim_path: The path of the prim to transform
        relative_pose: The relative pose to apply (WSPose object) in Cartesian coordinates
        object_first: If True, apply object's pose first, then relative pose. If False, apply relative pose first.
    """
    PrimUtils.set_relative_pose(
        prim_path=prim_path, relative_pose=relative_pose, object_first=object_first
    )


@object_router.post(
    path="/set_pose",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_pose",
    response_model=None,
)
async def set_pose(prim_path: str, input_pose: WSPose) -> None:
    """
    Sets the pose to the given prim given pose in wandelscript(WS) format
    Args:
        prim_path: prim path of the object
        input_pose: input pose of the object in WS format. This pose would be processed as per the units set in the stage
    Returns:
        None

    """
    try:
        PrimUtils.set_pose(prim_path=prim_path, input_pose=input_pose)
    except Exception as e:
        raise HTTPException(422, f"Unable to set pose for the prim:{str(e)}")


@object_router.put(
    path="/set_semantic_label",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_semantic_label",
    response_model=None,
)
async def set_semantic_label(prim_path: str, label: str) -> None:
    """
    Sets a semantic label for an object to capture synthetic data. Can also assign multiple labels ot an object with multiple requests
    Args:
        prim_path: prim path of the object
        label: label to be given for the object

    Returns:
        None

    """
    SyntheticDataUtils.set_semantic_label(prim_path, label)


@object_router.get(
    path="/get_semantic_label",
    operation_id="get_semantic_label",
    response_model=list[str],
)
async def get_semantic_label(prim_path: str) -> list[str]:
    """
    Fetches the semantic label set for the object in the scene
    Args:
        prim_path: prim path of the object

    Returns:
        the semantic labels set for an PrimUtils. Can also be multiple labels

    """
    return SyntheticDataUtils.get_semantic_label(prim_path)


@object_router.get(
    path="/get_all_semantic_labels",
    operation_id="get_all_semantic_labels",
    response_model=Dict[str, list],
)
async def get_all_semantic_labels() -> Dict[str, list]:
    """
    Fetches all the semantic labels set in the scene
    Returns:
        a dictionary with the object prim paths and its corresponding labels

    """
    return SyntheticDataUtils.get_all_semantic_labels()


@object_router.delete(
    path="/delete_all_semantic_labels",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="remove_all_semantic_labels",
    response_model=None,
)
async def remove_all_semantic_labels() -> None:
    """
    Removes all the semantic labels defined in the scene
    Returns:
        None

    """
    SyntheticDataUtils.remove_all_semantic_labels()


@object_router.post(
    path="/set_default_poses",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_default_poses_from_current_state",
    response_model=None,
)
async def set_default_poses_from_current_state(prim_path: str) -> None:
    """
    Sets default poses to the given prim and all its children prims. For this, the poses at runtime are overwritten
    as default. Can be used for resetting objects even when physics simulation is playing
    Args:
        prim_path: path of a workspace or path of a parent prim

    Returns:
        None

    """
    children_prims = prims_utils.get_all_matching_child_prims(prim_path, lambda _: True)
    if children_prims:
        for child_prim in children_prims:
            child_prim_path = child_prim.GetPrimPath().pathString
            child_prim_pose = get_pose(child_prim_path)
            host_database[f"default_poses.{child_prim_path}"] = child_prim_pose
    else:
        raise HTTPException(404, "Given prim not found in the scene")


@object_router.get(
    path="/get_default_poses",
    operation_id="get_default_poses",
    response_model=dict[str, Union[None, Pose]],
)
async def get_default_poses() -> dict[str, Union[None, Pose]]:
    """
    Fetches all the default poses set in the scene
    Returns:
        a dict with a mapping of prim path and corresponding set default poses

    """
    return host_database["default_poses"] if "default_poses" in host_database else {}


@object_router.delete(
    path="/delete_default_poses",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_default_poses",
    response_model=None,
)
async def delete_default_poses() -> None:
    """
    Deletes all the set default poses in the scene
    Returns:
        None

    """
    if "default_poses" in host_database:
        del host_database["default_poses"]


@object_router.post(
    path="/reset_objects",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="reset_objects_to_defined_poses",
    response_model=None,
)
async def reset_objects(prim_path: str) -> None:
    """
    Resets the prims to the default poses set
    Args:
        prim_path: path of the prim which needs to be reset to default poses. Also works when simulation is running

    Returns:
        None

    """
    try:
        PrimUtils.reset_objects(prim_path)
    except Exception as e:
        raise HTTPException(404, f"Not able to reset objects: {str(e)}")


@object_router.post(
    path="/add_metadata",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="add_metadata",
    response_model=None,
)
async def add_metadata(prim_path: str, metadata: CustomPrimData) -> None:
    """
    This endpoint adds custom metadata to the given `prim_path`.
    That means you can define a `type` and `category` for any `prim_path` from your scene.

    In our current API, for tool (category), three types are supported: `GenericGripper`,
    `SurfaceGripper` and `Conveyor'. Nevertheless, you can add any string value to `category` and `type` you
    wish for.

    Args:
        prim_path: the path of the prim
        metadata: the custom data to be added to the prim

    Returns:
        None

    """
    try:
        PrimUtils.add_metadata(prim_path, metadata)
    except Exception as e:
        raise HTTPException(404, f"Custom prim data could not be set: {str(e)}")


@object_router.post(
    path="/remove_metadata",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="remove_metadata",
    response_model=None,
)
async def remove_metadata(prim_path: str) -> None:
    """
    Removes the added metadata to the prim
    Args:
        prim_path: the path of the prim for which metadata has to be deleted

    Returns:
        None
    """
    try:
        PrimUtils.remove_metadata(prim_path)
    except Exception as e:
        raise HTTPException(
            404, f"No custom data found for the given prim path: {str(e)}"
        )


@object_router.post(
    path="/show",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="show_object",
    response_model=None,
)
async def show_object(prim_path: str) -> None:
    """
    Makes the prim visible in the viewport given its prim path

    Args:
        prim_path: the path of the prim

    Returns:
        None
    """
    try:
        PrimUtils.toggle_visibility(prim_path=prim_path, visible=True)
    except Exception as e:
        raise HTTPException(404, f"Could not show prim at path: {prim_path}") from e


@object_router.post(
    path="/hide",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="hide_object",
    response_model=None,
)
async def hide_object(prim_path: str) -> None:
    """
    Hides the prim in the viewport given its prim path

    Args:
        prim_path: the path of the prim

    Returns:
        None
    """
    try:
        PrimUtils.toggle_visibility(prim_path=prim_path, visible=False)
    except Exception as e:
        raise HTTPException(404, f"Could not hide prim at {prim_path}. {e}") from e


@object_router.post(
    path="/enable_collider",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="enable_collider",
    response_model=None,
)
async def enable_collider(prim_path: str) -> None:
    """
    Enable the collider if it is disabled

    Args:
        prim_path: the path of the prim

    Returns:
        None
    """
    try:
        PrimUtils.toggle_collider(prim_path=prim_path, enabled=True)
    except Exception as e:
        raise HTTPException(
            404, f"Could not enable collider of prim at {prim_path}. {e}"
        ) from e


@object_router.post(
    path="/disable_collider",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="disable_collider",
    response_model=None,
)
async def disable_collider(prim_path: str) -> None:
    """
    Disable the collider if it is enabled

    Args:
        prim_path: the path of the prim

    Returns:
        None
    """
    try:
        PrimUtils.toggle_collider(prim_path=prim_path, enabled=False)
    except Exception as e:
        raise HTTPException(
            404, f"Could not disable collider of prim {prim_path}. {e}"
        ) from e


@object_router.post(
    path="/select_object",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="select_object",
    response_model=None,
)
async def select_object(prim_path: str) -> None:
    """
    Select the object by its prim_path.

    Args:
        prim_path: the path of the prim to select in the scene.

    Returns:
        None
    """
    PrimUtils.select_object(prim_path)


@object_router.post(
    path="/enable_joint",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="enable_joint",
    response_model=None,
)
async def enable_joint(prim_path: str) -> None:
    """
    Enable the joint if it is disabled.

    Args:
        prim_path: the path of the prim.

    Returns:
        None
    """
    try:
        PrimUtils.toggle_joint(prim_path=prim_path, enabled=True)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Could not enable joint of prim at path: {prim_path}",
        ) from e


@object_router.post(
    path="/disable_joint",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="disable_joint",
    response_model=None,
)
async def disable_joint(prim_path: str) -> None:
    """
    Disable the joint if it is enabled.

    Args:
        prim_path: the path of the prim.

    Returns:
        None
    """
    try:
        PrimUtils.toggle_joint(prim_path=prim_path, enabled=False)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Could not disable joint of prim at path: {prim_path}",
        ) from e
