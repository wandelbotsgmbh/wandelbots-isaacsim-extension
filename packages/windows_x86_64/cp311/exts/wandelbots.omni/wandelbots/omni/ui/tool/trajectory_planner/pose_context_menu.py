"""Context menu entry for creating trajectory pose prims with embedded gizmo."""

from __future__ import annotations

import carb
import omni.usd

from wandelbots.omni.ui.tool.trajectory_planner.pose_utils import create_pose_prim
from wandelbots.omni.ui.utils import get_icon


def _create_trajectory_pose(payload: dict) -> None:
    stage = omni.usd.get_context().get_stage()
    if not stage:
        carb.log_warn("No active stage to create trajectory pose.")
        return

    prim_path = create_pose_prim(stage)
    if prim_path:
        carb.log_info(f"Created trajectory pose (embedded): {prim_path}")
        omni.usd.get_context().get_selection().set_selected_prim_paths(
            [prim_path], True
        )


def register_trajectory_pose_context_menu():
    import omni.kit.context_menu

    create_menu_dict = {
        "name": {
            "Wandelbots NOVA": [
                {
                    "name": "Pose",
                    "onclick_fn": _create_trajectory_pose,
                },
            ]
        },
        "glyph": get_icon("wandelbots.png"),
    }
    return omni.kit.context_menu.add_menu(create_menu_dict, "CREATE")
