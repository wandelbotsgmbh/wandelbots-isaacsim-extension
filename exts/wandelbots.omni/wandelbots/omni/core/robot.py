import pydantic
from dataclasses import dataclass
from typing import Any, Dict, List
from omni.isaac.core.articulations import Articulation
import omni.isaac.core.utils.stage as stage_utils
from wandelbots.omni.utils.auth import validate_request


@dataclass
class ConfigurableRobot:
    class Configuration(pydantic.BaseModel):
        identifier: str
        host: str
        cell: str
        controller_id: str
        motion_group_id: int
        prim_path: str
        is_secured: bool = False

    _configuration: Configuration

    def __init__(self, configuration: Configuration, **kwargs):
        super().__init__(**kwargs)
        self._configuration = configuration
        self._validate()
        try:
            self._articulation = Articulation(self._configuration.prim_path)
        except Exception as e:
            raise ValueError(
                f"Articulation cannot be applied to robot at path {self._configuration.prim_path}"
            ) from e
        self._connected_tools = []

    async def check_connection(self, token: str | None):
        protocol = "https" if self._configuration.is_secured else "http"
        self.base_url = f"{protocol}://{self._configuration.host}/api/v1/cells/{self._configuration.cell}/controllers/{self._configuration.controller_id}/state"
        await validate_request(token, self.base_url)

    @property
    def articulation(self):
        return self._articulation

    @classmethod
    def from_dict(cls, config: Dict):
        return cls.Configuration.parse_obj(config)

    @property
    def to_dict(self) -> dict[str, Any]:
        return dict(self._configuration)

    def _validate(self):
        self.stage_prims = [
            prim.GetPrimPath().pathString for prim in stage_utils.traverse_stage()
        ]
        if self._configuration.prim_path not in self.stage_prims:
            raise ValueError(
                f"Given {self._configuration.prim_path} is not a valid prim path in the stage"
            )

    def connect_tools(self, tools: List | str):
        self._connected_tools.append(tools) if isinstance(
            tools, str
        ) else self._connected_tools.extend(tools)

    @property
    def connected_tools(self) -> List:
        return self._connected_tools
