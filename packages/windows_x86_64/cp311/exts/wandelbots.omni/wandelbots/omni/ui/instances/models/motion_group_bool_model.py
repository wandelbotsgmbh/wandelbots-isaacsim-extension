import weakref
from typing import Optional

import carb
import omni.ui as ui
from omni.usd import get_watcher
from pxr import Sdf
import isaacsim.core.utils.stage as stage_utils

from wandelbots.omni.manipulators import MotionGroupConfiguration
from wandelbots.omni.manipulators.motion_group import (
    get_motion_group_configuration_from_prim,
)


class MotionGroupBoolModel(ui.SimpleBoolModel):
    """Bool model backed by one flag of a motion group prim's configuration.

    Subclasses implement _read_value/_write_value for their flag. The model is
    bound to the stage it was created from; if it outlives that stage it warns
    and detaches, since that means the parent widget missed a stage change.
    An ill-formed prim path (e.g. a motion group id instead of a prim path)
    is handled the same way: no subscription, and the model stays disabled.
    Widgets rendering this model must check is_backed and disable their
    control for an unbacked model, since writes are no-ops.
    """

    def __init__(self, motion_group_prim_path: str, **kwargs):
        self._stage = stage_utils.get_current_stage()
        self._motion_group_prim_path = motion_group_prim_path
        self._change_subscription = None

        # The path may come from a stored configuration and can be ill-formed
        # (e.g. a motion group id instead of a prim path). Sdf.Path raises for
        # such strings — from inside the USD watcher callback that would spam an
        # asyncio task exception on every stage change — so never subscribe to
        # or convert an invalid path.
        self._is_backed = Sdf.Path.IsValidPathString(motion_group_prim_path)
        if self._is_backed:

            def _on_motion_group_changed(path=None, weak_self=weakref.ref(self)):
                weak_self_instance = weak_self()
                if weak_self_instance:
                    weak_self_instance._on_motion_group_changed()

            self._change_subscription = get_watcher().subscribe_to_change_info_path(
                motion_group_prim_path,  # use prim path so we do not have to sync property names in future versions
                _on_motion_group_changed,
            )
        else:
            carb.log_warn(
                f"{type(self).__name__}: '{motion_group_prim_path}' is not a "
                "valid prim path; the control will stay at its default value."
            )
        super().__init__(self._get_prim_value(), **kwargs)

        def _value_changed_wrapper(
            model: ui.AbstractValueModel, weak_self=weakref.ref(self)
        ):
            weak_self_instance = weak_self()
            if weak_self_instance:
                weak_self_instance._set_prim_value(model.get_value_as_bool())

        self.add_value_changed_fn(_value_changed_wrapper)

    @property
    def is_backed(self) -> bool:
        """False when the prim path is ill-formed: the model then never reaches
        USD and the rendering widget must disable its control."""
        return self._is_backed

    def _read_value(self, config: MotionGroupConfiguration) -> bool:
        raise NotImplementedError

    def _write_value(self, config: MotionGroupConfiguration, value: bool) -> None:
        raise NotImplementedError

    def __del__(self):
        carb.log_verbose("Unsubscribing from motion group prim changes.")
        self._unsubscribe()

    def _unsubscribe(self):
        if self._change_subscription is not None:
            self._change_subscription.unsubscribe()
            self._change_subscription = None

    def _stage_alive(self) -> bool:
        # A dead stage (calling into it raises a misleading Boost.Python
        # ArgumentError) means the parent widget missed a stage change: warn
        # and detach the stale watcher.
        if self._stage is not None and not self._stage.expired:
            return True
        carb.log_warn(
            f"{type(self).__name__} for {self._motion_group_prim_path} outlived "
            "its stage - parent widget was not rebuilt on stage change."
        )
        self._unsubscribe()
        return False

    def _on_motion_group_changed(self):
        # No config to read: the stage died, or the prim lost its MotionGroupAPI
        # (e.g. another instance connected the same articulation); skip.
        if self.motion_group_configuration is None:
            return
        new_value = self._get_prim_value()
        if new_value != self.get_value_as_bool():
            self.set_value(new_value)

    @property
    def motion_group_configuration(self) -> Optional[MotionGroupConfiguration]:
        if not self._is_backed or not self._stage_alive():
            return None
        motion_group_prim = self._stage.GetPrimAtPath(
            Sdf.Path(self._motion_group_prim_path)
        )
        # HasAPI (inside the lookup below) raises "Accessed invalid null prim"
        # for a missing prim - e.g. after the motion group was deleted - so
        # treat that case as "no configuration" instead of crashing.
        if not motion_group_prim.IsValid():
            return None
        return get_motion_group_configuration_from_prim(motion_group_prim)

    def _get_prim_value(self) -> bool:
        # The prim may carry no (valid) MotionGroupAPI, e.g. while disconnecting;
        # default to off rather than dereferencing a missing configuration.
        config = self.motion_group_configuration
        return self._read_value(config) if config else False

    def _set_prim_value(self, value: bool):
        config = self.motion_group_configuration
        if config is None:
            carb.log_warn(
                f"Motion group prim not found at path: {self._motion_group_prim_path}"
            )
            # The write never happened; snap the model back so a rendered
            # control cannot show a state that USD does not have. Guarded to
            # terminate the recursion through the value-changed callback.
            backed_value = self._get_prim_value()
            if self.get_value_as_bool() != backed_value:
                self.set_value(backed_value)
            return
        if self._read_value(config) == value:
            return
        self._write_value(config, value)
        config.apply_to_prim(self._stage)
