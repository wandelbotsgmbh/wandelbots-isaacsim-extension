import io
from typing import Union, Optional
import omni.replicator.core as rep
import numpy as np
from PIL import Image
from fastapi.exceptions import HTTPException
from fastapi import Response, status, Query

import omni.syntheticdata
from omni.replicator.core.scripts.writers_default.tools import (
    colorize_distance,
    colorize_normals,
)
from wandelbots.omni.environment import host_database
from wandelbots.omni.core.periphery import ConfigurableCamera
from wandelbots.omni.utils.synthetic_data import SyntheticDataUtils
from wandelbots.omni.datatypes import VirtualCameraConfiguration, CameraParams, PointCloud, BoundingBox2D, BoundingBox3D, SemanticSegmentationData, InstanceSegmentationData

from fastapi import APIRouter

camera_router = APIRouter(prefix="/camera", tags=["camera"])

async def fetch_camera(camera_name: str) -> ConfigurableCamera:
    """
    Fetches the camera defined
    """
    camera = host_database[f"camera.{camera_name}.instance"]
    if not camera:
        raise KeyError(f"{camera_name} is not configured yet")
    return camera

@camera_router.get(
    path="/camera-objects", operation_id="get_all_camera_prims", response_model=list[str]
)
async def get_all_camera_objects() -> list[str]:
    """
        Fetches all the cameras/viewports defined in the scene.
        
        Returns:
            List[str]: A list of camera/viewpoint paths.

        Raises:
            HTTPException: If unable to fetch the cameras/viewports.
    """
    try:
        stage = omni.usd.get_context().get_stage()
        camera_prims = [
            x.GetPrimPath().pathString
            for x in stage.Traverse()
            if x.GetTypeName() == "Camera"
        ]
        return camera_prims
    except Exception as e:
        raise HTTPException(
            404, "Unable to fetch all cameras defined in the scene"
        ) from e


@camera_router.get(
    path="/active", operation_id="get_active_camera", response_model=str
)
async def get_active_camera() -> str:
    """
        Fetches the active camera/viewport in the scene.
        
        Returns:
            str: The prim path of the current set viewport/camera.

        Raises:
            HTTPException: If unable to fetch the active camera/viewport.
    """
    try:
        viewport = omni.kit.viewport.utility.get_active_viewport()
        return viewport.camera_path.pathString
    except Exception as e:
        raise HTTPException(404, "Unable to fetch active camera viewport") from e
    


@camera_router.post(
    path="/active",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_active_camera",
    response_model=None,
)
async def set_active_camera(camera_path: str) -> None:
    """
        Sets the active camera to the given prim path.
        
        Args:
            camera_path: The prim path to set as the active camera.

        Returns:
            None

        Raises:
            HTTPException: If unable to set the active camera.
    """
    
    try:
        viewport = omni.kit.viewport.utility.get_active_viewport()
        viewport.camera_path = camera_path
    except ValueError as e:
        raise HTTPException(404, f"Unable to set active camera to {camera_path}") from e


@camera_router.post(
    path="/",
    operation_id="create_camera",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
)
async def create_camera(configuration: VirtualCameraConfiguration):
    """
    Creates camera with suitable parameters
    """
    if configuration.identifier in host_database["camera"]:
        raise HTTPException(
            404,
            f"{configuration.identifier} is already created. Please delete it first to create a new camera",
        )
    try:
        camera = ConfigurableCamera(configuration)
        await camera.set_camera_params(configuration.cam_params)
        host_database[f"camera.{configuration.identifier}.instance"] = camera
    except Exception as e:
        raise HTTPException(404, f"Unable to create camera: {str(e)}")

@camera_router.get(
    path="/{camera_name}",
    operation_id="get_camera",
    response_model=VirtualCameraConfiguration,
)
async def get_camera(camera_name: str) -> VirtualCameraConfiguration:
    """
        Fetches the camera parameters for the active camera.
        
        Returns:
            dict: A dictionary containing the camera parameters.

        Raises:
            HTTPException: If unable to fetch the camera parameters.
    """
    try:
        camera = await fetch_camera(camera_name)
        return camera.configuration
    except Exception:
        raise HTTPException(404, f"Camera {camera_name} is not configured yet")


@camera_router.put(
    path="/{camera_name}/params",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="update_camera_params",
    response_model=None,
)
async def update_camera_params(camera_name: str, camera_params: CameraParams) -> None:
    """
        Sets the camera parameters as per the configuration file given.

        Args:
            camera_params: The camera configuration to be set.

        Returns:
            None

        Raises:
            HTTPException: If unable to set the camera parameters.
    """
    try:
        camera = await fetch_camera(camera_name)
        camera.set_camera_params(camera_params)
    except Exception as e:
        raise HTTPException(404, f"Unable to set camera parameters: {str(e)}")


@camera_router.delete(
    path="/{camera_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_camera",
    response_model=None,
)
async def delete_camera(camera_name: str) -> None:
    """
    Deletes a specific camera
    Args:
        camera_name: name of the camera

    Returns:
        None
    """
    if camera_name in host_database["camera"]:
        del host_database[f"camera.{camera_name}"]
    else:
        raise HTTPException(404, f"{camera_name} is not configured yet")

@camera_router.delete(
    path="/",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_all_cameras",
    response_model=None,
)
async def delete_all_cameras() -> None:
    """
    Deletes all configured cameras
    Returns:
        None
    """
    if "camera" in host_database:
        del host_database["camera"]

@camera_router.get(
    path="/",
    operation_id="get_all_cameras",
    response_model=list[VirtualCameraConfiguration],
)
async def get_all_cameras() -> list[VirtualCameraConfiguration]:
    """
    Fetches all configured cameras
    Returns:
        None
    """
    all_cameras = []
    for each in host_database["camera"]:
        all_cameras.append(host_database[f"camera.{each}.instance"].configuration)
    return all_cameras



@camera_router.get(path="/{camera_name}/color", operation_id="get_color", response_model=None)
async def get_color(camera_name: str) -> Response:
    """
        Fetches the color image from the active camera point of view.
        
        Returns:
            Response: An RGB image in JPEG format or raw data.

        Raises:
            HTTPException: If unable to capture the color image.
    """
    try:
        camera = await fetch_camera(camera_name)
        image = await camera.get_image()
        bytes_io = io.BytesIO()
        image.save(bytes_io, format="PNG")
        return Response(content=bytes_io.getvalue(), media_type="image/png")

    except Exception as e:
        raise HTTPException(404, f"Unable to capture color image: {str(e)}")
    


@camera_router.get(path="/{camera_name}/normals", response_model=list[float], operation_id="get_normals")
async def get_normals(camera_name: str,
                      colorize: bool = False) -> Union[list[float], Response]:
    """
        Fetches the normals from the image captured using the active camera.
        
        Args:
            camera_path: The path to the camera prim.
            colorize: A bool variable which defines if the output format should be raw numerical data or an image for visualization.

        Returns:
            Union[str, Response]: Either raw numerical normals data or a color image with normals.

        Raises:
            HTTPException: If unable to capture the normals image.
    """
    try:
        camera = await fetch_camera(camera_name)
        normals = await camera.get_normals()
        if not colorize:
            return normals

        normals_data = colorize_normals(np.array(normals))
        img_base64 = Image.fromarray(normals_data).convert("RGB")
        bytes_io = io.BytesIO()
        img_base64.save(bytes_io, format="PNG")
        return Response(content=bytes_io.getvalue(), media_type="image/png")

    except Exception as e:
        raise HTTPException(404, f"Unable to capture normals:{str(e)}")


@camera_router.get(path="/{camera_name}/depth", response_model=list[float], operation_id="get_depth")
async def get_depth(camera_name: str,
                    colorize: bool = False, near: float = 1e-05, far=100) -> Union[list[float], Response]:
    """
        Fetches the absolute depth (distance) from the image captured using the active camera. Raw data can be in
        ["mm", "cm", "m"] depending on the units set in the stage.
        
        Args:
            camera_path: The path to the camera prim.
            colorize: A bool variable which defines if the output format should be raw numerical data or an image for visualization.

        Returns:
            Union[str, Response]: Either raw numerical depth data or a grayscale image with depth.

        Raises:
            HTTPException: If unable to capture the depth image.
    """
    try:
        camera = await fetch_camera(camera_name)
        distance = await camera.get_distance()
        if not colorize:
            return distance
        distance_data = colorize_distance(distance, near=near, far=far)
        bytes_io = io.BytesIO()
        img_base64 = Image.fromarray(distance_data).convert("RGB")
        img_base64.save(bytes_io, format="PNG")
        return Response(content=bytes_io.getvalue(), media_type="image/png")

    except Exception as e:
        raise HTTPException(404, f"Unable to capture synthetic depth image: {str(e)}") from e


@camera_router.get(
    path="/{camera_name}/pointcloud", operation_id="get_pointcloud", response_model=PointCloud
)
async def get_pointcloud(camera_name: str,
                         downscale_factor: float = 1.0) -> PointCloud:
    """
        Fetches the point cloud from the image captured using the active camera. Raw data can be in ["mm", "cm", "m"]
        depending on the units set in the stage.
        
        Args:
            camera_path: The path to the camera prim.
            downscale_factor: A float variable which defines the compression factor for the point cloud. Should be between
            0.001 and 1. Higher factor (e.g. 1 - all points) corresponds to a lesser compression or no compression and
            a smaller factor (e.g. 0.1) corresponds to a higher compression.

        Returns:
            str: Either raw point cloud data as per the units set in the stage.

        Raises:
            HTTPException: If unable to capture the point cloud data.
    """

    try:
        camera = await fetch_camera(camera_name)
        point_cloud = await camera.get_pointcloud(downscale_factor=downscale_factor)
        return point_cloud
    except Exception as e:
        raise HTTPException(404, f"Unable to capture point cloud data: {str(e)}")


@camera_router.get(path="/{camera_name}/bounding-box-2d", response_model=list[BoundingBox2D], operation_id="get_boundingbox_2d")
async def get_boundingbox_2d(camera_name: str,
                      object_class: Optional[list[str]] = Query(None, description="The class of the object for which the data is to be fetched. If no labels are set in the scene, all bounding boxes could be returned."),
                      colorize: bool = False) -> Union[list[BoundingBox2D], Response]:
    """
        Fetches the 2D bounding box data given the object class. If object class is not specified, segmentation data
        is fetched for all the objects with labels in the scene. Use set_semantic_label endpoint to set a semantic label.
        
        Args:
            object_class: The class of the object for which the data is to be fetched. If no labels are set in the scene,
            all bounding boxes could be returned.
            colorize: A bool variable which defines if the output format should be raw numerical data or an image for visualization.

        Returns:
            Union[str, Response]: Either raw numerical 2D bounding boxes in the image captured or a color image with 2D bounding boxes on the image.

        Raises:
            HTTPException: If unable to capture the 2D bounding box data.
    """
    try:
        camera = await fetch_camera(camera_name)
        object_class = object_class or ["all"]
        bbox_2ds = await camera.get_bounding_boxes(box_type="2D", labels=object_class)
        if not colorize:
            return bbox_2ds

        await rep.orchestrator.step_async()
        image = await camera.get_image()

        bytes_io = io.BytesIO()
        image.save(bytes_io, format="PNG")
        bbox_image = SyntheticDataUtils.colorize_2d_bounding_boxes(image, bbox_2ds)
        bytes_io = io.BytesIO()
        bbox_image.save(bytes_io, format="PNG")
        return Response(content=bytes_io.getvalue(), media_type="image/png")

    except Exception as e:
        raise HTTPException(404, f"Unable to capture 2D bounding boxes: {str(e)}") from e


@camera_router.get(path="/{camera_name}/bounding-box-3d", response_model=list[BoundingBox3D], operation_id="get_boundingbox_3d")
async def get_boundingbox_3d(camera_name: str,
                      object_class: Optional[list[str]] = Query(None, description="The class of the object for which the data is to be fetched. If no labels are set in the scene, all bounding boxes could be returned."),
                      colorize: bool = False
) -> Union[list[BoundingBox3D], Response]:
    """
        Fetches the 3D bounding box data given the object class. If object class is not specified, segmentation data
        is fetched for all the objects with labels in the scene. Use set_semantic_label endpoint to set a semantic label.
        
        Args:
            object_class: The class of the object for which the data is to be fetched. If no labels are set in the scene,
            all bounding boxes could be returned.
            colorize: A bool variable which defines if the output format should be raw numerical data or an image for visualization.

        Returns:
            Union[str, Response]: Either raw numerical 3D bounding boxes in the image captured or a color image with 3D bounding boxes on the image.

        Raises:
            HTTPException: If unable to capture the 3D bounding box data.
    """
    try:
        camera = await fetch_camera(camera_name)
        object_class = object_class or ["all"]
        bbox_3ds = await camera.get_bounding_boxes(box_type="3D", labels=object_class)
        if not colorize:
            return bbox_3ds

        await rep.orchestrator.step_async()
        image = await camera.get_image()
        camera_params = await camera.get_camera_params()
        bbox_image = SyntheticDataUtils.colorize_3d_bounding_boxes(
            image, bbox_3ds, camera_params
        )
        bytes_io = io.BytesIO()
        bbox_image.save(bytes_io, format="PNG")
        return Response(content=bytes_io.getvalue(), media_type="image/png")

    except Exception as e:
        raise HTTPException(404, f"Unable to capture 3D bounding boxes: {str(e)}")



@camera_router.get(
    path="/{camera_name}/instance-segmentation",
    response_model=InstanceSegmentationData,
    operation_id="get_instance_segmentation",
)
async def get_instance_segmentation(camera_name: str,
                                    object_class: Optional[list[str]] = Query(None,
                                                                              description="The class of the object for which the data is to be fetched. If no labels are set in the scene, all bounding boxes could be returned."),
                                    colorize: bool = False) -> Union[InstanceSegmentationData, Response]:
    """
        Fetches the instance segmentation data given the object class. If object class is not specified, segmentation data
        is fetched for all the objects with labels in the scene. Use set_semantic_label endpoint to set a semantic label.
        
        Args:
            object_class: The class of the object for which the data is to be fetched. If no labels are set in the scene,
            a blank image could be returned.
            colorize: A bool variable which defines if the output format should be raw numerical data or an image for visualization.

        Returns:
            Union[str, Response]: Either raw numerical segmentation data with labels or a color image with instance segmentation for visualization.

        Raises:
            HTTPException: If unable to capture the instance segmentation data.
    """
    try:
        camera = await fetch_camera(camera_name)
        object_class = object_class or ["all"]
        segmented_data = await camera.get_segmentation_data(segmentation_type="instance", labels=object_class)
        if not colorize:
            return segmented_data

        colored_segmented_data = SyntheticDataUtils.colorize_segmented_data(segmented_data.data)
        img_base64 = Image.fromarray(colored_segmented_data)
        bytes_io = io.BytesIO()
        img_base64.save(bytes_io, format="PNG")
        return Response(content=bytes_io.getvalue(), media_type="image/png")

    except Exception as e:
        raise HTTPException(404, f"Unable to capture synthetic data: {str(e)}")

@camera_router.get(
    path="/{camera_name}/semantic-segmentation",
    response_model=SemanticSegmentationData,
    operation_id="get_semantic_segmentation",
)
async def get_semantic_segmentation(camera_name: str,
                                    object_class: Optional[list[str]] = Query(None,
                                                                    description="The class of the object for which the data is to be fetched. If no labels are set in the scene, all bounding boxes could be returned."),
                                    colorize: bool = False) -> Union[SemanticSegmentationData, Response]:
    """
        Fetches the semantic segmentation data given the object class. If object class is not specified, segmentation data
        is fetched for all the objects with labels in the scene. Use set_semantic_label endpoint to set a semantic label.
        
        Args:
            object_class: The class of the object for which the data is to be fetched. If no labels are set in the scene,
            a blank image could be returned.
            colorize: A bool variable which defines if the output format should be raw numerical data or an image for visualization.

        Returns:
            Union[str, Response]: Either raw numerical segmentation data with labels or a color image with semantic segmentation for visualization.

        Raises:
            HTTPException: If unable to capture the semantic segmentation data.
    """
    try:
        camera = await fetch_camera(camera_name)
        object_class = object_class or ["all"]
        segmented_data = await camera.get_segmentation_data(segmentation_type="semantic", labels=object_class)

        if not colorize:
            return segmented_data

        colored_segmented_data = SyntheticDataUtils.colorize_segmented_data(segmented_data.data)
        img_base64 = Image.fromarray(colored_segmented_data)
        bytes_io = io.BytesIO()
        img_base64.save(bytes_io, format="PNG")
        return Response(content=bytes_io.getvalue(), media_type="image/png")

    except Exception as e:
        raise HTTPException(404, f"Unable to capture synthetic data: {str(e)}")
