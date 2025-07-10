import pathlib
from enum import Enum
from typing import Literal, Optional, Union
from pydantic import BaseModel, SecretStr, Field, conlist


class UsdStageModel(BaseModel):
    uri: str = Field(..., title="USD stage to open", description="USD Stage to open")


class WSPose(BaseModel):
    pose: conlist(float, min_length=6, max_length=6) = Field(
        [0, 0, 0, 0, 0, 0], description="Pose in rotation vector format (WS)"
    )


class QuatPose(BaseModel):
    pose: conlist(float, min_length=7, max_length=7) = Field(
        [0, 0, 0, 1, 0, 0, 0], description="Pose in quaternion vector format (WS)"
    )


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


class RelativePoseMode(str, Enum):
    NORMAL = "normal"
    INVERSE_FIRST = "inverse_first"
    INVERSE_SECOND = "inverse_second"
    INVERSE_BOTH = "inverse_both"


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

GHOST_MATERIAL_MDL_FILE = str(pathlib.Path(__file__).parent / "assets" / "wb_ghost.mdl")
SHADER_IDENTIFIER = "GhostTeaching"

GIZMO_USD_FILE = str(pathlib.Path(__file__).parent / "assets" / "gizmo.usd")
