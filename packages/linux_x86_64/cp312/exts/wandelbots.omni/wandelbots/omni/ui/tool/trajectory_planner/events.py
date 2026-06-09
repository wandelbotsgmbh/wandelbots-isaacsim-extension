"""Typed event bus for a single trajectory planner skill instance."""

from __future__ import annotations

from dataclasses import dataclass, field

from wandelbots.omni.ui.signal import Signal


@dataclass
class TrajectoryPlannerEvents:
    # Robot setup
    motion_group_changed: Signal = field(default_factory=Signal)
    tcp_changed: Signal = field(default_factory=Signal)
    collision_setup_changed: Signal = field(default_factory=Signal)

    # Settings
    setting_changed: Signal = field(default_factory=Signal)

    # Button actions
    calculate_iks_requested: Signal = field(default_factory=Signal)
    plan_requested: Signal = field(default_factory=Signal)
    replan_requested: Signal = field(default_factory=Signal)
    execute_toggle_requested: Signal = field(default_factory=Signal)
    force_stop_requested: Signal = field(default_factory=Signal)

    # Pose list
    pose_added: Signal = field(default_factory=Signal)
    pose_removed: Signal = field(default_factory=Signal)
    poses_reordered: Signal = field(default_factory=Signal)
    motion_type_changed: Signal = field(default_factory=Signal)

    # Delegate inline actions
    inline_config_changed: Signal = field(default_factory=Signal)
    pose_settings_clicked: Signal = field(default_factory=Signal)

    # IK
    ik_progress: Signal = field(default_factory=Signal)
    ik_complete: Signal = field(default_factory=Signal)
    reachability_complete: Signal = field(default_factory=Signal)

    # Planning
    plan_started: Signal = field(default_factory=Signal)
    plan_progress: Signal = field(default_factory=Signal)
    plan_complete: Signal = field(default_factory=Signal)
    plan_failed: Signal = field(default_factory=Signal)
    plan_stored: Signal = field(default_factory=Signal)  # emits the export version tag

    # Execution
    execution_started: Signal = field(default_factory=Signal)
    execution_paused: Signal = field(default_factory=Signal)
    execution_progress: Signal = field(default_factory=Signal)
    execution_joint_update: Signal = field(default_factory=Signal)
    execution_location: Signal = field(default_factory=Signal)
    execution_complete: Signal = field(default_factory=Signal)
    execution_cancelled: Signal = field(default_factory=Signal)
    execution_failed: Signal = field(default_factory=Signal)

    def clear_all(self) -> None:
        for signal in self.__dict__.values():
            if isinstance(signal, Signal):
                signal.clear()
