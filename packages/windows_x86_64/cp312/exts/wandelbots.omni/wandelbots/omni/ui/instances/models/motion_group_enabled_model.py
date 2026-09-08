from wandelbots.omni.manipulators import MotionGroupConfiguration
from wandelbots.omni.ui.instances.models.motion_group_bool_model import (
    MotionGroupBoolModel,
)


class MotionGroupEnabledModel(MotionGroupBoolModel):
    """Bool model for the enabled flag of a motion group."""

    def _read_value(self, config: MotionGroupConfiguration) -> bool:
        return config.enabled

    def _write_value(self, config: MotionGroupConfiguration, value: bool) -> None:
        config.enabled = value
