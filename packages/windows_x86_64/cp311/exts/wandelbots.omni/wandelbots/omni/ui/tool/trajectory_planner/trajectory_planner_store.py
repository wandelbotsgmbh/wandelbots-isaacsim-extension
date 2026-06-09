"""Persistent storage for trajectory planner configurations."""

from __future__ import annotations

import carb
from pydantic import BaseModel, Field

from wandelbots.omni.utils.database import BaseStore


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


class PlannedTrajectoryConfig(BaseModel):
    joint_positions: list[list[float]] = Field(default_factory=list)
    locations: list[float] = Field(default_factory=list)
    times: list[float] = Field(default_factory=list)
    collision_free: bool = False


class TrajectoryPlannerConfig(BaseModel):
    name: str
    robot_prim_path: str | None = None
    tcp_name: str | None = None
    collision_setup: str | None = None
    poses: list[PoseConfig] = Field(default_factory=list)
    live_update: bool = False
    overlay_color: list[float] = Field(default_factory=lambda: [0.4, 1.0, 0.4])
    trajectory_color: list[float] = Field(default_factory=lambda: [0.808, 0.0, 0.345])
    tcp_velocity: float = 500.0
    tcp_acceleration: float = 2000.0
    auto_blending: bool = False
    blending_min_velocity_percent: int = 50
    global_blending: dict | None = None  # serialized MotionCommandBlending.to_dict()
    global_limits_override: dict | None = None  # serialized LimitsOverride.to_dict()
    payload_name: str = ""
    payload_mass: float = 0.0
    cf_algorithm: str = "RRTConnectAlgorithm"
    cf_max_iterations: int = 10000
    move_to_start: bool = False
    collapsed: bool = False
    poses_collapsed: bool = False
    planned_trajectory: PlannedTrajectoryConfig | None = None


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
