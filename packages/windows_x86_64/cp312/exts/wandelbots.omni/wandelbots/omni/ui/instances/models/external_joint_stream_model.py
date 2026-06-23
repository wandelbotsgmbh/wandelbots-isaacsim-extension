import carb
import omni.ui as ui
from omni.usd import get_watcher
import isaacsim.core.utils.stage as stage_utils
from pxr import Sdf
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
)
import weakref
from wandelbots.omni.manipulators.motion_group import (
    get_motion_group_configuration_from_prim,
    MotionStreamConfiguration,
)


class ExternalJointStreamModel(ui.SimpleBoolModel):
    def __init__(self, motion_group_prim_path: str, **kwargs):
        self._stage = stage_utils.get_current_stage()
        self._motion_group_prim_path = motion_group_prim_path

        def _on_motion_group_changed(path=None, weak_self=weakref.ref(self)):
            weak_self_instance = weak_self()
            if weak_self_instance:
                weak_self_instance._on_motion_group_changed()

        self._change_subscription = get_watcher().subscribe_to_change_info_path(
            motion_group_prim_path,  # use prim path so we do not have to sync property names in future versions
            _on_motion_group_changed,
        )
        super().__init__(self._get_prim_value(), **kwargs)

        def _value_changed_wrapper(
            model: ui.AbstractValueModel, weak_self=weakref.ref(self)
        ):
            weak_self_instance = weak_self()
            if weak_self_instance:
                weak_self_instance._set_prim_value(model.get_value_as_bool())

        self.add_value_changed_fn(_value_changed_wrapper)

    def __del__(self):
        carb.log_verbose("Unsubscribing from motion group prim changes.")
        self._change_subscription.unsubscribe()

    def _on_motion_group_changed(self):
        new_value = self._get_prim_value()
        if new_value != self.get_value_as_bool():
            self.set_value(new_value)

    @property
    def motion_group_configuration(self) -> MotionGroupConfiguration:
        motion_group_prim = self._stage.GetPrimAtPath(
            Sdf.Path(self._motion_group_prim_path)
        )
        return get_motion_group_configuration_from_prim(motion_group_prim)

    @property
    def motion_stream_configuration(self) -> MotionStreamConfiguration:
        return self.motion_group_configuration.motion_stream_configuration

    def _get_prim_value(self) -> bool:
        return self.motion_stream_configuration.use_external_joint_stream

    def _set_prim_value(self, value: bool):
        motion_group_prim = self._stage.GetPrimAtPath(
            Sdf.Path(self._motion_group_prim_path)
        )
        motion_group_config = get_motion_group_configuration_from_prim(
            motion_group_prim
        )
        if not motion_group_prim:
            carb.log_warn(
                f"Motion group prim not found at path: {self._motion_group_prim_path}"
            )
            return

        if (
            motion_group_config.motion_stream_configuration.use_external_joint_stream
            == value
        ):
            return

        motion_group_config.motion_stream_configuration.use_external_joint_stream = (
            value
        )
        motion_group_config.apply_to_prim(self._stage)
