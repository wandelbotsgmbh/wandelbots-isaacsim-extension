from typing import Dict, List

import numpy as np
import omni.isaac.core.utils.semantics as semantic_utils
import omni.isaac.core.utils.stage as stage_utils
from wandelbots.omni.utils.prim_utils import PrimUtils
from omni.replicator.core.scripts.writers_default.tools import data_to_colour
from PIL import Image, ImageDraw
from sklearn.neighbors import NearestNeighbors


class SyntheticDataUtils:
    @staticmethod
    def colorize_segmented_data(segmented_data: np.ndarray) -> np.ndarray:
        color_dict = {}
        np.random.seed(33)
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
        image: Image, bbox_3ds: dict, camera_params: dict
    ) -> Image:
        cam_view_transform = np.array(camera_params["cameraViewTransform"]).reshape(
            (4, 4)
        )
        cam_view_transform = cam_view_transform.T
        cam_projection_transform = np.array(camera_params["cameraProjection"]).reshape(
            (4, 4)
        )
        cam_projection_transform = cam_projection_transform.T
        width, height = camera_params["resolution"]

        colors = [data_to_colour(bbox["semanticId"]) for bbox in bbox_3ds["data"]]
        for bbox_data in bbox_3ds["data"]:
            s_id, x_min, y_min, z_min, x_max, y_max, z_max = (
                bbox_data[0],
                bbox_data[1],
                bbox_data[2],
                bbox_data[3],
                bbox_data[4],
                bbox_data[5],
                bbox_data[6],
            )
            local_to_world_transform = bbox_data[7]
            local_to_world_transform = local_to_world_transform.T
            vertices_local = [
                np.array([x_min, y_min, z_min, 1]),
                np.array([x_min, y_min, z_max, 1]),
                np.array([x_min, y_max, z_min, 1]),
                np.array([x_min, y_max, z_max, 1]),
                np.array([x_max, y_min, z_min, 1]),
                np.array([x_max, y_min, z_max, 1]),
                np.array([x_max, y_max, z_min, 1]),
                np.array([x_max, y_max, z_max, 1]),
            ]

            image_points = []
            for vertex in vertices_local:
                world_homogeneous = np.dot(local_to_world_transform, vertex)
                camera_homogeneous = np.dot(cam_view_transform, world_homogeneous)
                clip_space = np.dot(cam_projection_transform, camera_homogeneous)
                ndc = clip_space[:3] / clip_space[3]
                screen_point = ((ndc[0] + 1) * width / 2, (1 - ndc[1]) * height / 2)
                image_points.append(screen_point)

            draw = ImageDraw.Draw(image)
            draw.line([image_points[0], image_points[1]], fill=colors[s_id], width=2)
            draw.line([image_points[0], image_points[2]], fill=colors[s_id], width=2)
            draw.line([image_points[0], image_points[4]], fill=colors[s_id], width=2)
            draw.line([image_points[1], image_points[3]], fill=colors[s_id], width=2)
            draw.line([image_points[1], image_points[5]], fill=colors[s_id], width=2)
            draw.line([image_points[2], image_points[3]], fill=colors[s_id], width=2)
            draw.line([image_points[2], image_points[6]], fill=colors[s_id], width=2)
            draw.line([image_points[3], image_points[7]], fill=colors[s_id], width=2)
            draw.line([image_points[4], image_points[5]], fill=colors[s_id], width=2)
            draw.line([image_points[4], image_points[6]], fill=colors[s_id], width=2)
            draw.line([image_points[5], image_points[7]], fill=colors[s_id], width=2)
            draw.line([image_points[6], image_points[7]], fill=colors[s_id], width=2)

            return image

    @staticmethod
    def colorize_2d_bounding_boxes(image: Image, bbox_2ds: dict) -> Image:
        draw = ImageDraw.Draw(image)
        colors = [data_to_colour(bbox["semanticId"]) for bbox in bbox_2ds["data"]]
        bbox_2ds_data = np.array(bbox_2ds["data"].tolist()).astype(np.int64)
        for s_id, xmin, ymin, xmax, ymax, _ in bbox_2ds_data:
            draw.rectangle([(xmin, ymin), (xmax, ymax)], outline=colors[s_id], width=2)
        return image

    @staticmethod
    def downscale_point_cloud(
        points: np.ndarray, colors: np.ndarray, normals: np.ndarray, percentage: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        original_points_count = len(points)
        downsampled_points_count = int(original_points_count * percentage)
        nn_model = NearestNeighbors(n_neighbors=1)
        nn_model.fit(points)
        np.random.seed(33)
        random_indices = np.random.choice(
            original_points_count, downsampled_points_count, replace=False
        )
        selected_points = points[random_indices]
        selected_colors = colors[random_indices]
        selected_normals = normals[random_indices]

        return selected_points, selected_colors, selected_normals

    @staticmethod
    def get_semantic_predicate(object_class: List[str] = "all") -> str:
        if "all" in object_class:
            semantic_predicate = "class:*"
        else:
            sem_str = "|".join(object_class)
            semantic_predicate = "class:" + sem_str

        return semantic_predicate

    @staticmethod
    def get_all_semantic_labels() -> Dict[str, list]:
        labels = {}
        for prim in stage_utils.traverse_stage():
            label = semantic_utils.get_semantics(prim)
            if "Semantics" in label:
                labels.update({prim.GetPrimPath().pathString: [label["Semantics"][1]]})
        return labels

    @staticmethod
    def set_semantic_label(prim_path: str, label: str) -> None:
        prim = PrimUtils.get_object(prim_path)
        semantic_utils.add_update_semantics(prim, label)

    @staticmethod
    def get_semantic_label(prim_path: str) -> list[str]:
        prim = PrimUtils.get_object(prim_path)
        label = semantic_utils.get_semantics(prim)
        return [label["Semantics"][1]] if label else []

    @staticmethod
    def remove_all_semantic_labels() -> None:
        source_prim = PrimUtils.get_object("/")
        semantic_utils.remove_all_semantics(source_prim, recursive=True)
