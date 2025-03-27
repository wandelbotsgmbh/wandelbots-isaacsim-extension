import pathlib
from typing import Literal, Optional, Union
from pydantic import BaseModel, validator, SecretStr, Field, conlist, model_validator, ConfigDict


class UsdStageModel(BaseModel):
    uri: str = Field(..., title="USD stage to open", description="USD Stage to open")


class WSPose(BaseModel):
    pose: Optional[list[float]] = [0, 0, 0, 0, 0, 0]

    @validator("pose", allow_reuse=True)
    def validate_ws_pose(cls, value):  # noqa
        if len(value) != 6:
            raise ValueError("pose must have exactly 6 elements")
        return value


class QuatPose(BaseModel):
    pose: Optional[list[float]] = [0, 0, 0, 1, 0, 0, 0]

    @validator("pose", allow_reuse=True)
    def validate_quat_pose(cls, value):  # noqa
        if len(value) != 7:
            raise ValueError("pose must have exactly 7 elements")
        return value


class Auth0Credentials(BaseModel):
    host: str
    is_secured: bool = True
    access_token: Optional[str]


class BasicAuthCredentials(BaseModel):
    host: str
    is_secured: bool = True
    username: Optional[str]
    password: Optional[SecretStr]


class CustomPrimData(BaseModel):
    category: str
    type: str


class ArticulationChainState(BaseModel):
    mode: str
    signals_mapping: dict[str, Union[bool, float]]
    joint_positions: list[float]
    joint_velocities: list[float]

    class Config:
        title = "Articulation Chain State"


class ConveyorState(BaseModel):
    mode: str
    signals_mapping: dict[str, Union[bool, float]]
    velocity: float
    direction: Optional[list[float]]

    class Config:
        title = "Conveyor State"


class SurfaceGripperState(BaseModel):
    mode: Literal["open", "close"]
    signals_mapping: dict[str, Union[bool, float]]

    class Config:
        title = "Surface Gripper State"


class AnalogSignal(BaseModel):
    id: str
    range: list[float]


class MockAnalogSignal(BaseModel):
    io: str = Field(example="analog_out[0]")
    io_value: Union[bool, float] = Field(example=0.2)


class GhostObjectSource(BaseModel):
    name: str
    prim_path: str


class TCPSource(BaseModel):
    name: str
    prim_path: str
    value: WSPose


class GhostObject(BaseModel):
    name: str
    prim_path: str
    robot_prim_path: str | None
    pose: WSPose

class BaseCameraParams(BaseModel):
    cameraModel: Literal["pinhole", "fisheyePolynomial"] = Field("pinhole", description="Camera model type, such as 'pinhole' or 'fisheyePolynomial'")
    resolution: conlist(int, min_length=2, max_length=2) = Field([1280, 720], description="Resolution of the rendered product [width, height]")
    focalLength: Optional[float] = Field(None, description="Focal length of the camera in scene units")
    horizontalAperture: Optional[float] = Field(None, description="Horizontal aperture of the camera")
    verticalAperture: Optional[float] = Field(None, description="Vertical aperture of the camera")
    focusDistance: Optional[float] = Field(None, description="Focus distance of the camera in scene units")
    fStop: Optional[float] = Field(None, description="F-Stop value of the camera, affecting depth of field")
    clippingRange: Optional[conlist(float, min_length=2, max_length=2)] = Field(None, description="Near and far clipping plane distances [near, far]")

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)


class BaseFisheyeParams(BaseModel):
    nominal_width: Optional[float] = Field(None, description="Nominal width of the fisheye lens")
    nominal_height: Optional[float] = Field(None, description="Nominal height of the fisheye lens")
    optical_centre_x: Optional[float] = Field(None, description="Optical centre x of the fisheye lens ")
    optical_centre_y: Optional[float] = Field(None, description="Optical centre y of the fisheye lens")
    cameraFisheyeMaxFOV: float = Field(170, description="Maximum field of view for the fisheye lens (in degrees)", alias="diagonalFov")
    cameraFisheyePolynomial: conlist(float, min_length=5, max_length=5) = Field([0.005, 0, 0, 0, 0], description="Polynomial coefficients for fisheye distortion correction", alias="distortionCoefficients")

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow", populate_by_name=True)


class CameraParams(BaseCameraParams):
    fishEyeProperties: Optional[BaseFisheyeParams] = Field(
        None, description="Fisheye specific parameters, required if camera_model is 'fisheyePolynomial'"
    )
    @model_validator(mode="after")
    def validate_fisheye_properties(self):
        if self.cameraModel == "fisheyePolynomial" and self.fishEyeProperties is None:
            raise ValueError("fishEyeProperties is required when camera_model is 'fisheyePolynomial'")
        return self

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow", populate_by_name=True)

class VirtualCameraConfiguration(BaseModel):
    identifier: str = Field(frozen=True)
    prim_path: str
    cam_params: CameraParams = Field(None,
                                     description="Camera parameters for the configurable camera. If None is given, then default parameters from the scene are used")


class PointCloud(BaseModel):
    points: list[list[float]]
    colors: list[list[float]]
    normals: list[list[float]]

class BoundingBox2D(BaseModel):
    label: str
    bbox: conlist(float, min_length=4, max_length=4)
    prim_path: str
    semantic_id: int

class BoundingBox3D(BaseModel):
    label: str
    bbox: conlist(float, min_length=6, max_length=6)
    prim_path: str
    semantic_id: int
    transform: conlist(conlist(float, min_length=4, max_length=4), min_length=4, max_length=4)


class InstanceSegmentationInfo(BaseModel):
    idToLabels: dict[str, str]

class SemanticSegmentationInfo(BaseModel):
    idToLabels: dict[str, dict[str, str]]

class InstanceSegmentationData(BaseModel):
    data: list[list[int]] = Field(..., description="Pixel-to-label array")
    info: InstanceSegmentationInfo

class SemanticSegmentationData(BaseModel):
    data: list[list[int]] = Field(..., description="Pixel-to-label array")
    info: SemanticSegmentationInfo

Pose = Union[WSPose, QuatPose]

STAGE_UNITS = Literal["mm", "cm", "m"]
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

COORDINATE_SYSTEM = Literal["world", "local"]
ROTATION_TYPES = Literal["cartesian", "quaternions"]

GHOST_MATERIAL_MDL_FILE = str(
    pathlib.Path(__file__).parent / "assets" / "wb_ghost.mdl"
)
SHADER_IDENTIFIER = "GhostTeaching"
