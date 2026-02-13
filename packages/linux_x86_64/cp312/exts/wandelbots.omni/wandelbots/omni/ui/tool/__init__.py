from .nova_tcp import (
    register_tcp_from_isaac_to_nova_menu,
    register_tcp_from_nova_to_isaac_menu,
)
from .collision_setup.collision_setup_window import register_collision_setup_window
from .action_planner.action_planner_window import register_action_planner_window
from .ghost_teaching.ghost_teaching_tool_bar import register_ghost_teaching_tool_bar


def register_tools():
    return [
        register_collision_setup_window(),
        register_action_planner_window(),
        register_ghost_teaching_tool_bar(),
        register_tcp_from_isaac_to_nova_menu(),
        register_tcp_from_nova_to_isaac_menu(),
    ]
