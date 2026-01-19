from .ui import ui_router
from .manipulators import motion_groups_router
from .periphery import cameras_router
from .stage import stage_router, collision_world_router
from .teaching import teaching_router
from .trajectory import trajectory_router
from .prims import prims_router, colliders_router
from .nucleus import nucleus_router

__all__ = [
    "prims_router",
    "cameras_router",
    "stage_router",
    "teaching_router",
    "ui_router",
    "motion_groups_router",
    "trajectory_router",
    "collision_world_router",
    "colliders_router",
    "nucleus_router",
]
