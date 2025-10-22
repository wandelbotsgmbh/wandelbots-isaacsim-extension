import pathlib
from enum import Enum
from typing import Literal, Optional, Union
from pydantic import BaseModel, SecretStr, Field, conlist
import wandelbots_api_client.v2.models as nova_models


class UsdStageModel(BaseModel):
    uri: str = Field(..., title="USD stage to open", description="USD Stage to open")


class WSPose(BaseModel):
    pose: conlist(float, min_length=6, max_length=6) = Field(
        [0, 0, 0, 0, 0, 0], description="Pose in rotation vector format (WS)"
    )

    def to_nova_pose(self) -> nova_models.Pose:
        return nova_models.Pose(
            position=self.pose[:3],
            orientation=self.pose[3:],
        )

    def __str__(self):
        return "(" + ", ".join([f"{round(x, 3)}" for x in self.pose]) + ")"


class QuatPose(BaseModel):
    pose: conlist(float, min_length=7, max_length=7) = Field(
        [0, 0, 0, 1, 0, 0, 0], description="Pose in quaternion vector format (WS)"
    )

    def __str__(self):
        return "(" + ", ".join([f"{round(x, 3)}" for x in self.pose]) + ")"


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

PROJECT_MDL_DIR = pathlib.Path(".wandelbots") / "mdl"

GHOST_MATERIAL_MDL_EXT_FILE = pathlib.Path(__file__).parent / "assets" / "wb_ghost.mdl"
GHOST_MATERIAL_MDL_PROJECT_FILE = PROJECT_MDL_DIR / "wb_ghost.mdl"


SHADER_IDENTIFIER = "GhostTeaching"

GIZMO_USD_FILE = str(pathlib.Path(__file__).parent / "assets" / "gizmo.usd")
