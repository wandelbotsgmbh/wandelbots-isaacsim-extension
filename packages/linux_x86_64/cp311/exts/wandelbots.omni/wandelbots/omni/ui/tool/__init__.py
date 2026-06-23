from wandelbots.omni.ui.tool.collision_setup.collision_setup_window import (
    register_collision_setup_window,
)
from wandelbots.omni.ui.tool.ghost_teaching.ghost_teaching_tool_bar import (
    register_ghost_teaching_tool_bar,
)
from wandelbots.omni.ui.tool.animation_recorder.animation_recorder_window import (
    register_animation_recorder_window,
)
from wandelbots.omni.ui.tool.reachability.reachability_window import (
    register_reachability_window,
)
from wandelbots.omni.ui.tool.mounting_assistant import (
    register_mounting_assistant_window,
)
from wandelbots.omni.ui.tool.camera_near_clip_check import (
    register_camera_near_clip_check,
)
from wandelbots.omni.ui.tool.trajectory_planner import (
    register_trajectory_planner_window,
)
from wandelbots.omni.ui.tool.collider_list import register_collider_list_window


def register_tools():
    return [
        register_collision_setup_window(),
        register_ghost_teaching_tool_bar(),
        register_animation_recorder_window(),
        register_reachability_window(),
        register_mounting_assistant_window(),
        register_camera_near_clip_check(),
        register_trajectory_planner_window(),
        register_collider_list_window(),
    ]
