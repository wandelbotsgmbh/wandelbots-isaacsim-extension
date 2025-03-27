from dataclasses import dataclass
from typing import Literal, Union, Optional

import carb
import numpy as np
import omni.isaac.core.utils.stage as stage_utils
import omni.replicator.core as rep
from PIL import Image

from omni.syntheticdata import SyntheticData
from omni.isaac.sensor import Camera
import omni.isaac.core.utils.prims as prims_utils

from pydantic import confloat, ValidationError
from wandelbots.omni.utils.scene import SceneUtils
from wandelbots.omni.utils.synthetic_data import SyntheticDataUtils

from wandelbots.omni.datatypes import (VirtualCameraConfiguration, SYNTHETIC_DATA_CAPTURE_TYPES, PointCloud, CameraParams,
                                       BoundingBox2D, BoundingBox3D, SemanticSegmentationData, InstanceSegmentationData)


@dataclass
class ConfigurableCamera:
    def __init__(self, configuration: VirtualCameraConfiguration):
        super().__init__()
        self._configuration = configuration
        self.cam_path = self._configuration.prim_path
        self._validate()

    async def _capture_synthetic_data(self, capture_type: SYNTHETIC_DATA_CAPTURE_TYPES):
        timeline, was_playing = await SceneUtils.check_simulation()

        try:
            carb.log_info(f"Capturing {capture_type} data from camera {self._configuration.identifier}")
            render_product = rep.create.render_product(self.cam_path, self._configuration.cam_params.resolution)
            annotator = rep.AnnotatorRegistry.get_annotator(capture_type)
            annotator.attach([render_product])
            await rep.orchestrator.step_async()
            capture_data = annotator.get_data()
            annotator.detach()
            return capture_data
        except Exception as e:
            raise Exception(500, f"Unable to capture synthetic data: {str(e)}")
        finally:
            if was_playing:
                timeline.play()

    async def get_camera_params(self) -> CameraParams:
        try:
            # get projection and transformation matrices
            camera = Camera(self._configuration.prim_path)
            camera.initialize()
            camera_model = camera.get_projection_type()
            width, height = camera.get_resolution()
            camera_matrix = camera.get_intrinsics_matrix()
            transformation_matrix = camera.get_view_matrix_ros()
            projection_matrix = np.vstack([np.matmul(camera_matrix, transformation_matrix[:3, :]),
                                           np.array([0, 0, 0, 1])
                                           ])

            camera_data = {
                "cameraModel": camera_model,
                "resolution": [width, height],
                "focalLength": camera.get_focal_length(),
                "horizontalAperture": camera.get_horizontal_aperture(),
                "verticalAperture": camera.get_vertical_aperture(),
                "focusDistance": camera.get_focus_distance(),
                "fStop": camera.get_lens_aperture(),
                "clippingRange": camera.get_clipping_range(),
                "cameraIntrinsics": camera_matrix.tolist(),
                "cameraProjection": projection_matrix.tolist(),
                "cameraViewTransform": transformation_matrix.tolist(),
            }

            if camera_model == "fisheyePolynomial":
                fisheye_properties = camera.get_fisheye_polynomial_properties()
                camera_data["fishEyeProperties"] = {
                    "nominal_width": fisheye_properties[0],
                    "nominal_height": fisheye_properties[1],
                    "optical_centre_x": fisheye_properties[2],
                    "optical_centre_y": fisheye_properties[3],
                    "diagonalFov": fisheye_properties[4],
                    "distortionCoefficients": fisheye_properties[5],
                }

            self._configuration.cam_params = CameraParams(**camera_data)
            return CameraParams.model_validate(camera_data)

        except Exception as e:
            raise ValueError(f"Invalid configuration for camera parameters: {str(e)}")

    async def set_camera_params(self, camera_params: CameraParams) -> None:
        try:
            camera = Camera(self._configuration.prim_path)
            camera.initialize()
            camera_params_default = await self.get_camera_params()
            camera_model = camera_params.cameraModel or camera_params_default.cameraModel
            if camera_model not in ["pinhole", "fisheyePolynomial"]:
                raise ValueError("Invalid camera projection type in the given configuration")

            camera.set_projection_type(camera_model)
            resolution = camera_params.resolution
            camera.set_resolution((resolution[1], resolution[0]))

            # Compute aperture and focal length
            focal_length = camera_params.focalLength or camera_params_default.focalLength
            vertical_aperture = camera_params.verticalAperture or camera_params_default.verticalAperture
            horizontal_aperture = camera_params.horizontalAperture or camera_params_default.horizontalAperture

            # Apply camera settings
            camera.set_focal_length(focal_length)
            camera.set_horizontal_aperture(horizontal_aperture)
            camera.set_vertical_aperture(vertical_aperture)
            camera.set_focus_distance(camera_params.focusDistance or camera_params_default.focusDistance)
            camera.set_lens_aperture(camera_params.fStop or camera_params_default.fStop)

            clipping_range = camera_params.clippingRange or camera_params_default.clippingRange
            camera.set_clipping_range(clipping_range[0], clipping_range[1])

            # Handle fisheye properties if applicable
            if camera_model == "fisheyePolynomial":
                fish_eye_params = camera_params.fishEyeProperties
                default_fisheye_params = camera_params_default.fishEyeProperties
                camera.set_fisheye_polynomial_properties(
                    fish_eye_params.nominal_width or default_fisheye_params.nominal_width,
                    fish_eye_params.nominal_height or default_fisheye_params.nominal_height,
                    fish_eye_params.optical_centre_x or default_fisheye_params.optical_centre_x,
                    fish_eye_params.optical_centre_y or default_fisheye_params.optical_centre_y,
                    fish_eye_params.cameraFisheyeMaxFOV or default_fisheye_params.cameraFisheyeMaxFOV,
                    fish_eye_params.cameraFisheyePolynomial or default_fisheye_params.cameraFisheyePolynomial
                )

        except Exception as e:
            raise ValueError(f"Failed to set camera parameters: {str(e)}")

    def _validate(self):
        stage_prims = [prim.GetPrimPath().pathString for prim in stage_utils.traverse_stage()]
        if self.cam_path not in stage_prims:
            raise ValidationError(
                f"Given {self.cam_path} is not a valid prim path in the stage for {self._configuration.identifier}"
            )
        camera_prim = prims_utils.get_prim_at_path(self._configuration.prim_path)
        if camera_prim.GetTypeName() != "Camera":
            raise ValidationError(f"Given prim path {self._configuration.prim_path} is not a camera prim")


    async def get_image(self) -> Image:
        color = await self._capture_synthetic_data(capture_type="LdrColor")
        image = Image.fromarray(color).convert("RGB")
        return image

    async def get_distance(self) -> list[float]:
        distance = await self._capture_synthetic_data(capture_type="distance_to_camera")
        return distance.tolist()

    async def get_normals(self) -> list[float]:
        normals = await self._capture_synthetic_data(capture_type="normals")
        return normals.tolist()

    async def get_pointcloud(self, downscale_factor: confloat(ge=0.001, le=1) = 1) -> PointCloud:
        pc_data = await self._capture_synthetic_data(capture_type="pointcloud")
        if "data" not in pc_data or "info" not in pc_data:
            raise ValueError("No objects have semantic labels set in the camera field of view. "
                             "Set semantic label for atleast one object of interest to capture point cloud data")

        points = pc_data["data"] * 1000
        colors = pc_data["info"]["pointRgb"].reshape(-1, 4)[:, :3]
        normals = pc_data["info"]["pointNormals"].reshape(-1, 4)[:, :3]

        if downscale_factor!=1:
            points, colors, normals = SyntheticDataUtils.downscale_point_cloud(points, colors, normals, downscale_factor)

        world_to_cam = await SyntheticDataUtils.get_camera_tfm(self.cam_path)
        points = SyntheticDataUtils.transform_points(points, world_to_cam)
        normals = SyntheticDataUtils.transform_normals(normals, world_to_cam)
        point_cloud_data = PointCloud(points=points.tolist(),
                                      colors= colors.tolist(),
                                      normals=normals.tolist()
                                      )
        return point_cloud_data


    async def get_bounding_boxes(self, box_type:Literal["2D", "3D"], labels: list[str]) -> Union[list[BoundingBox2D], list[BoundingBox3D]]:
        bbox_capture_type = {"2D": "bounding_box_2d_tight",
                             "3D": "bounding_box_3d"}[box_type]
        SyntheticData.Get().set_instance_mapping_semantic_filter(SyntheticDataUtils.get_semantic_predicate(labels))
        bbox_data = await self._capture_synthetic_data(capture_type=bbox_capture_type)

        if bbox_data is None or "data" not in bbox_data or bbox_data["data"].size == 0:
            return []

        id_to_labels = bbox_data["info"]["idToLabels"]
        prim_paths = bbox_data["info"]["primPaths"]
        bounding_boxes = []

        for bbox, prim_path in zip(bbox_data["data"], prim_paths):
            semantic_id = str(bbox['semanticId'])
            label = id_to_labels[semantic_id]["class"]

            if box_type == "2D":
                bounding_boxes.append(BoundingBox2D(
                    label=label,
                    bbox=(bbox['x_min'], bbox['y_min'], bbox['x_max'], bbox['y_max']),
                    prim_path=prim_path,
                    semantic_id=semantic_id
                ))
            else:
                bounding_boxes.append(BoundingBox3D(
                    label=label,
                    bbox=[bbox[f] for f in ['x_min', 'y_min', 'z_min', 'x_max', 'y_max', 'z_max']],
                    prim_path=prim_path,
                    semantic_id=semantic_id,
                    transform=bbox["transform"].tolist()
                ))

        return bounding_boxes

    async def get_segmentation_data(self, segmentation_type:Literal["semantic", "instance"], labels: Optional[list[str]]=["all"]) -> Union[SemanticSegmentationData, InstanceSegmentationData]:
        segmentation_capture_type = {"semantic": "semantic_segmentation",
                                     "instance": "instance_segmentation"}[segmentation_type]
        SyntheticData.Get().set_instance_mapping_semantic_filter(SyntheticDataUtils.get_semantic_predicate(labels))
        segmented_data = await self._capture_synthetic_data(capture_type=segmentation_capture_type)
        if not segmented_data or "data" not in segmented_data:
            raise Exception("Unable to capture segmentation data. No labels found in the scene")

        if "data" in segmented_data:
            segmented_data["data"] = segmented_data["data"].tolist() if isinstance(segmented_data["data"], np.ndarray) else segmented_data["data"]

        if "info" in segmented_data:
            segmented_data["info"] = {
                key: (val.tolist() if isinstance(val, np.ndarray) else val)
                for key, val in segmented_data["info"].items()
            }

        if segmentation_type=="instance":
            return InstanceSegmentationData(**segmented_data)

        return SemanticSegmentationData(**segmented_data)


    @property
    def configuration(self):
        return self._configuration

    @property
    def camera_params(self):
        return self._configuration.cam_params



