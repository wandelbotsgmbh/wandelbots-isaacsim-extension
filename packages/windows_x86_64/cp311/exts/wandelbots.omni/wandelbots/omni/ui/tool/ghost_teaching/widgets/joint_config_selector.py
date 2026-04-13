from typing import Callable, Optional

import omni.ui as ui
from pxr import Sdf, Usd, Vt

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.utils.kinematics import InverseKinematicsResult, joint_config_signs
from wandelbots.omni.utils.teaching import GhostObjectUtils, PREFERRED_JOINT_VALUES_ATTR


class JointConfigSelector:
    def __init__(
        self,
        ghost_object_prim: Usd.Prim,
        initial_joint_configs: list[list[float]],
        initial_joint_limits: list[tuple[float, float]] | None = None,
        joint_config_changed_fn: Optional[Callable[[Optional[int]], None]] = None,
    ):
        self._ghost_object_prim = ghost_object_prim
        self._joint_config_changed_fn = joint_config_changed_fn
        self._joint_configs: list[list[float]] = list(initial_joint_configs)
        self._joint_limits: list[tuple[float, float]] = list(initial_joint_limits or [])
        self._frame = ui.Frame(width=ui.Pixel(85))
        self._combo_sub = None

        self._build_ui()

    def update_joint_configs(self, ik_result: InverseKinematicsResult):
        self._joint_configs = list(ik_result.joint_configs)
        self._joint_limits = list(ik_result.joint_limits)
        self._build_ui()

    def _build_ui(self):
        self._frame.clear()
        with self._frame:
            if not self._joint_configs:
                self._build_no_config_label()
            else:
                self._build_combo()

    def _build_no_config_label(self):
        ui.Label(
            "No IK", style={"color": NOVAColor.TEXT_SECONDARY.color, "font_size": 12}
        )

    def _build_combo(self):
        stored_idx = self._read_preferred_index()
        no_match = stored_idx is None

        # Prepend "---" when the stored config no longer matches any IK solution
        # (e.g. after the ghost was moved) so the user knows re-selection is needed.
        labels = (["---"] if no_match else []) + [
            f"{i + 1} {joint_config_signs(self._joint_configs[i], self._joint_limits or None)}"
            for i in range(len(self._joint_configs))
        ]
        combo = ui.ComboBox(0 if no_match else stored_idx, *labels)
        self._combo_sub = combo.model.add_item_changed_fn(
            lambda model, _, _no_match=no_match: self._on_combo_changed(
                model.get_item_value_model().as_int, _no_match
            )
        )

    def _on_combo_changed(self, idx: int, no_match: bool):
        # Ignore the placeholder "---" entry.
        if no_match and idx == 0:
            return
        self._on_selection(idx - (1 if no_match else 0))

    def _write_preferred_joint_values(self, joint_values: list[float]):
        if not self._ghost_object_prim or not self._ghost_object_prim.IsValid():
            return
        attr = self._ghost_object_prim.GetAttribute(PREFERRED_JOINT_VALUES_ATTR)
        if not attr:
            attr = self._ghost_object_prim.CreateAttribute(
                PREFERRED_JOINT_VALUES_ATTR,
                Sdf.ValueTypeNames.FloatArray,
            )
        attr.Set(Vt.FloatArray(joint_values))

    def _read_preferred_index(self) -> Optional[int]:
        stored = GhostObjectUtils.get_preferred_joint_values(self._ghost_object_prim)
        if stored is None:
            return None
        for i, config in enumerate(self._joint_configs):
            if len(config) == len(stored) and all(
                abs(config_val - stored_val) < 1e-4
                for config_val, stored_val in zip(config, stored)
            ):
                return i
        return None

    def _on_selection(self, idx: int):
        if 0 <= idx < len(self._joint_configs):
            self._write_preferred_joint_values(self._joint_configs[idx])
            if self._joint_config_changed_fn:
                self._joint_config_changed_fn(idx)
