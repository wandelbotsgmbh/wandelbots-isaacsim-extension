import pathlib
from typing import Literal, List, Optional, Union
from pydantic import BaseModel, validator, SecretStr, Field


class UsdStageModel(BaseModel):
    uri: str = Field(..., title="USD stage to open", description="USD Stage to open")


class WSPose(BaseModel):
    pose: Optional[List[float]] = [0, 0, 0, 0, 0, 0]

    @validator("pose", allow_reuse=True)
    def validate_ws_pose(cls, value):  # noqa
        if len(value) != 6:
            raise ValueError("pose must have exactly 6 elements")
        return value


class QuatPose(BaseModel):
    pose: Optional[List[float]] = [0, 0, 0, 1, 0, 0, 0]

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
