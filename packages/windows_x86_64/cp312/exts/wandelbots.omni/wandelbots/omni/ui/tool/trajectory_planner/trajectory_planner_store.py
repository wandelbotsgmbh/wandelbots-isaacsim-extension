"""Persistent storage for trajectory planner configurations."""

from __future__ import annotations

import carb
from pydantic import BaseModel, Field, field_validator

from wandelbots.omni.utils.database import BaseStore

#: Smallest step RRT-Connect can still extend its trees with; below this the
#: search stops making progress. Shared with the settings section.
CF_STEP_SIZE_MIN = 0.01


# Fields unique to BlendingPosition (BlendingAuto only ever has
# `min_velocity_in_percent`). Used to backfill the `blending_name`
# discriminator on blending dicts saved before the NOVA API client
# required it.
_BLENDING_POSITION_KEYS = {
    "position_zone_radius",
    "position_zone_percentage",
    "orientation_zone_radius",
    "orientation_zone_percentage",
    "joints_zone_radius",
    "joints_zone_percentage",
    "space",
}


def migrate_blending_dict(d: dict | None) -> dict | None:
    """Backfill the `blending_name` discriminator on a legacy blending dict.

    Configs saved before the client that introduced this discriminator
    (MotionCommandBlending.from_dict requires it) don't have it, so
    reopening or replanning a pre-upgrade skill would otherwise raise.
    """
    if d is None or "blending_name" in d:
        return d
    blending_name = (
        "BlendingPosition" if _BLENDING_POSITION_KEYS & d.keys() else "BlendingAuto"
    )
    return {**d, "blending_name": blending_name}


class PoseConfig(BaseModel):
    prim_path: str
    motion_type: str = "PathCartesianPTP"
    selected_joint_config: list[float] | None = None
    joint_configs: list[list[float]] = Field(default_factory=list)
    selected_config_idx: int = 0
    is_ghost_object: bool = False
    tcp_name: str | None = None  # per-pose TCP override; falls back to skill tcp_name
    blending: dict | None = None  # serialized MotionCommandBlending.to_dict()
    limits_override: dict | None = None  # serialized LimitsOverride.to_dict()

    @field_validator("blending", mode="before")
    @classmethod
    def _backfill_blending_name(cls, v):
        return migrate_blending_dict(v) if isinstance(v, dict) else v


class PlannedTrajectoryConfig(BaseModel):
    joint_positions: list[list[float]] = Field(default_factory=list)
    locations: list[float] = Field(default_factory=list)
    times: list[float] = Field(default_factory=list)
    collision_free: bool = False
    # Via points the collision-free planner inserted; drawn as markers on the curve.
    via_joint_positions: list[list[float]] | None = None


class TrajectoryPlannerConfig(BaseModel):
    name: str
    robot_prim_path: str | None = None
    tcp_name: str | None = None
    collision_setup: str | None = None
    poses: list[PoseConfig] = Field(default_factory=list)
    live_update: bool = False
    overlay_color: list[float] = Field(default_factory=lambda: [0.4, 1.0, 0.4])
    trajectory_color: list[float] = Field(default_factory=lambda: [0.808, 0.0, 0.345])
    # When True, color the trajectory curve by TCP speed (green=fast, red=slow);
    # when False, use the solid trajectory_color. Off by default — the per-vertex
    # gradient is heavier to render.
    velocity_coloring: bool = False
    tcp_velocity: float = 500.0
    tcp_acceleration: float = 2000.0
    auto_blending: bool = False
    blending_min_velocity_percent: int = 50
    global_blending: dict | None = None  # serialized MotionCommandBlending.to_dict()
    global_limits_override: dict | None = None  # serialized LimitsOverride.to_dict()

    @field_validator("global_blending", mode="before")
    @classmethod
    def _backfill_blending_name(cls, v):
        return migrate_blending_dict(v) if isinstance(v, dict) else v

    payload_name: str = ""
    payload_mass: float = 0.0
    cf_max_iterations: int = 10000
    # None, which is also every config saved before this field existed, leaves
    # the step size to RRT-Connect so those configs plan exactly as before.
    cf_step_size: float | None = None

    @field_validator("cf_step_size")
    @classmethod
    def _clamp_cf_step_size(cls, value: float | None) -> float | None:
        # A config edited by hand or written by an older build can hold a value
        # the settings field would never have allowed.
        if value is not None and value < CF_STEP_SIZE_MIN:
            return CF_STEP_SIZE_MIN
        return value

    # Whether to run collision-free planning. Independent of collision_setup: a
    # collision scene can be active while normal (motion-type) planning is used.
    plan_collision_free: bool = False
    move_to_start: bool = False
    collapsed: bool = False
    poses_collapsed: bool = False
    planned_trajectory: PlannedTrajectoryConfig | None = None
    # Visualization-only adjustments. None of these affect the planned motion.
    # Prim the trajectory is anchored to (None = robot base).
    reference_frame_path: str | None = None
    # Mounting offset: translation (mm) and rotation (degrees, extrinsic XYZ)
    # applied to correct the trajectory's placement when the robot is mounted
    # on an external axis or otherwise offset from the reference frame.
    mounting_offset: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    mounting_rotation: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class TrajectoryPlannerStore(BaseStore):
    def __init__(self):
        super().__init__(file_name="trajectory_planner.json")

    def save_configs(self, configs: list[TrajectoryPlannerConfig]) -> None:
        self._data = {"skills": [c.model_dump() for c in configs]}
        self.save_data()

    def load_configs(self) -> list[TrajectoryPlannerConfig]:
        skills = self._data.get("skills", self._data.get("sections", []))
        configs = []
        for entry in skills:
            try:
                configs.append(TrajectoryPlannerConfig(**entry))
            except Exception as exc:
                carb.log_warn(f"Failed to load trajectory planner config: {exc}")
        return configs


_store = TrajectoryPlannerStore()


def get_trajectory_planner_store() -> TrajectoryPlannerStore:
    return _store
