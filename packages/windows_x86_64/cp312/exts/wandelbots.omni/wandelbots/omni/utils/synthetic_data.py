import numpy as np

import carb
import isaacsim.core.utils.semantics as semantic_utils
import isaacsim.core.utils.stage as stage_utils
from pxr import Usd

try:
    # Deprecated SemanticsAPI, still shipped by omni.usd.schema.semantics on
    # both Isaac Sim 5.1 and 6.0. Scenes labeled by older extension versions
    # carry their labels in this schema instead of UsdSemantics.LabelsAPI.
    import Semantics

    _HAS_DEPRECATED_SEMANTICS = True
except ImportError:
    _HAS_DEPRECATED_SEMANTICS = False

from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.scene import SceneUtils
from wandelbots.omni.utils.math import pose_to_matrix
from omni.replicator.core.scripts.writers_default.tools import data_to_colour
from PIL import Image, ImageDraw

from wandelbots.omni.periphery.camera_configuration import (
    BoundingBox2D,
    BoundingBox3D,
    draw_wireframe,
    project_box_corners,
)


class SyntheticDataUtils:
    @staticmethod
    def colorize_segmented_data(segmented_data: list) -> np.ndarray:
        color_dict = {}
        np.random.seed(33)
        segmented_data = np.array(segmented_data)
        for each in np.unique(segmented_data):
            color = list(np.random.choice(range(256), size=3))
            color_dict.update({each: color})

        out_image = np.zeros(
            (segmented_data.shape[0], segmented_data.shape[1], 3), dtype=np.uint8
        )
        for key, value in color_dict.items():
            out_image[np.where(segmented_data == key)] = value

        return out_image

    @staticmethod
    def colorize_3d_bounding_boxes(
        camera_path: str,
        resolution: tuple[int, int],
        image: Image,
        bbox_3ds: list[BoundingBox3D],
    ) -> Image:
        """Draw the wireframe of every 3D box onto the captured frame.

        The boxes arrive in millimetres, which is what the API answers with;
        the view matrix works in stage units, so extent and translation are
        converted back here rather than projecting millimetres through a
        stage-unit camera.
        """
        from pxr import Usd, UsdGeom

        # Imported here because camera_capture_service imports this module.
        from wandelbots.omni.periphery.camera_capture_service import (
            NoCameraAtPathError,
        )

        width, height = resolution
        prim = UsdGeom.Camera(
            stage_utils.get_current_stage().GetPrimAtPath(camera_path)
        )
        if not prim:
            raise NoCameraAtPathError(f"No camera prim at {camera_path}")

        gf_camera = prim.GetCamera(Usd.TimeCode.Default())
        # The authored vertical aperture belongs to the camera's own
        # resolution. Pairing it with a different requested one would stretch
        # the wireframe away from the object it is drawn around; Isaac Sim
        # renders square pixels, so the aperture follows the requested aspect.
        gf_camera.verticalAperture = gf_camera.horizontalAperture * height / width

        frustum = gf_camera.frustum
        # USD's own matrices, in the row-vector convention project_box_corners
        # multiplies with.
        view = np.array(frustum.ComputeViewMatrix(), dtype=float).reshape((4, 4))
        projection = np.array(frustum.ComputeProjectionMatrix(), dtype=float).reshape(
            (4, 4)
        )

        draw = ImageDraw.Draw(image)
        for bbox_data in bbox_3ds:
            colour = data_to_colour(bbox_data.semantic_id)
            local_to_world = np.array(bbox_data.transform, dtype=float)
            local_to_world[3, :3] = [
                SceneUtils.millimeters_to_stage_value(v) for v in local_to_world[3, :3]
            ]
            corners = project_box_corners(
                bbox=tuple(
                    SceneUtils.millimeters_to_stage_value(v) for v in bbox_data.bbox
                ),
                local_to_world=local_to_world,
                view=view,
                projection=projection,
                resolution=resolution,
            )
            # Empty when the box straddles the camera plane: a mirrored
            # wireframe looks convincing and is wrong, so it is skipped.
            draw_wireframe(draw, corners, colour)

        return image

    @staticmethod
    def colorize_2d_bounding_boxes(image: Image, bbox_2ds: BoundingBox2D) -> Image:
        draw = ImageDraw.Draw(image)
        for each in bbox_2ds:
            xmin, ymin, xmax, ymax = map(int, each.bbox)
            color = data_to_colour(each.semantic_id)
            draw.rectangle([(xmin, ymin), (xmax, ymax)], outline=color, width=2)

        return image

    @staticmethod
    def downscale_point_cloud(
        points: np.ndarray, colors: np.ndarray, normals: np.ndarray, percentage: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        original_points_count = len(points)
        downsampled_points_count = int(original_points_count * percentage)
        np.random.seed(33)
        random_indices = np.random.choice(
            original_points_count, downsampled_points_count, replace=False
        )
        selected_points = points[random_indices]
        selected_colors = colors[random_indices]
        selected_normals = normals[random_indices]

        return selected_points, selected_colors, selected_normals

    @staticmethod
    def get_semantic_predicate(object_class: list[str]) -> str:
        if object_class == ["all"]:
            semantic_predicate = "class:*"
        else:
            sem_str = "|".join(object_class)
            semantic_predicate = "class:" + sem_str

        return semantic_predicate

    CLASS_INSTANCE_NAME = "class"

    @staticmethod
    def _deprecated_class_semantics(prim: Usd.Prim) -> list[tuple[str, object]]:
        """(instance name, SemanticsAPI) of the prim's deprecated class tags.

        The deprecated schema stores the namespace in `semanticType`, so only
        tags of type "class" correspond to the labels this API reads and
        writes; anything else belongs to another namespace and is left alone.
        """
        if not _HAS_DEPRECATED_SEMANTICS:
            return []
        instance_names = {
            prop.SplitName()[1]
            for prop in prim.GetProperties()
            if Semantics.SemanticsAPI.IsSemanticsAPIPath(prop.GetPath())
        }
        class_semantics = []
        for instance_name in sorted(instance_names):
            semantics_api = Semantics.SemanticsAPI.Get(prim, instance_name)
            if not semantics_api:
                continue
            semantic_type = semantics_api.GetSemanticTypeAttr().Get()
            if semantic_type == SyntheticDataUtils.CLASS_INSTANCE_NAME:
                class_semantics.append((instance_name, semantics_api))
        return class_semantics

    @staticmethod
    def get_prim_labels(prim: Usd.Prim) -> list[str]:
        """Class labels of a prim, falling back to the deprecated SemanticsAPI.

        Scenes labeled before the migration to UsdSemantics.LabelsAPI would
        otherwise report as unlabeled.
        """
        class_labels = semantic_utils.get_labels(prim).get("class")
        if class_labels:
            return list(class_labels)

        deprecated_labels = []
        for _, semantics_api in SyntheticDataUtils._deprecated_class_semantics(prim):
            semantic_data = semantics_api.GetSemanticDataAttr().Get()
            if semantic_data:
                deprecated_labels.append(semantic_data)
        return sorted(deprecated_labels)

    @staticmethod
    def remove_prim_labels(prim: Usd.Prim, include_descendants: bool = False) -> None:
        """Remove the prim's class labels, current and deprecated schema.

        Scoped to the "class" instance: that is the only namespace this API
        reads and writes, so clearing must not delete labels another tool
        stored under a different instance name.
        """
        semantic_utils.remove_labels(
            prim,
            instance_name=SyntheticDataUtils.CLASS_INSTANCE_NAME,
            include_descendants=include_descendants,
        )
        SyntheticDataUtils.remove_deprecated_prim_labels(
            prim, include_descendants=include_descendants
        )

    @staticmethod
    def remove_deprecated_prim_labels(
        prim: Usd.Prim, include_descendants: bool = False
    ) -> None:
        """Remove only the deprecated class tags, leaving current labels.

        Used before writing a label so a prim never carries both schemas.
        """
        if not _HAS_DEPRECATED_SEMANTICS:
            return
        prims = Usd.PrimRange(prim) if include_descendants else [prim]
        for current_prim in prims:
            for (
                instance_name,
                semantics_api,
            ) in SyntheticDataUtils._deprecated_class_semantics(current_prim):
                for attribute in (
                    semantics_api.GetSemanticTypeAttr(),
                    semantics_api.GetSemanticDataAttr(),
                ):
                    if attribute and attribute.IsDefined():
                        current_prim.RemoveProperty(attribute.GetName())
                current_prim.RemoveAPI(Semantics.SemanticsAPI, instance_name)
                carb.log_info(
                    f"Removed deprecated semantics '{instance_name}' from "
                    f"{current_prim.GetPath().pathString}"
                )

    @staticmethod
    def get_all_semantic_labels() -> dict[str, list]:
        labels = {}

        for prim in stage_utils.traverse_stage():
            class_labels = SyntheticDataUtils.get_prim_labels(prim)
            if not class_labels:
                continue
            labels[prim.GetPrimPath().pathString] = class_labels
        return labels

    @staticmethod
    def set_semantic_label(prim_path: str, label: str) -> None:
        prim = PrimUtils.get_prim(prim_path)
        # Drop a deprecated tag first so the prim does not carry both schemas
        # with conflicting labels. Only the deprecated ones: add_labels already
        # replaces the "class" instance, and other LabelsAPI instances on the
        # prim must survive.
        SyntheticDataUtils.remove_deprecated_prim_labels(prim)
        semantic_utils.add_labels(prim, [label], instance_name="class")

    @staticmethod
    def get_semantic_label(prim_path: str) -> list[str]:
        return SyntheticDataUtils.get_prim_labels(PrimUtils.get_prim(prim_path))

    @staticmethod
    def remove_all_semantic_labels() -> None:
        source_prim = PrimUtils.get_prim("/")
        SyntheticDataUtils.remove_prim_labels(source_prim, include_descendants=True)

    @staticmethod
    async def get_camera_tfm(camera_path: str) -> np.ndarray:
        """World-to-camera transform of the camera prim."""
        pose = PrimUtils.get_prim_pose(camera_path, coordinate_system="world")
        return np.linalg.inv(pose_to_matrix(pose.pose))

    @staticmethod
    def transform_points(points3d, tfm) -> np.ndarray:
        points4d = np.hstack([points3d, np.ones((points3d.shape[0], 1))])
        points4d = np.dot(points4d, tfm.T)
        return points4d[:, :3]

    @staticmethod
    def transform_normals(normals3d, tfm) -> np.ndarray:
        return np.dot(normals3d, tfm[:3, :3].T)
