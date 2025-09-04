from typing import Annotated, Literal, Union


import omni.isaac.core.utils.prims as prims_utils
from pydantic import RootModel, BaseModel, Field
from wandelbots.omni.datatypes import (
    COORDINATE_SYSTEM,
    ROTATION_TYPES,
    CustomPrimData,
    Pose,
    WSPose,
    RelativePoseMode,
)
import omni.isaac.core.utils.semantics as semantic_utils
from wandelbots.omni.environment import host_database
from wandelbots.omni.utils.prims import PrimUtils
import omni.usd
import omni.isaac.core.utils.stage as stage_utils
from fastapi import APIRouter, status, Query, Body, Depends
from fastapi.exceptions import HTTPException

prims_router = APIRouter(prefix="/prims", tags=["Prims"])


def validate_prim_path_query(name="prim_path") -> str:
    def validator(
        prim_path: str = Query(..., description="Prim path of the object", alias=name),
    ) -> str:
        if not PrimUtils.is_prim_valid(prim_path):
            raise HTTPException(404, detail=f"Invalid prim path: {prim_path}")
        return prim_path

    return validator


def validate_prim_path_body(
    prim_path: str = Body(..., description="Prim path of the object"),
) -> str:
    if not PrimUtils.is_prim_valid(prim_path):
        raise HTTPException(404, detail=f"Invalid prim path: {prim_path}")
    return prim_path


@prims_router.get(
    path="/poses",
    response_model=Pose,
    operation_id="get_pose",
    responses={
        200: {"description": "Successfully retrieved the pose"},
        404: {"description": "Invalid prim path"},
        500: {"description": "Internal server error: Unable to fetch pose"},
    },
)
async def get_pose(
    prim_path: str = Depends(validate_prim_path_query("prim_path")),
    coordinate_system: COORDINATE_SYSTEM = Query(
        "local", description="pose in either world coordinates or local coordinates"
    ),
    rotation_type: ROTATION_TYPES = Query(
        "cartesian", description="pose in either quaternions or cartesian vectors"
    ),
) -> Pose:
    """
    Returns the current pose of a prim in WandelScript (WS) 6D format — a 3D position and 3D rotation vector.
    Rotations are in radians, position in millimeters.
    The pose can be returned in either local or world coordinate system.
    """
    try:
        return PrimUtils.get_prim_pose(
            prim_path=prim_path,
            coordinate_system=coordinate_system,
            rotation_type=rotation_type,
        )
    except Exception as e:
        raise HTTPException(500, f"Unable to fetch pose for the prim: {e}")


@prims_router.put(
    path="/poses",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="update_pose",
    responses={
        204: {"description": "Successfully set the pose"},
        404: {"description": "Invalid prim path"},
        500: {"description": "Internal server error: Unable to set pose for the prim"},
    },
)
async def update_pose(
    prim_path: str = Depends(validate_prim_path_body),
    input_pose: WSPose = Body(..., description="input pose of the object in WS format"),
) -> None:
    """
    Sets the pose of the given prim using WandelScript (WS) format — a 6D vector (3D position, 3D rotation).
    Rotations are in radians. Position is in millimeters.
    """
    try:
        PrimUtils.set_prim_pose(prim_path=prim_path, input_pose=input_pose)
    except Exception as e:
        raise HTTPException(500, f"Unable to set pose for the prim: {e}")


@prims_router.get(
    path="/poses/relative",
    operation_id="get_relative_pose",
    response_model=Pose,
    responses={
        200: {"description": "Successfully retrieved the relative pose"},
        404: {"description": "One or both prims not found in the scene"},
        500: {"description": "Internal server error: Unable to fetch relative pose"},
    },
)
async def get_relative_pose(
    prim_path_1: Annotated[str, Depends(validate_prim_path_query("prim_path_1"))],
    prim_path_2: Annotated[str, Depends(validate_prim_path_query("prim_path_2"))],
    mode: RelativePoseMode = Query(
        default="normal", description="The mode of operation"
    ),
    rotation_type: ROTATION_TYPES = "cartesian",
) -> Pose:
    """
    Calculates the relative pose between two objects specified by their prim paths.

    Supports four modes of operation:

    |Mode|Description|
    |--|--|
    |`normal`| Computes prim1::prim2|
    |`inverse_first`| Computes ~prim1::prim2|
    |`inverse_second`| Computes prim1::~prim2|
    |`inverse_both`| Computes ~prim1::~prim2|

    The resulting pose is returned in WandelScript (WS) 6D format.
    Rotations are in radians, position in millimeters.
    The coordinate system used depends on the rotation_type parameter: either 'cartesian' or 'quaternion'.
    """
    try:
        return PrimUtils.get_relative_pose(
            prim_path_1=prim_path_1,
            prim_path_2=prim_path_2,
            mode=mode,
            rotation_type=rotation_type,
        )
    except Exception as e:
        raise HTTPException(500, f"Unable to fetch relative pose for the prims: {e}")


@prims_router.post(
    path="/poses/relative",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="apply_relative_pose",
    response_model=None,
    responses={
        204: {"description": "Relative pose set successfully"},
        404: {"description": "Invalid prim path"},
        500: {"description": "Unable to set relative pose for the prim"},
    },
)
async def apply_relative_pose(
    prim_path: str = Depends(validate_prim_path_body),
    relative_pose: WSPose = Body(
        ...,
        description="relative pose to apply (WSPose object) in Cartesian coordinates",
    ),
    object_first: bool = Body(
        default=False,
        description="If True, apply object's pose first, then relative pose. If False, apply relative pose first.",
    ),
) -> None:
    """
    Applies a relative pose to a prim using WandelScript (WS) Cartesian format. This modifies the prim's pose by composing it with the input relative transform.
    If `object_first` is True, the prim's pose is applied before the relative transform, otherwise, the relative transform is applied first.
    """
    try:
        PrimUtils.set_relative_pose(
            prim_path=prim_path, relative_pose=relative_pose, object_first=object_first
        )
    except Exception as e:
        raise HTTPException(500, f"Unable to set relative pose for the prim: {e}")


@prims_router.put(
    path="/labels",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_semantic_label",
    responses={
        204: {"description": "Semantic label set successfully"},
        404: {"description": "Invalid prim path"},
        422: {"description": "Invalid label"},
    },
)
async def set_semantic_label(
    prim_path: str = Depends(validate_prim_path_body),
    label: str = Body(..., description="label to be given for the object"),
) -> None:
    """
    Sets a semantic label for an object to capture synthetic data. Can also assign multiple labels for a prim.
    """
    try:
        prim = PrimUtils.get_prim(prim_path)
        semantic_utils.add_update_semantics(prim, label)
    except Exception as e:
        raise HTTPException(422, f"Invalid label: {e}")


# This model is needed because openapi generator generates
# dict[str, list[Optional[str]]] for whatever reason if this type
# is just passed plain to response model
class PrimsLabelsResponse(RootModel):
    root: dict[str, list[str]]


@prims_router.get(
    path="/labels",
    operation_id="list_semantic_labels",
    response_model=PrimsLabelsResponse,
    responses={
        200: {"description": "Successfully retrieved semantic labels"},
        404: {"description": "Invalid prim path"},
        422: {"description": "Invalid label"},
    },
)
async def list_semantic_labels(
    prim_path: str = None,
) -> PrimsLabelsResponse:
    """
    Fetches the semantic labels set for the object in the scene. Can also be multiple labels
    """
    if prim_path is not None and not PrimUtils.is_prim_valid(prim_path):
        raise HTTPException(404, detail=f"Invalid prim path: {prim_path}")

    if prim_path:
        prims = [PrimUtils.get_prim(prim_path)]
    else:
        prims = stage_utils.traverse_stage()

    try:
        labels: dict[str, list[str]] = {}
        for prim in prims:
            label = semantic_utils.get_semantics(prim)

            # Read label name from object
            # e.g. {'Semantics': ('class', 'robot')} where robot is the label name
            if label and "Semantics" in label:
                labels[prim.GetPrimPath().pathString] = [label["Semantics"][1]]
        return PrimsLabelsResponse(labels)
    except Exception as e:
        raise HTTPException(422, f"Failed to collect labels. {e}")


@prims_router.delete(
    path="/labels",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="clear_semantic_labels",
    response_model=None,
    responses={
        204: {
            "description": "Semantic labels removed successfully for the specified prim"
        },
        404: {"description": "Invalid prim path"},
        500: {"description": "Unable to remove semantic labels for the given prim"},
    },
)
async def clear_semantic_labels(
    prim_path: str | None = None,
) -> None:
    """
    Deletes semantic labels from the specified list of prim paths.
    """

    if prim_path is not None and not PrimUtils.is_prim_valid(prim_path):
        raise HTTPException(404, detail=f"Invalid prim path: {prim_path}")

    if prim_path:
        prim_paths = [prim_path]
    else:
        prim_paths = [
            prim.GetPrimPath().pathString for prim in stage_utils.traverse_stage()
        ]

    try:
        for prim_path in prim_paths:
            prim = PrimUtils.get_prim(prim_path)
            semantic_utils.remove_all_semantics(prim, recursive=False)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Unable to remove semantic labels for prim: {e}"
        )


@prims_router.put(
    path="/poses/default",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="assign_default_poses",
    response_model=None,
    responses={
        204: {"description": "Default poses set successfully"},
        404: {"description": "Invalid prim path"},
        500: {"description": "Failed to set default poses"},
    },
)
async def assign_default_poses(
    prim_path: str = Depends(validate_prim_path_body),
) -> None:
    """
    Sets default poses to the given prim and all its children prims. For this, the poses at runtime are overwritten
    as default. Can be used for resetting objects even when physics simulation is playing
    """
    children_prims = prims_utils.get_all_matching_child_prims(prim_path, lambda _: True)
    if not children_prims:
        raise HTTPException(500, "Unable to set default poses")

    for child_prim in children_prims:
        child_prim_path = child_prim.GetPrimPath().pathString
        child_prim_pose = PrimUtils.get_prim_pose(
            prim_path=prim_path,
            coordinate_system="local",
            rotation_type="cartesian",
        )
        if child_prim_pose:
            host_database[f"default_poses.{child_prim_path}"] = child_prim_pose


@prims_router.get(
    path="/poses/default",
    operation_id="list_default_poses",
    response_model=dict[str, Union[None, Pose]],
    responses={200: {"description": "Successfully retrieved default poses"}},
)
async def list_default_poses() -> dict[str, Union[None, Pose]]:
    """
    Returns a dictionary of prim paths to their saved default poses.
    """
    return host_database.get("default_poses", {})


@prims_router.delete(
    path="/poses/default",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="clear_default_poses",
    response_model=None,
    responses={204: {"description": "Default poses deleted successfully"}},
)
async def clear_default_poses() -> None:
    """
    Deletes all the set default poses in the scene
    """
    if "default_poses" in host_database:
        del host_database["default_poses"]


@prims_router.post(
    path="/poses/default/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="reset_to_default_poses",
    response_model=None,
    responses={
        204: {"description": "Prims reset to default poses successfully"},
        404: {"description": "Invalid prim path"},
        500: {"description": "Failed to reset poses"},
    },
)
async def reset_prim_poses_to_default(
    prim_path: str = Depends(validate_prim_path_body),
) -> None:
    """
    Resets the prims to the default poses set given a prim path. Can be used for resetting objects even when physics simulation is playing.
    """
    try:
        PrimUtils.reset_objects(prim_path)
    except Exception as e:
        raise HTTPException(500, f"Not able to reset objects: {e}")


@prims_router.put(
    path="/metadata",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_prim_metadata",
    response_model=None,
    responses={
        204: {"description": "Metadata set successfully"},
        404: {"description": "Invalid prim path"},
        500: {"description": "Failed to set custom metadata on the prim"},
    },
)
async def set_prim_metadata(
    prim_path: str = Depends(validate_prim_path_body),
    metadata: CustomPrimData = Body(
        ..., description="custom data to be added to the prim"
    ),
) -> None:
    """
    This endpoint adds custom metadata to the given `prim_path`.
    That means you can define a `type` and `category` for any `prim_path` from your scene.

    In our current API, for tool (category), three types are supported: `GenericGripper`,
    `SurfaceGripper` and `Conveyor'. Nevertheless, you can add any string value to `category` and `type` you
    wish for.
    """
    try:
        prim = PrimUtils.get_prim(prim_path)
        custom_data = prim.GetCustomData()
        custom_data["metadata"] = dict(metadata)
        prim.SetCustomData(custom_data)
    except Exception as e:
        raise HTTPException(500, f"Custom prim data could not be set: {e}")


@prims_router.delete(
    path="/metadata",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="remove_prim_metadata",
    response_model=None,
    responses={
        204: {"description": "Metadata removed successfully"},
        404: {"description": "Invalid prim path"},
        500: {"description": "Failed to remove custom metadata from the prim"},
    },
)
async def remove_prim_metadata(
    prim_path: str = Depends(validate_prim_path_query("prim_path")),
) -> None:
    """
    Removes the added metadata to the prim
    """
    try:
        prim = PrimUtils.get_prim(prim_path)
        prim.SetCustomData({})
    except Exception as e:
        raise HTTPException(500, f"No custom data found for the given prim path: {e}")


@prims_router.patch(
    path="/visibility",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_prim_visibility",
    response_model=None,
    responses={
        204: {"description": "Prim visibility updated successfully"},
        404: {"description": "Invalid prim path"},
        500: {"description": "Failed to update visibility of prim"},
    },
)
async def set_prim_visibility(
    prim_path: str = Depends(validate_prim_path_body),
    visibility: Literal["show", "hide"] = Body(
        "show", description="set to `show` or `hide` the prim"
    ),
) -> None:
    """
    Makes the prim visible or hidden in the viewport given its prim path
    """
    try:
        prim = PrimUtils.get_prim(prim_path)
        prim.GetAttribute("visibility").Set(
            "inherited" if visibility == "show" else "invisible"
        )
    except Exception as e:
        raise HTTPException(500, f"Could not show prim at path: {e}")


class PrimSelection(BaseModel):
    prim_paths: list[str] = Field(
        ..., descirption="Prims which will replace the selection"
    )


def validate_prim_selection(selection: PrimSelection) -> str:
    invalid_prim_paths = []
    for prim_path in selection.prim_paths:
        if not PrimUtils.is_prim_valid(prim_path):
            invalid_prim_paths.append(prim_path)

    if len(invalid_prim_paths) > 0:
        raise HTTPException(
            404,
            detail=f"Selection contains invalid prims [{', '.join(invalid_prim_paths)}]",
        )
    return selection


@prims_router.put(
    path="/selected",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="select_prims",
    response_model=None,
    responses={
        204: {"description": "Prim selected successfully"},
        404: {"description": "Invalid prim path"},
        500: {"description": "Could not select prim"},
    },
)
async def select_prims(
    selection: PrimSelection = Depends(validate_prim_selection),
) -> None:
    """
    Selects the prims in the UI by their prim path.
    """
    try:
        context_selection = omni.usd.get_context().get_selection()
        context_selection.clear_selected_prim_paths()
        context_selection.set_selected_prim_paths(selection.prim_paths, False)
    except Exception as e:
        raise HTTPException(500, f"Could not select prim at path: {e}")


@prims_router.get(
    path="/selected",
    status_code=status.HTTP_200_OK,
    operation_id="list_selected_prims",
    responses={200: {"description": "Prim selection"}},
)
async def list_selected_prims() -> PrimSelection:
    """
    Returns all prims selected in the tree
    """
    return PrimSelection(
        prim_paths=omni.usd.get_context().get_selection().get_selected_prim_paths()
    )


@prims_router.patch(
    path="/physics/joints",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_joint_state",
    response_model=None,
    responses={
        204: {"description": "Joint state updated successfully"},
        404: {"description": "Invalid prim path"},
        500: {"description": "Could not update joint state"},
    },
)
async def set_joints(
    prim_path: str = Depends(validate_prim_path_body),
    enable: bool = Body(
        ..., description="Set to true to enable joint, false to disable"
    ),
) -> None:
    """
    Enables or disables the joints for a prim.
    """
    try:
        prim = PrimUtils.get_prim(prim_path)
        prim.GetAttribute("physics:jointEnabled").Set(True if enable else False)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not disable joint of prim at path: {e}",
        )
