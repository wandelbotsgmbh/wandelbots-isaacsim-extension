"""Pose list CRUD operations for the trajectory planner TreeView."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Callable

import carb
import omni.kit.clipboard
import omni.kit.notification_manager as nm
import omni.usd
from omni.kit.async_engine import run_coroutine
from pxr import UsdGeom

import wandelbots.usd as wb_schema  # type: ignore

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.ui.dialogs import PrimSelectDialog
from wandelbots.omni.ui.tool.trajectory_planner.pose_utils import create_pose_prim
from wandelbots.omni.ui.tool.trajectory_planner.pose_tree_widget import (
    PoseItem,
    PoseModel,
)
from wandelbots.omni.utils.teaching import make_ghost_tcp_matcher

if TYPE_CHECKING:
    from wandelbots.omni.ui.tool.trajectory_planner.events import (
        TrajectoryPlannerEvents,
    )


class PoseListManager:
    """Manages adding, removing, reordering, and copying poses in a PoseModel."""

    def __init__(
        self,
        pose_model: PoseModel,
        get_pose_relative_to_mg: Callable[[str], WSPose],
        events: "TrajectoryPlannerEvents",
        get_nova_tcps: Callable[[], dict] | None = None,
    ) -> None:
        self._pose_model = pose_model
        self._get_pose_relative_to_mg = get_pose_relative_to_mg
        self._events = events
        self._get_nova_tcps = get_nova_tcps
        self._pose_dialog: PrimSelectDialog | None = None

    def add_pose(self) -> None:
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return

        prim_path = create_pose_prim(stage)
        if not prim_path:
            return

        pose_name = stage.GetPrimAtPath(prim_path).GetName()
        pose = WSPose(pose=[0, 0, 0, 0, 0, 0])
        item = self._pose_model.add_pose(prim_path=prim_path, name=pose_name, pose=pose)
        omni.usd.get_context().get_selection().set_selected_prim_paths(
            [prim_path], True
        )
        self._events.pose_added.emit(item)

    def add_from_selection(self) -> None:
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        selected = omni.usd.get_context().get_selection().get_selected_prim_paths()
        if not selected:
            return
        added_items: list[PoseItem] = []
        for prim_path in selected:
            prim = stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                continue
            if not self.is_pose_prim(prim):
                continue
            is_ghost = self._is_ghost_object_prim(prim)
            try:
                pose = self._get_pose_relative_to_mg(prim_path)
            except Exception:
                pose = WSPose(pose=[0, 0, 0, 0, 0, 0])
            tcp_name = self._resolve_ghost_tcp(prim) if is_ghost else None
            item = self._pose_model.add_pose(
                prim_path=prim_path,
                name=prim.GetName(),
                pose=pose,
                is_ghost_object=is_ghost,
                tcp_name=tcp_name,
            )
            added_items.append(item)
        if not added_items and selected:
            nm.post_notification(
                "No valid pose prims in selection. Select prims with type:POSE or GhostObjectAPI.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
        for item in added_items:
            self._events.pose_added.emit(item)

    @staticmethod
    def is_pose_prim(prim) -> bool:
        try:
            if prim.HasAPI(wb_schema.GhostObjectAPI):
                return True
        except Exception as e:
            carb.log_warn(f"Failed to check GhostObjectAPI: {e}")
        try:
            custom_data = prim.GetCustomDataByKey("wandelbots")
            if custom_data and custom_data.get("type") == "POSE":
                return True
        except Exception as e:
            carb.log_warn(f"Failed to check custom data for pose: {e}")
        return False

    def pick_poses(self) -> None:
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        self._pose_dialog = PrimSelectDialog(
            stage=stage,
            window_title="Select Trajectory Poses",
            modal_window=True,
        )

        def _on_picked(future):
            try:
                prims = future.result()
                if prims:
                    self._on_poses_picked(prims)
            except Exception as exc:
                carb.log_warn(f"Pose picking failed: {exc}")

        run_coroutine(
            self._pose_dialog.show(sys.maxsize, self.is_pose_prim)
        ).add_done_callback(_on_picked)

    def _on_poses_picked(self, prims) -> None:
        if not prims:
            return
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        for prim in prims:
            prim_path = prim.GetPath().pathString
            is_ghost = self._is_ghost_object_prim(prim)
            try:
                pose = self._get_pose_relative_to_mg(prim_path)
            except Exception:
                pose = WSPose(pose=[0, 0, 0, 0, 0, 0])
            tcp_name = self._resolve_ghost_tcp(prim) if is_ghost else None
            item = self._pose_model.add_pose(
                prim_path=prim_path,
                name=prim.GetName(),
                pose=pose,
                is_ghost_object=is_ghost,
                tcp_name=tcp_name,
            )
            self._events.pose_added.emit(item)

    def _resolve_ghost_tcp(self, prim) -> str | None:
        nova_tcps = self._get_nova_tcps() if self._get_nova_tcps else {}
        matched = make_ghost_tcp_matcher(prim)(nova_tcps) if nova_tcps else None
        carb.log_info(
            f"_resolve_ghost_tcp: matched tcp={matched!r} "
            f"for {prim.GetPath()} (nova_tcps available={bool(nova_tcps)})"
        )
        return matched

    @staticmethod
    def _is_ghost_object_prim(prim) -> bool:
        try:
            return prim.HasAPI(wb_schema.GhostObjectAPI)
        except Exception:
            return False

    def remove_pose(self, item: PoseItem) -> None:
        self._pose_model.remove_pose(item.prim_path)
        self._events.pose_removed.emit(item)

    def copy_pose(self, item: PoseItem) -> None:
        omni.kit.clipboard.copy(str(item.pose))

    def move_up(self, item: PoseItem) -> None:
        self._pose_model.move_up(item)
        self._events.poses_reordered.emit(item)

    def move_down(self, item: PoseItem) -> None:
        self._pose_model.move_down(item)
        self._events.poses_reordered.emit(item)

    def toggle_visibility(self, item: PoseItem) -> None:
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        prim = stage.GetPrimAtPath(item.prim_path)
        if not prim or not prim.IsValid():
            return
        imageable = UsdGeom.Imageable(prim)
        if item.is_visible:
            imageable.MakeInvisible()
            item.is_visible = False
        else:
            imageable.MakeVisible()
            item.is_visible = True
        self._pose_model.notify_item_changed(item)

    def on_motion_type_changed(self, item: PoseItem, motion_type: str) -> None:
        item.motion_type = motion_type
        carb.log_info(
            f"Motion type stored: '{item.name_model.get_value_as_string()}' = {item.motion_type}"
        )
        self._events.motion_type_changed.emit(item, motion_type)
