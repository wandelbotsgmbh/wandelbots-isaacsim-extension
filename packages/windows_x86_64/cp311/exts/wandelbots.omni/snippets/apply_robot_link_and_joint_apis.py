# Wandelbots NOVA/Apply Robot, Link and Joint APIs

import omni.usd
import isaacsim.core.utils.stage as stage_utils
from wandelbots.omni.manipulators.utils import _apply_isaac_robot_schema

motion_group_prim_path = (
    omni.usd.get_context().get_selection().get_selected_prim_paths()[0]
)
stage = stage_utils.get_current_stage()
motion_group_prim = stage.GetPrimAtPath(motion_group_prim_path)

_apply_isaac_robot_schema(motion_group_prim)
