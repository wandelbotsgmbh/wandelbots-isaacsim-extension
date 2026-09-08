from typing import Optional

from wandelbots.omni.manipulators import MotionGroupConfiguration
from wandelbots.omni.manipulators.motion_group import MotionStreamConfiguration
from wandelbots.omni.ui.instances.models.motion_group_bool_model import (
    MotionGroupBoolModel,
)


class ExternalJointStreamModel(MotionGroupBoolModel):
    """Bool model for the use_external_joint_stream flag of a motion group."""

    @property
    def motion_stream_configuration(self) -> Optional[MotionStreamConfiguration]:
        config = self.motion_group_configuration
        return config.motion_stream_configuration if config else None

    def _read_value(self, config: MotionGroupConfiguration) -> bool:
        return config.motion_stream_configuration.use_external_joint_stream

    def _write_value(self, config: MotionGroupConfiguration, value: bool) -> None:
        config.motion_stream_configuration.use_external_joint_stream = value
