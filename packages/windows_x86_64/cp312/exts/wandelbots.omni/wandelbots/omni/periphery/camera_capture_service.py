from typing import Literal, Union, Optional

import carb
import numpy as np

import omni.replicator.core as rep
from PIL import Image

from omni.syntheticdata import SyntheticData
from pxr import UsdGeom

import isaacsim.core.utils.stage as stage_utils

from pydantic import confloat
from wandelbots.omni.utils.scene import SceneUtils
from wandelbots.omni.utils.synthetic_data import SyntheticDataUtils

from wandelbots.omni.periphery.camera_configuration import (
    InstanceSegmentationInfo,
    SemanticSegmentationInfo,
    SYNTHETIC_DATA_CAPTURE_TYPES,
    DEPTH_UNIT_FROM_MILLIMETRES,
    DepthCaptureResult,
    DepthUnit,
    PointCloud,
    BoundingBox2D,
    BoundingBox3D,
    SemanticSegmentationData,
    InstanceSegmentationData,
)
from omni.replicator.core.scripts.writers_default.tools import (
    colorize_distance,
    colorize_normals,
)
from omni.replicator.core.scripts.utils.viewport_manager import HydraTexture


class NoLabeledObjectsError(Exception):
    """Raised when a capture requires semantic labels but none are in view."""


class NoCameraAtPathError(Exception):
    """Raised when the requested prim path holds no USD camera."""


def _finite_or_zero(data) -> np.ndarray:
    """Replace inf / -inf / NaN with 0.0 so the result survives JSON encoding.

    Annotators mark "nothing here" with a non-finite float - depth is inf for
    a pixel that sees no geometry, and normals are NaN there. JSON has no
    representation for either, so encoding the raw array fails the whole
    request with "Out of range float values are not JSON compliant" the
    moment the camera looks past the scene.
    """
    array = np.asarray(data, dtype=np.float64)
    return np.where(np.isfinite(array), array, 0.0)


class CameraCaptureService:
    async def _capture_synthetic_data(
        self,
        camera_path: str,
        capture_type: SYNTHETIC_DATA_CAPTURE_TYPES,
        resolution: tuple[int, int] = (512, 512),
    ):
        _, was_playing = SceneUtils.check_simulation()
        carb.log_info(f"Capturing {capture_type} data from camera {camera_path}")

        render_product: HydraTexture = rep.create.render_product(
            camera_path, resolution=resolution
        )
        annotator = rep.AnnotatorRegistry.get_annotator(capture_type)
        annotator.attach(render_product)
        try:
            # A fresh render pipeline can need up to 3 frames until the
            # annotator has data, so retry while the capture is pending.
            for _ in range(3):
                await rep.orchestrator.step_async(
                    pause_timeline=not was_playing, delta_time=0.0
                )
                data = annotator.get_data()
                if not self._is_capture_pending(data):
                    return data
            return data
        except Exception as e:
            raise RuntimeError(f"Unable to capture synthetic data: {e}")
        finally:
            annotator.detach(render_product)
            render_product.destroy()

    @staticmethod
    def _is_capture_pending(data) -> bool:
        """Detect the not-rendered-yet result shapes of annotator.get_data()."""
        if isinstance(data, np.ndarray):
            return data.size == 0
        if isinstance(data, dict):
            return "data" not in data
        return data is None

    def list_camera_prims(self) -> list[str]:
        """
        Returns all camera prim paths defined in the current scene stage.
        """
        stage = stage_utils.get_current_stage()
        return [
            x.GetPrimPath().pathString
            for x in stage.Traverse()
            if x.GetTypeName() == "Camera"
        ]

    async def get_color_image(
        self, camera_path: str, resolution: tuple[int, int]
    ) -> Image:
        color = await self._capture_synthetic_data(
            camera_path, capture_type="LdrColor", resolution=resolution
        )
        return Image.fromarray(color).convert("RGB")

    async def get_distance(
        self,
        camera_path: str,
        resolution: tuple[int, int],
        unit: DepthUnit = "mm",
    ) -> list[list[float]]:
        distance = await self._capture_synthetic_data(
            camera_path, capture_type="distance_to_camera", resolution=resolution
        )
        # Annotator data is in stage units. Millimetres are the house
        # convention get_pointcloud already uses, so the conversion goes through
        # there and then on to whatever the caller asked for.
        millimetres = SceneUtils.value_to_millimeters(_finite_or_zero(distance))
        return (millimetres * DEPTH_UNIT_FROM_MILLIMETRES[unit]).tolist()

    def get_camera_intrinsics(
        self, camera_path: str, resolution: tuple[int, int]
    ) -> list[list[float]]:
        """The 3x3 camera intrinsics (K) matrix for the requested resolution.

        fx/fy and cx/cy scale with the resolution because the USD camera prim
        only stores focal length and sensor aperture, both of which are
        resolution independent; the pixel-space intrinsics depend on how many
        pixels that aperture is rendered into.

        fy is the horizontal focal length, not one derived from the authored
        ``verticalAperture``. Isaac Sim renders square pixels - its own Camera
        helper says so at get_horizontal_aperture ("Only square pixels are
        supported; vertical aperture should match aspect ratio") and keeps the
        two apertures in sync whenever either is set. The authored vertical
        aperture belongs to the camera's NATIVE resolution, so pairing it with
        a different requested height produced an anamorphic fy: a ZED authored
        at 1920x1080 asked for 1280x720 reported fy off by the ratio of the two
        aspect ratios. Every consumer that unprojects the depth - which is what
        the intrinsics are returned for - then got a skewed result.
        """
        stage = stage_utils.get_current_stage()
        camera_prim = UsdGeom.Camera(stage.GetPrimAtPath(camera_path))
        if not camera_prim:
            # Without this the attribute reads below return None and the caller
            # sees a TypeError from the multiplication rather than the path it
            # got wrong.
            raise NoCameraAtPathError(f"No camera prim at {camera_path}")
        focal_length = camera_prim.GetFocalLengthAttr().Get()
        horizontal_aperture = camera_prim.GetHorizontalApertureAttr().Get()

        width, height = resolution
        fx = width * focal_length / horizontal_aperture
        return [[fx, 0.0, width / 2.0], [0.0, fx, height / 2.0], [0.0, 0.0, 1.0]]

    async def get_depth_capture(
        self,
        camera_path: str,
        resolution: tuple[int, int],
        unit: DepthUnit = "mm",
    ) -> DepthCaptureResult:
        """Range data plus the intrinsics that belong to the same resolution.

        Both together, because turning the Euclidean range this annotator
        reports into planar depth needs the intrinsics, and a caller that has
        to fetch them separately can end up pairing them with a different
        resolution. The unit is echoed back so the numbers are never read
        against the wrong assumption.
        """
        return DepthCaptureResult(
            depth=await self.get_distance(camera_path, resolution, unit),
            camera_intrinsics=self.get_camera_intrinsics(camera_path, resolution),
            unit=unit,
        )

    async def get_distance_image(
        self,
        camera_path: str,
        resolution: tuple[int, int],
        near: float | None = None,
        far: float | None = None,
    ) -> Image:
        """A colorized depth PNG, by default ramped over what is in the frame.

        near/far are stage units, so no fixed pair suits every scene: 100 is
        10 cm in a millimetre stage and 100 m in a metre one, and the wrong
        one renders the whole image flat. Fitting the ramp to the finite
        values in the frame gives a readable picture in either, and an
        explicit range still overrides it.
        """
        distance = await self._capture_synthetic_data(
            camera_path, capture_type="distance_to_camera", resolution=resolution
        )
        near, far = self._colour_ramp(distance, near, far)
        distance_data = colorize_distance(distance, near=near, far=far)
        return Image.fromarray(distance_data).convert("RGB")

    @staticmethod
    def _colour_ramp(
        distance, near: float | None, far: float | None
    ) -> tuple[float, float]:
        """The range to colorize over, filled in from the data where unset."""
        finite = np.asarray(distance)[np.isfinite(distance)]
        if near is None:
            near = float(finite.min()) if finite.size else 0.0
        if far is None:
            far = float(finite.max()) if finite.size else near + 1.0
        # colorize_distance divides by (far - near); a single-depth frame or a
        # reversed pair would otherwise produce inf.
        if far <= near:
            far = near + 1e-6
        return near, far

    async def get_normals(
        self, camera_path: str, resolution: tuple[int, int]
    ) -> list[list[list[float]]]:
        normals = await self._capture_synthetic_data(
            camera_path, capture_type="normals", resolution=resolution
        )
        # Same trap as depth: pixels without geometry carry no normal, and a
        # non-finite float is not JSON serializable - the request would fail
        # with "Out of range float values are not JSON compliant" as soon as
        # the camera sees past the geometry.
        return _finite_or_zero(normals).tolist()

    async def get_normals_image(
        self, camera_path: str, resolution: tuple[int, int]
    ) -> Image:
        normals = await self._capture_synthetic_data(
            camera_path, capture_type="normals", resolution=resolution
        )
        normals_data = colorize_normals(normals)
        return Image.fromarray(normals_data)

    async def get_pointcloud(
        self,
        camera_path: str,
        resolution: tuple[int, int],
        downscale_factor: confloat(ge=0.001, le=1) = 1,  # type: ignore
    ) -> PointCloud:
        pointcloud_data = await self._capture_synthetic_data(
            camera_path=camera_path, capture_type="pointcloud", resolution=resolution
        )
        if (
            "data" not in pointcloud_data
            or "info" not in pointcloud_data
            or pointcloud_data["data"].size == 0
        ):
            raise NoLabeledObjectsError(
                "No objects have semantic labels set in the camera field of view. "
                "Set semantic label for atleast one object of interest to capture point cloud data"
            )

        # Annotator data is in stage units, NOVA expects millimeters.
        # Sanitize here and not on the way out: the camera transform is a matmul,
        # where one inf becomes NaN across the whole row (inf * 0 is NaN) and
        # numpy warns about it. Cleaning the input keeps the good components of
        # a point whose neighbour was non-finite.
        points = _finite_or_zero(
            SceneUtils.value_to_millimeters(pointcloud_data["data"])
        )
        colors = pointcloud_data["info"]["pointRgb"].reshape(-1, 4)[:, :3]
        normals = _finite_or_zero(
            pointcloud_data["info"]["pointNormals"].reshape(-1, 4)[:, :3]
        )

        if downscale_factor != 1:
            points, colors, normals = SyntheticDataUtils.downscale_point_cloud(
                points, colors, normals, downscale_factor
            )

        world_to_cam = await SyntheticDataUtils.get_camera_tfm(camera_path)
        points = SyntheticDataUtils.transform_points(points, world_to_cam)
        normals = SyntheticDataUtils.transform_normals(normals, world_to_cam)
        point_cloud_data = PointCloud(
            points=points.tolist(), colors=colors.tolist(), normals=normals.tolist()
        )
        return point_cloud_data

    async def get_bounding_boxes(
        self,
        camera_path: str,
        resolution: tuple[int, int],
        box_type: Literal["2D", "3D"],
        labels: list[str],
    ) -> Union[list[BoundingBox2D], list[BoundingBox3D]]:
        bbox_capture_type = {"2D": "bounding_box_2d_tight", "3D": "bounding_box_3d"}[
            box_type
        ]
        SyntheticData.Get().set_instance_mapping_semantic_filter(
            SyntheticDataUtils.get_semantic_predicate(labels)
        )
        bbox_data = await self._capture_synthetic_data(
            camera_path=camera_path,
            capture_type=bbox_capture_type,
            resolution=resolution,
        )

        if bbox_data is None or "data" not in bbox_data or bbox_data["data"].size == 0:
            return []

        id_to_labels = bbox_data["info"]["idToLabels"]
        prim_paths = bbox_data["info"]["primPaths"]
        bounding_boxes = []

        for bbox, prim_path in zip(bbox_data["data"], prim_paths):
            semantic_id = str(bbox["semanticId"])
            label = id_to_labels[semantic_id]["class"]

            if box_type == "2D":
                bounding_boxes.append(
                    BoundingBox2D(
                        label=label,
                        bbox=(
                            bbox["x_min"],
                            bbox["y_min"],
                            bbox["x_max"],
                            bbox["y_max"],
                        ),
                        prim_path=prim_path,
                        semantic_id=semantic_id,
                    )
                )
            else:
                # The extent and the translation are both lengths in stage
                # units, and NOVA answers in millimetres, so both scale. The
                # rotation and scale in the rest of the matrix are ratios and
                # stay as they are.
                bbox["transform"][3, :3] = SceneUtils.value_to_millimeters(
                    bbox["transform"][3, :3]
                )

                bounding_boxes.append(
                    BoundingBox3D(
                        label=label,
                        bbox=[
                            SceneUtils.value_to_millimeters(bbox[f])
                            for f in [
                                "x_min",
                                "y_min",
                                "z_min",
                                "x_max",
                                "y_max",
                                "z_max",
                            ]
                        ],
                        prim_path=prim_path,
                        semantic_id=semantic_id,
                        transform=bbox["transform"].tolist(),
                    )
                )

        return bounding_boxes

    async def get_bounding_boxes_image(
        self,
        camera_path: str,
        resolution: tuple[int, int],
        box_type: Literal["2D", "3D"],
        labels: list[str],
    ) -> Image:
        """The captured frame with the boxes drawn on it.

        2D boxes are pixel rectangles; 3D boxes are wireframes projected
        through the camera, so the two need different drawing.
        """
        boxes = await self.get_bounding_boxes(
            camera_path, labels=labels, resolution=resolution, box_type=box_type
        )

        image = await self.get_color_image(camera_path, resolution)
        if box_type == "3D":
            return SyntheticDataUtils.colorize_3d_bounding_boxes(
                camera_path, resolution, image, boxes
            )
        return SyntheticDataUtils.colorize_2d_bounding_boxes(image, boxes)

    async def get_segmentation_data(
        self,
        camera_path: str,
        resolution: tuple[int, int],
        segmentation_type: Literal["semantic", "instance"],
        labels: Optional[list[str]],
    ) -> Union[SemanticSegmentationData, InstanceSegmentationData]:
        segmentation_capture_type = {
            "semantic": "semantic_segmentation",
            "instance": "instance_segmentation",
        }[segmentation_type]
        SyntheticData.Get().set_instance_mapping_semantic_filter(
            SyntheticDataUtils.get_semantic_predicate(labels)
        )
        segmented_data = await self._capture_synthetic_data(
            camera_path, capture_type=segmentation_capture_type, resolution=resolution
        )
        if not segmented_data or "data" not in segmented_data:
            raise Exception(
                "Unable to capture segmentation data. No labels found in the scene"
            )

        if "data" in segmented_data:
            segmented_data["data"] = (
                segmented_data["data"].tolist()
                if isinstance(segmented_data["data"], np.ndarray)
                else segmented_data["data"]
            )

        if "info" in segmented_data:
            segmented_data["info"] = {
                key: (val.tolist() if isinstance(val, np.ndarray) else val)
                for key, val in segmented_data["info"].items()
            }

        if segmentation_type == "instance":
            return InstanceSegmentationData(
                data=segmented_data["data"],
                info=InstanceSegmentationInfo(
                    id_to_labels=segmented_data["info"]["idToSemantics"]
                ),
            )

        return SemanticSegmentationData(
            data=segmented_data["data"],
            info=SemanticSegmentationInfo(
                id_to_labels=segmented_data["info"]["idToLabels"]
            ),
        )

    async def get_segmentation_image(
        self,
        camera_path: str,
        resolution: tuple[int, int],
        segmentation_type: Literal["semantic", "instance"],
        labels: Optional[list[str]] = None,
    ) -> Image:
        segmented_data = await self.get_segmentation_data(
            camera_path=camera_path,
            resolution=resolution,
            segmentation_type=segmentation_type,
            labels=labels,
        )
        return Image.fromarray(
            SyntheticDataUtils.colorize_segmented_data(segmented_data.data)
        )


_camera_capture_service = CameraCaptureService()


def get_camera_capture_service():
    return _camera_capture_service
