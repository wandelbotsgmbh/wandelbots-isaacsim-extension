from dataclasses import dataclass
from typing import Any

import carb
import pydantic
from isaacsim.core.prims import SingleArticulation
from wandelbots.omni.manipulators.motion_stream_configuration import (
    MotionStreamConfiguration,
)
from wandelbots.omni.utils.api import ApiConfiguration
from wandelbots.omni.utils.auth import validate_request
from pxr import Usd, Sdf
import wandelbots.usd as wb_schema


class MotionGroupConfiguration(pydantic.BaseModel):
    name: str | None = pydantic.Field(
        description="DEPRECATED: Name of motion group. This field is deprecated and the backend will ignore this field. We used this field to identify motion groups, but now we use the prim_path instead.",
        deprecated=True,
    )
    prim_path: str = pydantic.Field(
        example="/World/universalrobots_ur10e",
        description="Path to motion-group prim with ArticulationRootApi",
    )
    enabled: bool = pydantic.Field(
        default=True,
        description="Whether the motion group is connecting to a motion stream on simulation start",
    )
    motion_stream_configuration: MotionStreamConfiguration

    def apply_to_prim(self, stage: Usd.Stage) -> None:
        """Only applies attributes to prim if it has MotionGroupAPI"""
        carb.log_verbose(f"Applying configuration to prim: {self.prim_path}")

        prim = stage.GetPrimAtPath(Sdf.Path(self.prim_path))
        if not prim.HasAPI(wb_schema.MotionGroupAPI):
            prim.ApplyAPI(wb_schema.MotionGroupAPI)

        motion_group_api = wb_schema.MotionGroupAPI.Get(prim.GetStage(), prim.GetPath())

        motion_group_api.GetEnabledAttr().Set(self.enabled)
        motion_group_api.GetHostAttr().Set(self.motion_stream_configuration.host)
        motion_group_api.GetSecureAttr().Set(
            self.motion_stream_configuration.secure_connection
        )
        motion_group_api.GetCellAttr().Set(self.motion_stream_configuration.cell)

        motion_group_api.GetControllerAttr().Set(
            self.motion_stream_configuration.controller
        )
        motion_group_api.GetMotionGroupAttr().Set(
            self.motion_stream_configuration.motion_group
        )
        motion_group_api.GetExternalJointStreamAttr().Set(
            self.motion_stream_configuration.use_external_joint_stream
        )
        motion_group_api.GetResponseRateAttr().Set(
            self.motion_stream_configuration.response_rate
        )

    def refresh_from_prim(self, stage: Usd.Stage) -> None:
        """Refreshes the configuration from the prim if it has MotionGroupAPI"""
        carb.log_verbose(f"Refreshing configuration from prim: {self.prim_path}")

        prim = stage.GetPrimAtPath(Sdf.Path(self.prim_path))
        if not prim.HasAPI(wb_schema.MotionGroupAPI):
            raise ValueError(
                f"Prim {self.prim_path} does not have MotionGroupAPI applied"
            )

        motion_group_api = wb_schema.MotionGroupAPI.Get(prim.GetStage(), prim.GetPath())

        self.enabled = motion_group_api.GetEnabledAttr().Get()
        self.motion_stream_configuration.host = motion_group_api.GetHostAttr().Get()
        self.motion_stream_configuration.secure_connection = (
            motion_group_api.GetSecureAttr().Get()
        )
        self.motion_stream_configuration.cell = motion_group_api.GetCellAttr().Get()
        self.motion_stream_configuration.controller = (
            motion_group_api.GetControllerAttr().Get()
        )
        self.motion_stream_configuration.motion_group = (
            motion_group_api.GetMotionGroupAttr().Get()
        )
        self.motion_stream_configuration.use_external_joint_stream = (
            motion_group_api.GetExternalJointStreamAttr().Get()
        )
        self.motion_stream_configuration.response_rate = (
            motion_group_api.GetResponseRateAttr().Get()
        )

    @property
    def identifier(self) -> str:
        return self.prim_path

    async def check_connection(self, token: str | None):
        self._api_configuration = ApiConfiguration(
            host=self.motion_stream_configuration.host,
            secure_connection=self.motion_stream_configuration.secure_connection,
            access_token=token,
            version="v1",
        )
        request_url = f"{self._api_configuration.base_url}/cells/{self.motion_stream_configuration.cell}/controllers/{self.motion_stream_configuration.controller}/state"
        await validate_request(token, request_url)


@dataclass
class MotionGroup:
    _configuration: MotionGroupConfiguration

    def __init__(self, stage: Usd.Stage, configuration: MotionGroupConfiguration):
        self._configuration = configuration

        self._validate(stage)
        try:
            self._articulation = SingleArticulation(self._configuration.prim_path)
        except Exception as e:
            raise ValueError(
                f"Articulation cannot be applied to motion-group at path {self._configuration.prim_path}"
            ) from e

    @property
    def identifier(self) -> str:
        return self._configuration.prim_path

    @property
    def articulation(self) -> SingleArticulation:
        return self._articulation

    @property
    def configuration(self) -> MotionGroupConfiguration:
        return self._configuration

    @property
    def to_dict(self) -> dict[str, Any]:
        return dict(self._configuration)

    def _validate(self, stage: Usd.Stage):
        if not stage.GetPrimAtPath(Sdf.Path(self._configuration.prim_path)).IsValid():
            raise ValueError(
                f"Given {self._configuration.prim_path} is not a valid prim path in the stage"
            )


def get_motion_group_configuration_from_prim(
    prim: Usd.Prim,
) -> MotionGroupConfiguration:
    if not prim.HasAPI(wb_schema.MotionGroupAPI):
        return None

    motion_group_api = wb_schema.MotionGroupAPI.Get(prim.GetStage(), prim.GetPath())

    return MotionGroupConfiguration(
        name=motion_group_api.GetMotionGroupAttr().Get(),
        prim_path=prim.GetPath().pathString,
        enabled=motion_group_api.GetEnabledAttr().Get(),
        motion_stream_configuration=MotionStreamConfiguration(
            host=motion_group_api.GetHostAttr().Get(),
            secure_connection=motion_group_api.GetSecureAttr().Get(),
            cell=motion_group_api.GetCellAttr().Get(),
            controller=motion_group_api.GetControllerAttr().Get(),
            motion_group=motion_group_api.GetMotionGroupAttr().Get(),
            response_rate=motion_group_api.GetResponseRateAttr().Get(),
            use_external_joint_stream=motion_group_api.GetExternalJointStreamAttr().Get(),
        ),
    )


def is_prim_motion_group(prim: Usd.Prim) -> bool:
    """Check if the given prim is a motion group."""
    return prim.HasAPI(wb_schema.MotionGroupAPI)
