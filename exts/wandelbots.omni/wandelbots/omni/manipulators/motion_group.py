from dataclasses import dataclass
from typing import Any

import omni.isaac.core.utils.stage as stage_utils
import pydantic
from omni.isaac.core.articulations import Articulation
from wandelbots.omni.manipulators.motion_stream_configuration import (
    MotionStreamConfiguration,
)
from wandelbots.omni.utils.api import ApiConfiguration
from wandelbots.omni.utils.auth import validate_request


class MotionGroupConfiguration(pydantic.BaseModel):
    name: str = pydantic.Field(example="ur10e", description="Unique id of motion-group")
    prim_path: str = pydantic.Field(
        example="/World/universalrobots_ur10e",
        description="Path to motion-group prim with ArticulationRootApi",
    )
    motion_stream_configuration: MotionStreamConfiguration


@dataclass
class MotionGroup:
    _configuration: MotionGroupConfiguration

    def __init__(self, configuration: MotionGroupConfiguration):
        self._configuration = configuration

        self._validate()
        try:
            self._articulation = Articulation(self._configuration.prim_path)
        except Exception as e:
            raise ValueError(
                f"Articulation cannot be applied to motion-group at path {self._configuration.prim_path}"
            ) from e
        self._connected_tools = []

    async def check_connection(self, token: str | None):
        self._api_configuration = ApiConfiguration(
            host=self._configuration.motion_stream_configuration.host,
            secure_connection=self._configuration.motion_stream_configuration.secure_connection,
            access_token=token,
            version="v1",
        )
        request_url = f"{self._api_configuration.base_url}/cells/{self._configuration.motion_stream_configuration.cell}/controllers/{self._configuration.motion_stream_configuration.controller_id}/state"
        await validate_request(token, request_url)

    @property
    def identifier(self) -> str:
        return self._configuration.name

    @property
    def articulation(self) -> Articulation:
        return self._articulation

    @property
    def configuration(self) -> MotionGroupConfiguration:
        return self._configuration

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

    def connect_tools(self, tools: list | str):
        self._connected_tools.append(tools) if isinstance(
            tools, str
        ) else self._connected_tools.extend(tools)

    @property
    def connected_tools(self) -> list:
        return self._connected_tools
