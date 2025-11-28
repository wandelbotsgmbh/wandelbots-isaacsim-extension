from .nova_tcp import register_nova_tcp_menu
from .collision_export.collision_export_window import register_collision_export_window
from .action_planner.action_planner_window import register_action_planner_window
from .ghost_teaching.ghost_teaching_tool_bar import register_ghost_teaching_tool_bar


def register_tools():
    return [
        register_collision_export_window(),
        register_action_planner_window(),
        register_ghost_teaching_tool_bar(),
        register_nova_tcp_menu(),
    ]
