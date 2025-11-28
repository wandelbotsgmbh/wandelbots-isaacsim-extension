from typing import Annotated, Literal

from fastapi.exceptions import HTTPException
from fastapi import Response, status, Query, Body, Depends

from pydantic import RootModel
from wandelbots.omni.periphery.camera_configuration import (
    PointCloud,
    BoundingBox2D,
    BoundingBox3D,
    SemanticSegmentationData,
    InstanceSegmentationData,
)

from fastapi import APIRouter
from wandelbots.omni.periphery import (
    CameraCaptureService,
    get_camera_capture_service,
)
import omni.kit.viewport.utility
import io
from PIL import Image

import isaacsim.core.utils.stage as stage_utils

cameras_router = APIRouter(prefix="/periphery/cameras", tags=["Periphery (Camera)"])

CameraCaptureServiceDep = Annotated[
    CameraCaptureService, Depends(get_camera_capture_service)
]

ImageCaptureResultOption = Annotated[
    Literal["json", "rgb_png"],
    Query(
        ...,
        description="Format which will be used to represent the captured data",
    ),
]


class ImageResolution:
    def __init__(
        self,
        width: int = Query(640, description="Image width in px"),
        height: int = Query(480, description="Image height in px"),
    ):
        self.width = width
        self.height = height

    @property
    def tuple(self) -> tuple[int, int]:
        return self.width, self.height


def to_png_response(image: Image) -> Response:
    image_bytes = io.BytesIO()
    image.save(image_bytes, format="PNG")
    return Response(content=image_bytes.getvalue(), media_type="image/png")


async def find_camera_or_raise(
    camera_prim_path: str = Query(..., description="Path of camera prim"),
) -> str:
    """
    Fetches the camera defined
    """
    stage = stage_utils.get_current_stage()
    if not stage.GetPrimAtPath(camera_prim_path).IsValid():
        raise HTTPException(404, f"{camera_prim_path} not found in stage")
    return camera_prim_path


CameraPath = Annotated[str, Depends(find_camera_or_raise)]


class CamerasPrimsResponse(RootModel):
    root: list[str]


@cameras_router.get(
    path="/prims",
    response_model=CamerasPrimsResponse,
    operation_id="list_camera_prims",
    responses={
        200: {
            "description": "List of camera prim paths in the scene",
            "content": {
                "application/json": {
                    "example": ["/World/Camera1", "/World/Robot/Camera2"]
                }
            },
        },
        500: {"description": "Failed to retrieve camera prim paths from the stage"},
    },
)
async def list_camera_prims(
    camera_service: CameraCaptureServiceDep,
) -> CamerasPrimsResponse:
    """
    Returns all camera prim paths defined in the current scene stage.
    """
    try:
        return camera_service.list_camera_prims()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unable to fetch camera prims: {e}",
        )


@cameras_router.get(
    path="/active",
    operation_id="get_active_camera",
    response_model=str,
    responses={
        200: {"description": "Active camera prim path"},
        500: {"description": "Could not fetch active camera viewport"},
    },
)
async def get_active_camera() -> str:
    """
    Returns the active camera prim path from the active viewport.
    """
    try:
        viewport = omni.kit.viewport.utility.get_active_viewport()
        return viewport.camera_path.pathString
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Unable to fetch active camera viewport: {e}"
        )


@cameras_router.put(
    path="/active",
    operation_id="set_active_camera",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Successfully set active camera"},
        404: {"description": "Camera prim not found or invalid"},
    },
)
async def set_active_camera(
    camera_path: str = Body(
        ..., description="Prim path of the camera to be set as active"
    ),
) -> None:
    """
    Sets the given camera prim path as the active viewport camera.
    """
    try:
        viewport = omni.kit.viewport.utility.get_active_viewport()
        viewport.camera_path = camera_path
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"Unable to set active camera to {camera_path}"
        )


@cameras_router.get(
    path="/capture/color",
    operation_id="capture_color_image",
    response_class=Response,
    responses={
        200: {
            "description": "Successfully fetched color image from camera",
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        404: {"description": "Camera not configured"},
        500: {"description": "Could not fetch color image"},
    },
)
async def capture_color_image(
    camera_path: CameraPath,
    camera_service: CameraCaptureServiceDep,
    resolution: ImageResolution = Depends(),
) -> Response:
    """
    Retrieves the raw RGB color image from the camera's point of view.

    - Returns a PNG image captured from the current camera.
    """
    try:
        return to_png_response(
            await camera_service.get_color_image(camera_path, resolution.tuple)
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unable to capture color image: {e}",
        )


@cameras_router.get(
    path="/capture/normals",
    operation_id="capture_normals_image",
    responses={
        200: {
            "description": "Successfully fetched normals data",
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        404: {"description": "Camera not configured"},
        500: {"description": "Could not fetch normals"},
    },
)
async def capture_normals_image(
    result_type: ImageCaptureResultOption,
    camera_path: CameraPath,
    camera_service: CameraCaptureServiceDep,
    resolution: ImageResolution = Depends(),
) -> list[list[list[float]]]:
    """
    Retrieves surface normal data from the camera's captured image.
    """
    try:
        if result_type == "json":
            return await camera_service.get_normals(camera_path, resolution.tuple)
        return to_png_response(
            await camera_service.get_normals_image(camera_path, resolution.tuple)
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unable to capture normals: {e}",
        )


@cameras_router.get(
    path="/capture/depth",
    operation_id="capture_depth_image",
    responses={
        200: {
            "description": "Successfully fetched depth data",
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        404: {"description": "Camera not configured"},
        500: {"description": "Could not fetch depth data"},
    },
)
async def capture_depth_image(
    result_type: ImageCaptureResultOption,
    camera_path: CameraPath,
    camera_service: CameraCaptureServiceDep,
    resolution: ImageResolution = Depends(),
    near: float = Query(1e-5, description="Near clipping plane value."),
    far: float = Query(100.0, description="Far clipping plane value."),
) -> list[list[float]]:
    """
    Retrieves depth (distance) data from the captured image.
    """
    try:
        if result_type == "json":
            return await camera_service.get_distance(camera_path, resolution.tuple)

        return to_png_response(
            await camera_service.get_distance_image(
                camera_path, resolution.tuple, near, far
            )
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unable to capture depth image: {e}",
        )


@cameras_router.get(
    path="/capture/pointcloud",
    response_model=PointCloud,
    operation_id="capture_pointcloud",
    responses={
        200: {"description": "Successfully fetched point cloud data"},
        404: {"description": "Camera not configured"},
        500: {"description": "Could not fetch point cloud data"},
    },
)
async def capture_pointcloud(
    camera_path: CameraPath,
    camera_service: CameraCaptureServiceDep,
    downscale_factor: float = Query(
        1.0,
        description="Downscale factor for the point cloud. "
        "1.0 = no compression, 0.1 = heavy compression",
    ),
    resolution: ImageResolution = Depends(),
) -> PointCloud:
    """
    Retrieves a point cloud from the camera's captured image.

    - Resulting data format depends on stage units (e.g., mm, cm, m).
    - `downscale_factor` determines compression level: 1.0 = full data, <1.0 = compressed.
    """
    try:
        return await camera_service.get_pointcloud(
            camera_path, resolution.tuple, downscale_factor
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unable to capture point cloud data: {e}",
        )


@cameras_router.get(
    path="/capture/bounding-box-2d",
    operation_id="capture_boundingbox_2d",
    responses={
        200: {
            "description": "Successfully fetched 2D bounding box data",
            "content": {
                "application/json": {},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        404: {
            "description": "Camera not configured",
        },
        500: {"description": "Could not fetch 2D bounding box data"},
    },
)
async def capture_boundingbox_2d(
    result_type: ImageCaptureResultOption,
    camera_path: CameraPath,
    camera_service: CameraCaptureServiceDep,
    object_class: list[str] = Query(
        None,
        description="Classes of objects to include in the 2D bounding box output. "
        "If not specified, returns all labeled instances.",
    ),
    resolution: ImageResolution = Depends(),
) -> list[BoundingBox2D]:
    """
    Retrieves 2D bounding box data for specified object classes from the scene.
    Use the `set_semantic_label` endpoint to assign labels to scene entities.
    """
    try:
        object_class = object_class or ["all"]
        if result_type == "json":
            return await camera_service.get_bounding_boxes(
                camera_path,
                box_type="2D",
                labels=object_class,
                resolution=resolution.tuple,
            )

        return to_png_response(
            await camera_service.get_bounding_boxes_image(
                camera_path,
                box_type="2D",
                labels=object_class,
                resolution=resolution.tuple,
            )
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unable to capture 2D bounding boxes: {e}",
        )


@cameras_router.get(
    path="/capture/bounding-box-3d",
    operation_id="capture_boundingbox_3d",
    response_model=list[BoundingBox3D],
    responses={
        200: {
            "description": "Successfully fetched 3D bounding box data",
            "content": {"application/json": {}},
        },
        404: {"description": "Camera not configured"},
        500: {"description": "Could not fetch 3D bounding box data"},
    },
)
async def capture_boundingbox_3d(
    camera_path: CameraPath,
    camera_service: CameraCaptureServiceDep,
    object_class: list[str] = Query(
        None,
        description="Classes of objects to include in the 3D bounding box output. If not specified, returns all labeled instances.",
    ),
    resolution: ImageResolution = Depends(),
) -> list[BoundingBox3D]:
    """
    Retrieves 3D bounding box data for specified object classes from the scene.
    Use the `set_semantic_label` endpoint to assign labels to scene entities.
    """
    try:
        object_class = object_class or ["all"]
        bbox_3ds = await camera_service.get_bounding_boxes(
            camera_path,
            box_type="3D",
            labels=object_class,
            resolution=resolution.tuple,
        )
        return bbox_3ds
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unable to capture 3D bounding boxes: {e}",
        )


@cameras_router.get(
    path="/capture/instance-segmentation",
    operation_id="capture_instance_segmentation",
    responses={
        200: {
            "description": "Successfully fetched instance segmentation data",
            "content": {
                "application/json": {},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        404: {"description": "Camera not configured"},
        500: {"description": "Could not fetch segmentation data"},
    },
)
async def capture_instance_segmentation(
    result_type: ImageCaptureResultOption,
    camera_path: CameraPath,
    camera_service: CameraCaptureServiceDep,
    object_class: list[str] = Query(
        None,
        description="Classes of objects to include in segmentation. If not specified, returns all labeled instances.",
    ),
    resolution: ImageResolution = Depends(),
) -> InstanceSegmentationData:
    """
    Retrieves instance segmentation data for specified object classes, with each detected object uniquely identified.
    Use the `set_semantic_label` endpoint to assign labels to scene entities.
    """
    try:
        object_class = object_class or ["all"]
        if result_type == "json":
            return await camera_service.get_segmentation_data(
                camera_path,
                resolution.tuple,
                segmentation_type="instance",
                labels=object_class,
            )

        return to_png_response(
            await camera_service.get_segmentation_image(
                camera_path,
                resolution.tuple,
                segmentation_type="instance",
                labels=object_class,
            )
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unable to capture instance segmentation data: {e}",
        )


@cameras_router.get(
    path="/capture/semantic-segmentation",
    operation_id="capture_semantic_segmentation",
    responses={
        200: {
            "description": "Successfully fetched semantic segmentation data",
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        404: {"description": "Camera not configured"},
        500: {"description": "Could not fetch segmentation data"},
    },
)
async def capture_semantic_segmentation(
    result_type: ImageCaptureResultOption,
    camera_path: CameraPath,
    camera_service: CameraCaptureServiceDep,
    object_class: list[str] = Query(
        None,
        description="The class of the object for which the data is to be fetched. If no labels are set in the scene, all bounding boxes could be returned.",
    ),
    resolution: ImageResolution = Depends(),
) -> SemanticSegmentationData:
    """
    Retrieves semantic segmentation data for specified object classes, with each detected entity uniquely labeled.
    If no class is specified, data for all labeled objects in the scene is returned.

    Use the `set_semantic_label` endpoint to assign labels to scene entities.
    """
    try:
        object_class = object_class or ["all"]

        if result_type == "json":
            return await camera_service.get_segmentation_data(
                camera_path,
                resolution.tuple,
                segmentation_type="semantic",
                labels=object_class,
            )

        return to_png_response(
            await camera_service.get_segmentation_image(
                camera_path,
                resolution.tuple,
                segmentation_type="semantic",
                labels=object_class,
            )
        )
    except Exception as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Unable to capture semantic segmentation data: {e}",
        )
