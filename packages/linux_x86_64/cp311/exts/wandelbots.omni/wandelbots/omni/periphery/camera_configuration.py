import io
from typing import Literal, Optional, Annotated

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field, ConfigDict, model_validator

#: The twelve edges of a box, as index pairs into the eight corners produced
#: by `project_box_corners` (x minor, then y, then z).
BOX_EDGES = (
    (0, 1),
    (0, 2),
    (0, 4),
    (1, 3),
    (1, 5),
    (2, 3),
    (2, 6),
    (3, 7),
    (4, 5),
    (4, 6),
    (5, 7),
    (6, 7),
)


def draw_wireframe(draw, corners: list[tuple[float, float]], colour) -> None:
    """Draw the twelve edges of a projected box, or nothing.

    `project_box_corners` answers an empty list for a box the camera cannot
    see properly. Drawing has to accept that rather than index into it: an
    IndexError here turns one awkward box into a 500 for the whole capture.
    """
    if not corners:
        return
    for first, second in BOX_EDGES:
        draw.line([corners[first], corners[second]], fill=colour, width=2)


def project_box_corners(
    bbox: tuple[float, float, float, float, float, float],
    local_to_world,
    view,
    projection,
    resolution: tuple[int, int],
) -> list[tuple[float, float]]:
    """The eight corners of a 3D box, as pixel coordinates.

    `bbox` is the axis-aligned extent in the object's own frame and
    `local_to_world` places it, both in stage units - the same units the view
    matrix works in. Returns an empty list when any corner falls behind the
    camera: dividing by a negative w mirrors the box back into frame, which
    draws a convincing but wrong wireframe.

    Kept here rather than beside the drawing code so the arithmetic can be
    tested without Kit.
    """
    x_min, y_min, z_min, x_max, y_max, z_max = bbox
    corners = [
        np.array([x, y, z, 1.0])
        for x in (x_min, x_max)
        for y in (y_min, y_max)
        for z in (z_min, z_max)
    ]
    # Row-vector convention: USD puts the translation in the last ROW, so a
    # point multiplies from the left.
    world = [corner @ np.asarray(local_to_world) for corner in corners]
    camera = [point @ np.asarray(view) for point in world]
    clip = [point @ np.asarray(projection) for point in camera]

    if any(point[3] <= 0 for point in clip):
        return []

    width, height = resolution
    points = []
    for point in clip:
        ndc = point[:3] / point[3]
        points.append(((ndc[0] + 1) * width / 2, (1 - ndc[1]) * height / 2))
    return points


#: The formats a capture can be returned in, besides "json". PNG is lossless
#: and is what every existing caller gets; jpeg is a fraction of the size on a
#: rendered frame, which matters when the capture is polled rather than taken
#: once.
IMAGE_RESULT_TYPES = ("rgb_png", "jpeg")

ImageResultType = Literal["rgb_png", "jpeg"]

_MEDIA_TYPES = {"rgb_png": "image/png", "jpeg": "image/jpeg"}


def encode_image(image: Image.Image, result_type: str) -> tuple[bytes, str]:
    """Encode a captured frame, returning the bytes and their media type.

    Kept here rather than in the router so it can be tested without Kit.
    """
    if result_type not in _MEDIA_TYPES:
        raise ValueError(
            f"{result_type!r} is not an image format; expected one of {IMAGE_RESULT_TYPES}"
        )
    buffer = io.BytesIO()
    if result_type == "jpeg":
        # The annotators hand back RGBA and jpeg has nowhere to put the alpha
        # channel, so it is dropped rather than letting PIL raise.
        image.convert("RGB").save(buffer, format="JPEG", quality=90)
    else:
        image.save(buffer, format="PNG")
    return buffer.getvalue(), _MEDIA_TYPES[result_type]


# ------------------------- datatypes for camera -------------------------
class BaseCameraParams(BaseModel):
    camera_model: Literal["pinhole", "fisheyePolynomial"] = Field(
        "pinhole",
        description="Camera model type, such as 'pinhole' or 'fisheyePolynomial'",
    )
    resolution: Annotated[list[int], Field(min_length=2, max_length=2)] = Field(
        [1280, 720], description="Resolution of the rendered product [width, height]"
    )
    focal_length: Optional[float] = Field(
        None, description="Focal length of the camera in scene units"
    )
    horizontal_aperture: Optional[float] = Field(
        None, description="Horizontal aperture of the camera"
    )
    vertical_aperture: Optional[float] = Field(
        None, description="Vertical aperture of the camera"
    )
    focus_distance: Optional[float] = Field(
        None, description="Focus distance of the camera in scene units"
    )
    f_stop: Optional[float] = Field(
        None, description="F-Stop value of the camera, affecting depth of field"
    )
    clipping_range: Optional[
        Annotated[list[float], Field(min_length=2, max_length=2)]
    ] = Field(
        None,
        description="Near and far clipping plane distances [near, far]",
    )


class BaseFisheyeParams(BaseModel):
    nominal_width: Optional[float] = Field(
        None, description="Nominal width of the fisheye lens"
    )
    nominal_height: Optional[float] = Field(
        None, description="Nominal height of the fisheye lens"
    )
    optical_centre_x: Optional[float] = Field(
        None, description="Optical centre x of the fisheye lens"
    )
    optical_centre_y: Optional[float] = Field(
        None, description="Optical centre y of the fisheye lens"
    )
    fisheye_max_fov: float = Field(
        170,
        description="Maximum field of view for the fisheye lens (in degrees)",
        alias="diagonal_fov",
    )
    fisheye_polynomial: Annotated[list[float], Field(min_length=5, max_length=5)] = (
        Field(
            [0.005, 0, 0, 0, 0],
            description="Polynomial coefficients for fisheye distortion correction",
            alias="distortion_coefficients",
        )
    )


class CameraParams(BaseCameraParams):
    fisheye_properties: Optional[BaseFisheyeParams] = Field(
        None,
        description="Fisheye specific parameters, required if camera_model is 'fisheye_properties'",
    )

    camera_intrinsics: list[list[float]] = Field(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        description="Matrix with camera intrinsics",
    )
    camera_projection: list[list[float]] = Field(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        description="Projection matrix",
    )
    camera_view_transform: list[list[float]] = Field(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        description="View transfrom matrix",
    )

    @model_validator(mode="after")
    def validate_fisheye_properties(self):
        if (
            self.camera_model == "fisheye_properties"
            and self.fisheye_properties is None
        ):
            raise ValueError(
                "fisheye_properties is required when camera_model is 'fisheye_properties'"
            )
        return self


class VirtualCameraConfiguration(BaseModel):
    identifier: str = Field(
        ..., description="Unique identifier for the camera", frozen=True
    )
    prim_path: str = Field(
        ..., description="Path to the USD prim representing the camera"
    )
    camera_params: CameraParams = Field(
        ...,
        description="Camera parameters for the configurable camera. If None is given, then default parameters from the scene are used",
    )


# Depth is the only value here that carries a length unit; the intrinsics are
# pixels either way. Naming the unit in the request and echoing it in the answer
# keeps the next caller who wants metres from needing a new contract.
DepthUnit = Literal["mm", "m"]
DEPTH_UNIT_FROM_MILLIMETRES: dict[str, float] = {"mm": 1.0, "m": 0.001}


# Both dimensions are pinned, so a generated client gets a 3x3 matrix rather
# than three rows of arbitrary length.
IntrinsicsRow = Annotated[list[float], Field(min_length=3, max_length=3)]
IntrinsicsMatrix = Annotated[list[IntrinsicsRow], Field(min_length=3, max_length=3)]


class DepthCaptureResult(BaseModel):
    depth: list[list[float]] = Field(
        ...,
        description="Per-pixel Euclidean range (distance from the camera to the "
        "surface along the viewing ray), in the unit named by `unit`. Pixels "
        "without geometry (infinite range) are returned as 0.0.",
    )
    camera_intrinsics: IntrinsicsMatrix = Field(
        ...,
        description="3x3 camera intrinsics (K) matrix [[fx, 0, cx], [0, fy, cy], "
        "[0, 0, 1]] in pixels, computed for the requested capture resolution.",
    )
    unit: DepthUnit = Field(
        "mm",
        description="Length unit the depth values are expressed in.",
    )

    model_config = ConfigDict(title="Depth Capture Result")


class PointCloud(BaseModel):
    points: list[list[float]] = Field(
        ..., description="List of 3D points in world coordinates"
    )
    colors: list[list[float]] = Field(
        ..., description="List of RGB colors for each point"
    )
    normals: list[list[float]] = Field(
        ..., description="List of surface normals for each point"
    )

    model_config = ConfigDict(title="Point Cloud")


class BoundingBox2D(BaseModel):
    label: str = Field(..., description="Class label for the detected object")
    bbox: Annotated[list[float], Field(min_length=4, max_length=4)] = Field(
        ..., description="Bounding box coordinates [x_min, y_min, x_max, y_max]"
    )
    prim_path: str = Field(
        ..., description="Path to the USD prim of the detected object"
    )
    semantic_id: int = Field(..., description="Semantic ID of the detected object")

    model_config = ConfigDict(title="2D Bounding Box")


class BoundingBox3D(BaseModel):
    label: str = Field(..., description="Class label for the detected object")
    bbox: Annotated[list[float], Field(min_length=6, max_length=6)] = Field(
        ...,
        description="Bounding box coordinates [x_min, y_min, z_min, x_max, y_max, z_max]",
    )
    prim_path: str = Field(
        ..., description="Path to the USD prim of the detected object"
    )
    semantic_id: int = Field(..., description="Semantic ID of the detected object")
    transform: Annotated[list[list[float]], Field(min_length=4, max_length=4)] = Field(
        ..., description="4x4 transformation matrix. Translation always uses mm"
    )

    model_config = ConfigDict(title="3D Bounding Box")


class InstanceSegmentationInfo(BaseModel):
    id_to_labels: dict[str, dict[str, str]] = Field(
        ..., description="Mapping of instance IDs to their class labels"
    )


class SemanticSegmentationInfo(BaseModel):
    id_to_labels: dict[str, dict[str, str]] = Field(
        ..., description="Mapping of semantic IDs to their class labels and attributes"
    )


class InstanceSegmentationData(BaseModel):
    data: list[list[int]] = Field(..., description="Pixel-to-label array")
    info: InstanceSegmentationInfo = Field(
        ..., description="Instance segmentation metadata"
    )


class SemanticSegmentationData(BaseModel):
    data: list[list[int]] = Field(..., description="Pixel-to-label array")
    info: SemanticSegmentationInfo = Field(
        ..., description="Semantic segmentation metadata"
    )


SYNTHETIC_DATA_CAPTURE_TYPES = Literal[
    "LdrColor",
    "normals",
    "distance_to_camera",
    "pointcloud",
    "bounding_box_2d_tight",
    "bounding_box_3d",
    "instance_segmentation",
    "semantic_segmentation",
]
