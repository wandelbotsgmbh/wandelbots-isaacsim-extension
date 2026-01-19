from typing import Literal, Optional, Annotated
from pydantic import BaseModel, Field, ConfigDict, model_validator


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
