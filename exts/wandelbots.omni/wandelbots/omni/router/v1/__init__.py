from .object import object_router
from .camera import camera_router
from .stream import stream_router
from .scene import scene_router
from .robot import robot_router
from .tool import tool_router
from .configuration import configuration_router
from .ghost_teaching import ghost_teaching_router
from .ui import ui_router

__all__ = [
    "object_router",
    "camera_router",
    "stream_router",
    "scene_router",
    "robot_router",
    "tool_router",
    "configuration_router",
    "ghost_teaching_router",
    "ui_router",
]
