from typing import Any

import carb
import pydantic
from isaacsim.core.prims import SingleArticulation
from pxr import Sdf, Usd, UsdPhysics

import wandelbots.usd as wb_schema  # type: ignore
from wandelbots.omni.manipulators.motion_stream_configuration import (
    MotionStreamConfiguration,
)
from wandelbots.omni.manipulators.articulation_cache import get_articulation_cache
from wandelbots.omni.utils.auth import validate_request
from wandelbots_api_client.v2.models import (
    MotionGroupDescription,
    KinematicModel,
    DHParameter,
)
from wandelbots_api_client.v2.api import (
    MotionGroupApi,
    MotionGroupModelsApi,
)

from wandelbots.omni.utils.api import get_api_client_from_config


def get_root_articulation_path(prim: Usd.Prim) -> str:
    current_prim = prim
    visited_prims = set()

    while current_prim is not None:
        prim_path = current_prim.GetPath()

        if prim_path in visited_prims:
            carb.log_warn(
                f"Circular reference detected in motion group chain at {prim_path}. "
                f"Returning current prim as root."
            )
            return current_prim.GetPath().pathString
        visited_prims.add(prim_path)

        root_joint_prim = current_prim.GetChild("root_joint")
        if not root_joint_prim.IsValid():
            carb.log_warn(
                f"No valid root_joint found for prim {current_prim.GetPath()}. "
                f"Returning current prim as root."
            )
            return current_prim.GetPath().pathString

        joint = UsdPhysics.Joint(root_joint_prim)

        body0_rel = joint.GetBody0Rel()
        if not body0_rel:
            return current_prim.GetPath().pathString

        body0_targets = body0_rel.GetTargets()
        if not body0_targets:
            return current_prim.GetPath().pathString

        body0_prim = current_prim.GetStage().GetPrimAtPath(body0_targets[0])
        if not body0_prim.IsValid():
            return current_prim.GetPath().pathString

        parent_prim = body0_prim.GetParent()
        if not parent_prim or not UsdPhysics.ArticulationRootAPI(parent_prim):
            return current_prim.GetPath().pathString

        # Continue traversal with parent
        current_prim = parent_prim

    return current_prim.GetPath().pathString


def find_physx_articulation_path(prim: Usd.Prim) -> str:
    """Return the path PhysX actually registers the articulation under.

    ``ArticulationRootAPI`` is conventionally applied to a parent Xform that
    has no ``RigidBodyAPI`` itself.  PhysX registers (and pattern-matches)
    articulations against rigid-body prims, so passing the Xform path to
    ``SingleArticulation`` fails with "did not match any rigid bodies".
    This function descends breadth-first to the first ``RigidBodyAPI``
    descendant — which is the prim PhysX actually uses as the anchor — and
    returns its path.  If the prim itself already has ``RigidBodyAPI`` the
    path is returned unchanged.
    """
    if UsdPhysics.RigidBodyAPI(prim):
        return prim.GetPath().pathString

    queue = list(prim.GetChildren())
    while queue:
        child = queue.pop(0)
        if UsdPhysics.RigidBodyAPI(child):
            return child.GetPath().pathString
        queue.extend(child.GetChildren())

    carb.log_warn(
        f"No RigidBodyAPI descendant found under {prim.GetPath()}. "
        f"Using prim path directly."
    )
    return prim.GetPath().pathString


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

    async def check_connection(self):
        self._api_configuration = (
            self.motion_stream_configuration.get_api_configuration()
        )
        request_url = f"{self._api_configuration.base_url}/cells/{self.motion_stream_configuration.cell}/controllers/{self.motion_stream_configuration.controller}/motion-groups/{self.motion_stream_configuration.motion_group}/state"
        await validate_request(self._api_configuration.access_token, request_url)


class MotionGroup:
    def __init__(self, stage: Usd.Stage, configuration: MotionGroupConfiguration):
        self._configuration = configuration
        self._stage = stage
        self._dh_parameters: list[DHParameter] = []
        self._validate(stage)

        motion_group_prim = stage.GetPrimAtPath(Sdf.Path(configuration.prim_path))
        usd_root_path = get_root_articulation_path(motion_group_prim)
        usd_root_prim = stage.GetPrimAtPath(Sdf.Path(usd_root_path))
        self._articulation_cache_handle = get_articulation_cache().get_articulation(
            find_physx_articulation_path(usd_root_prim)
        )

    @property
    def identifier(self) -> str:
        return self._configuration.prim_path

    @property
    def articulation(self) -> SingleArticulation | None:
        return self._articulation_cache_handle.articulation

    @property
    def configuration(self) -> MotionGroupConfiguration:
        return self._configuration

    @property
    def to_dict(self) -> dict[str, Any]:
        return dict(self._configuration)

    @property
    def motion_group_dh_parameters(self) -> list[DHParameter]:
        return self._dh_parameters

    async def get_dh_parameters(self) -> None:
        self._dh_parameters = await get_motion_group_dhparameters_from_prim(
            self._stage.GetPrimAtPath(Sdf.Path(self._configuration.prim_path))
        )

    def _validate(self, stage: Usd.Stage):
        if not stage.GetPrimAtPath(Sdf.Path(self._configuration.prim_path)).IsValid():
            raise ValueError(
                f"Given {self._configuration.prim_path} is not a valid prim path in the stage"
            )


async def get_motion_group_dhparameters_from_prim(
    prim: Usd.Prim,
) -> list[DHParameter]:
    try:
        motion_group_configuration = get_motion_group_configuration_from_prim(prim)
        stream_config = motion_group_configuration.motion_stream_configuration

        async with get_api_client_from_config(
            stream_config.get_api_configuration()
        ) as api:
            motion_group_description: MotionGroupDescription = await MotionGroupApi(
                api
            ).get_motion_group_description(
                cell=stream_config.cell,
                controller=stream_config.controller,
                motion_group=stream_config.motion_group,
            )

            motion_group_kinematics: KinematicModel = await MotionGroupModelsApi(
                api
            ).get_motion_group_kinematic_model(
                motion_group_model=motion_group_description.motion_group_model,
            )

            return motion_group_kinematics.dh_parameters
    except Exception as ex:
        carb.log_error(f"Failed to get motion group kinetmatics: {ex}")
        return []


def get_motion_group_configuration_from_prim(
    prim: Usd.Prim,
) -> MotionGroupConfiguration | None:
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
