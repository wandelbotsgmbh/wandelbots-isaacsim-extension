from typing import Callable, Optional

import omni.ui as ui
from pxr import Sdf, Usd, Vt

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.utils.kinematics import InverseKinematicsResult, joint_config_signs
from wandelbots.omni.utils.teaching import GhostObjectUtils, PREFERRED_JOINT_VALUES_ATTR


class JointConfigSelector:
    """Dropdown for choosing one of the available IK joint configurations.

    Args:
        ghost_object_prim: The USD prim whose IK configs are being displayed.
        initial_joint_configs: Available joint configurations (each a list of joint values).
        initial_joint_limits: Joint limits used to render +/- sign labels.
        joint_config_changed_fn: Called with the selected index when the user picks a config.
        write_to_prim: If True (default), persist the selection as ``preferredJointValues``
            on the prim. Set to False when the selection is managed externally (e.g. the
            trajectory planner stores it per-pose so the same prim can have independent
            selections).
        selected_index: Initial combo box selection when *write_to_prim* is False.
            Ignored when *write_to_prim* is True (the index is read from the prim instead).
    """

    def __init__(
        self,
        ghost_object_prim: Usd.Prim,
        initial_joint_configs: list[list[float]],
        initial_joint_limits: list[tuple[float, float]] | None = None,
        joint_config_changed_fn: Optional[Callable[[Optional[int]], None]] = None,
        write_to_prim: bool = True,
        selected_index: int | None = None,
    ):
        self._ghost_object_prim = ghost_object_prim
        self._joint_config_changed_fn = joint_config_changed_fn
        self._joint_configs: list[list[float]] = list(initial_joint_configs)
        self._joint_limits: list[tuple[float, float]] = list(initial_joint_limits or [])
        self._write_to_prim = write_to_prim
        self._selected_index = selected_index
        self._frame = ui.Frame(width=ui.Fraction(1))
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
        if self._write_to_prim:
            stored_idx = self._read_preferred_index()
        else:
            stored_idx = self._selected_index
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

    def _read_preferred_index(self) -> int | None:
        stored = GhostObjectUtils.get_preferred_joint_values(self._ghost_object_prim)
        if stored is None:
            return None
        return GhostObjectUtils.find_preferred_config_index(self._joint_configs, stored)

    def _on_selection(self, idx: int):
        if 0 <= idx < len(self._joint_configs):
            if self._write_to_prim:
                self._write_preferred_joint_values(self._joint_configs[idx])
            self._selected_index = idx
            if self._joint_config_changed_fn:
                self._joint_config_changed_fn(idx)
