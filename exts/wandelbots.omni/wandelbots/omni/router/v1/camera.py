import io
import json
from typing import Dict, Union, Any
import numpy as np
from PIL import Image
import carb
from fastapi.exceptions import HTTPException
from fastapi import Response, status, File, UploadFile, Query

import omni.syntheticdata
from omni.isaac.sensor import Camera
from pxr import Gf
import omni.replicator.core as rep
from omni.syntheticdata import SyntheticData
from omni.replicator.core.scripts.writers_default.tools import (
    colorize_distance,
    colorize_normals,
)
from scipy.spatial.transform import Rotation as R

from wandelbots.omni.utils.prim_utils import PrimUtils
from wandelbots.omni.utils.synthetic_data import SyntheticDataUtils
from wandelbots.omni.utils.scene import SceneUtils
from wandelbots.omni.datatypes import SYNTHETIC_DATA_CAPTURE_TYPES

from fastapi import APIRouter

camera_router = APIRouter(prefix="/camera", tags=["camera"])


async def get_active_viewport() -> omni.kit.viewport:
    """
    Fetches the active viewport/camera defined in the omniverse scene

    Returns:
        omni kit viewport object

    """
    viewport = omni.kit.viewport.utility.get_active_viewport()
    if not viewport:
        raise ValueError("No active viewport found")

    return viewport

async def capture_synthetic_data(
        capture_type: SYNTHETIC_DATA_CAPTURE_TYPES,
        camera_path: str = None,
        resolution: tuple[int, int] = None,
) -> Union[np.ndarray, Dict]:
    """
    Given a type, captures synthetic data in omniverse
    Args:
        capture_type: one of the capture types in ["LdrColor", "normals", "distance_to_camera", "pointcloud",
        "bounding_box_2d_tight", "bounding_box_3d", "instance_segmentation", "semantic_segmentation"]

    Returns:
        corresponding synthetic data capture as numpy array or dict
    """
    try:
        timeline, was_playing = await SceneUtils.check_simulation()
        viewport = await get_active_viewport()

        if resolution is None:
            resolution = viewport.resolution
            carb.log_warn(f"No resolution given. Falling back to viewport resolution {resolution}")

        carb.log_info(f"Capturing {capture_type} data from camera {camera_path}")
        render_product = rep.create.render_product(
            camera_path, viewport.resolution
        )
        annotator = rep.AnnotatorRegistry.get_annotator(capture_type)
        annotator.attach([render_product])
        await rep.orchestrator.step_async()
        capture_data = annotator.get_data()
        annotator.detach()
        return capture_data
    except Exception as e:
        raise HTTPException(500, f"Unable to capture synthetic data: {str(e)}")
    finally:
        if was_playing:
            timeline.play()

@camera_router.post(
    path="/reset_view",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="reset_view",
    response_model=None,
)
async def reset_view() -> None:
    """
        Resets the view to perspective view.
        
        Returns:
            None

        Raises:
            HTTPException: If unable to reset the view.
    """
    try:
        viewport = await get_active_viewport()
        viewport.camera_path = "/OmniverseKit_Persp"
    except Exception as e:
        raise HTTPException(404, "Unable to reset viewport to default camera") from e


@camera_router.get(
    path="/get_all_cameras", operation_id="get_all_cameras", response_model=list[str]
)
async def get_all_cameras() -> list[str]:
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
    path="/get_active_camera", operation_id="get_active_camera", response_model=str
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
        viewport = await get_active_viewport()
        return viewport.camera_path.pathString
    except Exception as e:
        raise HTTPException(404, "Unable to fetch active camera") from e
    


@camera_router.post(
    path="/set_active_camera",
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
        viewport = await get_active_viewport()
        viewport.camera_path = camera_path
    except ValueError as e:
        raise HTTPException(404, f"Unable to set active camera to {camera_path}") from e


@camera_router.get(
    path="/get_camera_params",
    operation_id="get_camera_params",
    response_model=dict[str, Any],
)
async def get_camera_params() -> dict[str, Any]:
    """
        Fetches the camera parameters for the active camera.
        
        Returns:
            dict: A dictionary containing the camera parameters.

        Raises:
            HTTPException: If unable to fetch the camera parameters.
    """
    try:
        viewport = omni.kit.viewport.utility.get_active_viewport()
        if not viewport:
            raise HTTPException(404, detail="No active viewport found")

        rp = rep.create.render_product(viewport.camera_path.pathString, viewport.resolution)
        cam_params_annot = rep.annotators.get("CameraParams")
        cam_params_annot.attach([rp])

        cam_params = {}
        await rep.orchestrator.step_async()
        params = cam_params_annot.get_data()
        for key, val in params.items():
            if isinstance(val, np.ndarray):
                params[key] = val.tolist()

        focal_length = params["cameraFocalLength"]
        horiz_aperture, vert_aperture = params["cameraAperture"]
        height, width = params["renderProductResolution"]
        focal_x = height * focal_length / vert_aperture
        focal_y = width * focal_length / horiz_aperture
        center_x = height * 0.5
        center_y = width * 0.5
        camera_matrix = [[1, focal_x, center_x], [0, focal_y, center_y], [0, 0, 1]]

        cam_params["resolution"] = params["renderProductResolution"]
        cam_params["cameraMatrix"] = camera_matrix
        cam_params["cameraModel"] = params["cameraModel"]
        cam_params["clippingRange"] = params["cameraNearFar"]
        cam_params["focusDistance"] = params["cameraFocusDistance"]
        cam_params["fStop"] = params["cameraFStop"]
        cam_params["cameraProjection"] = params["cameraProjection"]
        cam_params["cameraViewTransform"] = params["cameraViewTransform"]
        if params["cameraModel"] == "fisheyePolynomial":
            cam_params["fishEyeProperties"] = {
                "cameraFisheyeLensP": params["cameraFisheyeLensP"],
                "cameraFisheyeLensS": params["cameraFisheyeLensS"],
                "cameraFisheyeMaxFOV": params["cameraFisheyeLensP"],
                "cameraFisheyeNominalHeight": params["cameraFisheyeLensP"],
                "cameraFisheyeNominalWidth": params["cameraFisheyeLensP"],
                "cameraFisheyeOpticalCentre": params["cameraFisheyeLensP"],
                "cameraFisheyePolynomial": params["cameraFisheyePolynomial"],
            }

        return cam_params
    except Exception as e:
        raise HTTPException(404, "Unable to fetch camera parameters") from e


@camera_router.post(
    path="/set_camera_params",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_camera_params",
    response_model=None,
)
async def set_camera_params(upload_file: UploadFile = File(...)) -> None:
    """
        Sets the camera parameters as per the configuration file given.
        
        Args:
            upload_file: The file which has the camera configuration to be set.

        Returns:
            None

        Raises:
            HTTPException: If unable to set the camera parameters.
    """
    viewport = await get_active_viewport()
    stage = omni.usd.get_context().get_stage()
    camera = stage.GetPrimAtPath(viewport.get_active_camera().pathString)
    cam_object = Camera(viewport.get_active_camera().pathString)
    camera_params = json.load(upload_file.file)

    for key, val in camera_params.items():
        try:
            if key == "cameraModel":
                if val not in ["pinhole", "fisheyePolynomial"]:
                    raise ValueError(
                        "Invalid camera projection type in the given configuration"
                    )
                cam_object.set_projection_type(val)
            elif key == "resolution":
                viewport.resolution = val
            elif key == "cameraMatrix":
                ((fx, _, cx), (_, fy, cy), (_, _, _)) = val
                if (
                    "pixelSize" not in camera_params
                    or "resolution" not in camera_params
                ):
                    raise ValueError(
                        "pixelSize is required for setting camera aperture and focal length"
                    )
                pixel_size = camera_params["pixelSize"]
                width, height = camera_params["resolution"]
                horizontal_aperture = pixel_size * width
                vertical_aperture = pixel_size * height
                focal_length_x = fx * pixel_size
                focal_length_y = fy * pixel_size
                focal_length = (focal_length_x + focal_length_y) / 2

                camera.GetAttribute("focalLength").Set(focal_length)
                camera.GetAttribute("horizontalAperture").Set(horizontal_aperture)
                camera.GetAttribute("verticalAperture").Set(vertical_aperture)
                if camera_params["cameraModel"] == "fisheyePolynomial":
                    diagonal_fov = (
                        camera_params["fishEyeProperties"]["diagonalFov"]
                        if "diagonalFov" in camera_params["fishEyeProperties"]
                        else 180
                    )
                    distortion_coefficients = (
                        camera_params["fishEyeProperties"]["distortionCoefficients"]
                        if "distortionCoefficients" in camera_params
                        else [0.0, 0.0, 0.0, 0.0]
                    )
                    cam_object.set_fisheye_polynomial_properties(
                        width, height, cx, cy, diagonal_fov, distortion_coefficients
                    )

            elif key == "focusDistance" or key == "fstop":
                camera.GetAttribute(key).Set(val)
            elif key == "clippingRange":
                camera.GetAttribute("clippingRange").Set(Gf.Vec2f(val))
            else:
                pass

        except ValueError as e:
            raise HTTPException(404, "Input configuration file is not valid") from e


@camera_router.get(path="/get_color", operation_id="get_color", response_model=None)
async def get_color(camera_path: str = None, resolution: tuple[int, int] = None) -> Response:
    """
        Fetches the color image from the active camera point of view.
        
        Returns:
            Response: An RGB image in JPEG format or raw data.

        Raises:
            HTTPException: If unable to capture the color image.
    """
    try:
        color = await capture_synthetic_data(camera_path=camera_path,
                                             resolution=resolution,
                                             capture_type="LdrColor")
        
        if color.size == 0:
            raise ValueError("Color image is empty")
        
        bytes_io = io.BytesIO()
        img_base64 = Image.fromarray(color).convert("RGB")
        img_base64.save(bytes_io, format="jpeg")
        
        # Check if bytes_io has data
        if bytes_io.getbuffer().nbytes == 0:
            raise ValueError("Bytes IO is empty")

        return Response(content=bytes_io.getvalue(), media_type="image/jpeg")

    except Exception as e:
        raise HTTPException(404, f"Unable to capture synthetic image: {e}") from e
    


#
#
@camera_router.get(path="/get_normals", response_model=None, operation_id="get_normals")
async def get_normals(camera_path: str = None, colorize: bool = False, resolution: tuple[int, int] = None) -> Union[str, Response]:
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
        normals = await capture_synthetic_data(camera_path=camera_path,
                                               resolution=resolution,
                                               capture_type="normals")

        if colorize:
            normals_data = colorize_normals(normals)
            bytes_io = io.BytesIO()
            img_base64 = Image.fromarray(normals_data).convert("RGB")
            img_base64.save(bytes_io, format="jpeg")
            return Response(content=bytes_io.getvalue(), media_type="image/jpeg")
        else:
            return json.dumps(normals.tolist())
        
    except Exception as e:
        raise HTTPException(404, "Unable to capture normals") from e


@camera_router.get(path="/get_depth", response_model=None, operation_id="get_depth")
async def get_depth(camera_path: str = None, colorize: bool = False, resolution: tuple[int, int] = None, near: float = 1e-05, far=100, image_format: str = "jpeg") -> Union[str, Response]:
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
        depth = await capture_synthetic_data(camera_path=camera_path,
                                             resolution=resolution,
                                             capture_type="distance_to_camera")

        if colorize:
            distance_data = colorize_distance(depth, near=near, far=far)
            bytes_io = io.BytesIO()
            img_base64 = Image.fromarray(distance_data).convert("RGB")
            img_base64.save(bytes_io, format=image_format)
            return Response(content=bytes_io.getvalue(), media_type=f"image/{image_format}")
        else:
            return json.dumps(depth.tolist())
        
    except Exception as e:
        raise HTTPException(404, "Unable to capture synthetic depth image") from e


@camera_router.get(
    path="/get_pointcloud", operation_id="get_pointcloud", response_model=str
)
async def get_pointcloud(camera_path: str = None, downscale_factor: float = 1.0, resolution: tuple[int, int] = None) -> str:
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

    async def get_camera_tfm(camera_path: str) -> np.ndarray:
        pose = PrimUtils.get_pose(camera_path, coordinate_system="world")
        tfm = np.eye(4)
        tfm[:3, :3] = R.from_rotvec(pose.pose[3:]).as_matrix()
        tfm[:3, -1] = pose.pose[:3]
        tfm[:3, -1] = tfm[:3, -1]
        return np.linalg.inv(tfm)

    def transform_points(points3d, tfm) -> np.ndarray:
        points4d = np.hstack([points3d, np.ones((points3d.shape[0], 1))])
        points4d = np.dot(points4d, tfm.T)
        return points4d[:, :3]

    def transform_normals(normals3d, tfm) -> np.ndarray:
        return np.dot(normals3d, tfm[:3, :3].T)

    if not 0.001 <= downscale_factor <= 1.0:
        raise HTTPException(
            status_code=400, detail="Downscale factor should be between 0.001 and 1"
        )

    try:
        pc_data = await capture_synthetic_data(camera_path=camera_path,
                                               resolution=resolution,
                                               capture_type="pointcloud")

        if "data" not in pc_data:
            raise HTTPException(
                404,
                """
                Unable to capture point cloud data. No data found in the captured image. 
                Check if semantic labels are assigned to the objects of interest.
                """,
            )
        
        if "info" not in pc_data:
            raise HTTPException(
                404,
                """
                Unable to capture point cloud data. No info found in the captured image.
                Check if semantic labels are assigned to the objects of interest.
                """,
            )
        
        if "pointRgb" not in pc_data["info"]:
            raise HTTPException(
                404,
                """
                Unable to capture point cloud data. No RGB data found in the captured image.
                Check if semantic labels are assigned to the objects of interest.
                """,
            )
        
        if "pointNormals" not in pc_data["info"]:
            raise HTTPException(
                404,
                """
                Unable to capture point cloud data. No normals data found in the captured image.
                Check if semantic labels are assigned to the objects of interest.
                """,
            )

        points = pc_data["data"] * 1000
        if points.size == 0:
            raise HTTPException(
                404,
                """
                Unable to capture point cloud data. No points found in the captured image.
                Check if semantic labels are assigned to the objects of interest.
                """,
            )
        
        colors = pc_data["info"]["pointRgb"].reshape(-1, 4)[:, :3]
        normals = pc_data["info"]["pointNormals"].reshape(-1, 4)[:, :3]

        if downscale_factor != 1:
            points, colors, normals = SyntheticDataUtils.downscale_point_cloud(
                points, colors, normals, downscale_factor
            )

        world_to_cam = await get_camera_tfm(camera_path)
        points = transform_points(points, world_to_cam)
        normals = transform_normals(normals, world_to_cam)
        point_cloud_data = {
            "points": points.tolist(),
            "colors": colors.tolist(),
            "normals": normals.tolist(),
        }

        return json.dumps(point_cloud_data)
    except Exception as e:
        raise HTTPException(404, "Unable to capture point cloud data") from e


@camera_router.get(path="/get_bbox_2d", response_model=None, operation_id="get_bbox_2d")
async def get_bbox_2d(
    camera_path: str = None,
    object_class: list[str] = Query(default=["all"]), colorize: bool = False, resolution: tuple[int, int] = None,
) -> Union[str, Response]:
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
        SyntheticData.Get().set_instance_mapping_semantic_filter(
            SyntheticDataUtils.get_semantic_predicate(object_class)
        )
        bbox_2ds = await capture_synthetic_data(camera_path=camera_path,
                                                resolution=resolution,
                                                capture_type="bounding_box_2d_tight")

        if colorize:
            color = await capture_synthetic_data(camera_path=camera_path,
                                                 resolution=resolution,
                                                 capture_type="LdrColor")
            color = Image.fromarray(color).convert("RGB")
            bbox_image = SyntheticDataUtils.colorize_2d_bounding_boxes(color, bbox_2ds)
            bytes_io = io.BytesIO()
            bbox_image.save(bytes_io, format="jpeg")
            return Response(content=bytes_io.getvalue(), media_type="image/jpeg")

        else:
            for key, val in bbox_2ds.items():
                if key == "data":
                    bbox_2ds[key] = [each.tolist() for each in val]
                elif key == "info":
                    for each in val:
                        val[each] = (
                            val[each].tolist()
                            if isinstance(val[each], np.ndarray)
                            else val[each]
                        )
                else:
                    pass

            return json.dumps(bbox_2ds)
    except Exception as e:
        raise HTTPException(404, "Unable to capture bounding boxes") from e


@camera_router.get(path="/get_bbox_3d", response_model=None, operation_id="get_bbox_3d")
async def get_bbox_3d(camera_path: str = None,
    object_class: list[str] = Query(default=["all"]), colorize: bool = False, resolution: tuple[int, int] = None,
) -> Union[str, Response]:
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
        SyntheticData.Get().set_instance_mapping_semantic_filter(
            SyntheticDataUtils.get_semantic_predicate(object_class)
        )
        bbox_3ds = await capture_synthetic_data(camera_path=camera_path,
                                                resolution=resolution,
                                                capture_type="bounding_box_3d")

    except Exception as e:
        raise HTTPException(404, "Unable to capture 3D bounding box") from e

    if colorize:
        try:
            color = await capture_synthetic_data(camera_path=camera_path,
                                                 resolution=resolution,
                                                 capture_type="LdrColor")
        except Exception as e:
            raise HTTPException(404, "Unable to capture synthetic image") from e

        color = Image.fromarray(color).convert("RGB")
        camera_params = await get_camera_params()
        bbox_image = SyntheticDataUtils.colorize_3d_bounding_boxes(
            color, bbox_3ds, camera_params
        )
        bytes_io = io.BytesIO()
        bbox_image.save(bytes_io, format="jpeg")
        return Response(content=bytes_io.getvalue(), media_type="image/jpeg")
    else:
        bbox_3ds["transforms"] = bbox_3ds["data"]["transform"].tolist()
        for key, val in bbox_3ds.items():
            if key == "data":
                bbox_3ds_data = np.asarray(bbox_3ds["data"].tolist(), dtype="object")
                bbox_3ds["data"] = np.hstack(
                    [bbox_3ds_data[:, :7], bbox_3ds_data[:, 8:]]
                ).tolist()
            elif key == "info":
                for each in val:
                    val[each] = (
                        val[each].tolist()
                        if isinstance(val[each], np.ndarray)
                        else val[each]
                    )
            else:
                pass

        return json.dumps(bbox_3ds)


@camera_router.get(
    path="/get_instance_segmentation",
    response_model=None,
    operation_id="get_instance_segmentation",
)
async def get_instance_segmentation(camera_path: str = None,
    object_class: list[str] = Query(default=["all"]), colorize: bool = False, resolution: tuple[int, int] = None,
) -> Union[str, Response]:
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
        SyntheticData.Get().set_instance_mapping_semantic_filter(
            SyntheticDataUtils.get_semantic_predicate(object_class)
        )
        segmented_data = await capture_synthetic_data(camera_path=camera_path,
                                                      resolution=resolution,
                                                      capture_type="instance_segmentation")

        if colorize:
            if "data" not in segmented_data:
                raise HTTPException(
                    404,
                    """
                    Unable to capture instance segmentation data. No data found in the captured image.
                    Check if semantic labels are assigned to the objects of interest.
                    """,
                )

            colored_segmented_data = SyntheticDataUtils.colorize_segmented_data(
                segmented_data["data"]
            )
            img_base64 = Image.fromarray(colored_segmented_data)
            bytes_io = io.BytesIO()
            img_base64.save(bytes_io, format="jpeg")
            return Response(content=bytes_io.getvalue(), media_type="image/jpeg")
        
        for key, val in segmented_data.items():
            if key == "data":
                segmented_data[key] = val.tolist()
            elif key == "info":
                for each in val:
                    val[each] = (
                        val[each].tolist()
                        if isinstance(val[each], np.ndarray)
                        else val[each]
                    )
            else:
                pass

        return json.dumps(segmented_data)
    
    except Exception as e:
        raise HTTPException(404, "Unable to capture synthetic data") from e


@camera_router.get(
    path="/get_semantic_segmentation",
    response_model=None,
    operation_id="get_semantic_segmentation",
)
async def get_semantic_segmentation(camera_path: str = None,
    object_class: list[str] = Query(default=["all"]), colorize: bool = False, resolution: tuple[int, int] = None,
) -> Union[str, Response]:
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
        SyntheticData.Get().set_instance_mapping_semantic_filter(
            SyntheticDataUtils.get_semantic_predicate(object_class)
        )
        segmented_data = await capture_synthetic_data(camera_path=camera_path,
                                                      resolution=resolution,
                                                      capture_type="semantic_segmentation")
    except Exception as e:
        raise HTTPException(404, "Unable to capture synthetic data") from e

    if colorize:
        colored_segmented_data = SyntheticDataUtils.colorize_segmented_data(
            segmented_data["data"]
        )
        img_base64 = Image.fromarray(colored_segmented_data)
        bytes_io = io.BytesIO()
        img_base64.save(bytes_io, format="jpeg")
        return Response(content=bytes_io.getvalue(), media_type="image/jpeg")
    else:
        for key, val in segmented_data.items():
            if key == "data":
                segmented_data[key] = val.tolist()
            elif key == "info":
                for each in val:
                    val[each] = (
                        val[each].tolist()
                        if isinstance(val[each], np.ndarray)
                        else val[each]
                    )
            else:
                pass

        return json.dumps(segmented_data)
