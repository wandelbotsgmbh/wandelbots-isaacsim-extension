from typing import Literal, Union, Optional

import carb
import numpy as np

from pxr import UsdGeom
import omni.usd
import omni.replicator.core as rep
from PIL import Image

from omni.syntheticdata import SyntheticData

import isaacsim.core.utils.stage as stage_utils

from pydantic import confloat
from wandelbots.omni.utils.scene import SceneUtils
from wandelbots.omni.utils.synthetic_data import SyntheticDataUtils

from wandelbots.omni.periphery.camera_configuration import (
    InstanceSegmentationInfo,
    SemanticSegmentationInfo,
    SYNTHETIC_DATA_CAPTURE_TYPES,
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


class CameraCaptureService:
    async def _capture_synthetic_data(
        self,
        camera_path: str,
        capture_type: SYNTHETIC_DATA_CAPTURE_TYPES,
        resolution: tuple[int, int] = (512, 512),
    ):
        _, was_playing = SceneUtils.check_simulation()
        try:
            carb.log_info(f"Capturing {capture_type} data from camera /World/TestCam")

            render_product: HydraTexture = rep.create.render_product(
                camera_path, resolution=resolution
            )
            annotator = rep.AnnotatorRegistry.get_annotator(capture_type)
            annotator.attach(render_product)

            await rep.orchestrator.step_async(
                pause_timeline=not was_playing, delta_time=0.0
            )
            data = annotator.get_data()
            annotator.detach(render_product)
            return data
        except Exception as e:
            raise RuntimeError(f"Unable to capture synthetic data: {e}")

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
        self, camera_path: str, resolution: tuple[int, int]
    ) -> list[float]:
        distance = await self._capture_synthetic_data(
            camera_path, capture_type="distance_to_camera", resolution=resolution
        )
        return distance.tolist()

    async def get_distance_image(
        self,
        camera_path: str,
        resolution: tuple[int, int],
        near: float = 1e-5,
        far: float = 100.0,
    ) -> Image:
        distance_data = colorize_distance(
            await self.get_distance(camera_path, resolution), near=near, far=far
        )
        return Image.fromarray(distance_data).convert("RGB")

    async def get_normals(
        self, camera_path: str, resolution: tuple[int, int]
    ) -> list[list[float]]:
        return await self._capture_synthetic_data(
            camera_path, capture_type="normals", resolution=resolution
        )

    async def get_normals_image(
        self, camera_path: str, resolution: tuple[int, int]
    ) -> Image:
        normals = await self.get_normals(camera_path, resolution)

        normals_data = colorize_normals(normals)
        return Image.fromarray(normals_data)

    async def get_pointcloud(
        self,
        camera_path: str,
        resolution: tuple[int, int],
        downscale_factor: confloat(ge=0.001, le=1) = 1,  # type: ignore
    ) -> PointCloud:
        pc_data = await self._capture_synthetic_data(
            camera_path=camera_path, capture_type="pointcloud", resolution=resolution
        )
        if "data" not in pc_data or "info" not in pc_data:
            raise ValueError(
                "No objects have semantic labels set in the camera field of view. "
                "Set semantic label for atleast one object of interest to capture point cloud data"
            )

        points = pc_data["data"] * 1000
        colors = pc_data["info"]["pointRgb"].reshape(-1, 4)[:, :3]
        normals = pc_data["info"]["pointNormals"].reshape(-1, 4)[:, :3]

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
                # convert translation from stage unit to mm (unit used in NOVA)
                translation_conversion_factor = 1000.0 / UsdGeom.GetStageMetersPerUnit(
                    omni.usd.get_context().get_stage()
                )
                bbox["transform"][3, :3] *= translation_conversion_factor

                bounding_boxes.append(
                    BoundingBox3D(
                        label=label,
                        bbox=[
                            bbox[f]
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
        bbox_2ds = await self.get_bounding_boxes(
            camera_path, labels=labels, resolution=resolution, box_type=box_type
        )

        image = await self.get_color_image(camera_path, resolution)
        return SyntheticDataUtils.colorize_2d_bounding_boxes(image, bbox_2ds)

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
