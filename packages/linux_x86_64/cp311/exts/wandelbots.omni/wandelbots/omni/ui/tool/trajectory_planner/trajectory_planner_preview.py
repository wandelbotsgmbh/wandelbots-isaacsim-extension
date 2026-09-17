"""Viewport preview controller for the trajectory planner.

Shows a robot ghost overlay at a given joint configuration using the registered RobotOverlay.
"""

from __future__ import annotations

import asyncio

import carb
from omni.kit.async_engine import run_coroutine

from wandelbots.omni.ui.overlay import ROBOT_OVERLAY_NAME
from wandelbots.omni.ui.overlay.overlay_registry import get_overlay_registry

#: What a ghost is on screen for. A caller that only wants to take its own ghost
#: down passes the same value back to `hide`.
REASON_PREVIEW = "preview"
REASON_FAILED_PLAN = "failed_plan"


class TrajectoryPlannerPreview:
    """Manages the robot ghost overlay for the trajectory planner widget."""

    def __init__(self) -> None:
        self._motion_group_prim_path: str | None = None
        self._reason: str = REASON_PREVIEW
        self._show_task: asyncio.Task | None = None

    @property
    def motion_group_prim_path(self) -> str | None:
        return self._motion_group_prim_path

    def show(
        self,
        motion_group_prim_path: str,
        joint_positions: list[float],
        color: list[float] | None = None,
        tool_colliders: dict | None = None,
        filled: bool = True,
        reason: str = REASON_PREVIEW,
    ) -> None:
        self._motion_group_prim_path = motion_group_prim_path
        self._reason = reason
        if self._show_task is not None:
            self._show_task.cancel()
        self._show_task = run_coroutine(
            self._show_async(
                motion_group_prim_path, joint_positions, color, tool_colliders, filled
            )
        )

    async def _show_async(
        self,
        motion_group_prim_path: str,
        joint_positions: list[float],
        color: list[float] | None = None,
        tool_colliders: dict | None = None,
        filled: bool = True,
    ) -> None:
        overlay = get_overlay_registry().get_overlay(ROBOT_OVERLAY_NAME)
        if overlay is None:
            carb.log_warn("RobotOverlay not registered; cannot show preview.")
            return
        try:
            await overlay.show(
                motion_group_prim_path,
                joint_positions,
                color=color,
                tool_colliders=tool_colliders,
                filled=filled,
            )
        except Exception as exc:
            carb.log_warn(f"Failed to show robot overlay preview: {exc}")
        finally:
            self._show_task = None

    def hide(self, only_if: str | None = None) -> None:
        """Take the ghost down. With *only_if*, only the one shown for that reason.

        A caller that put up its own ghost passes its reason back, so it cannot
        remove a ghost someone else has since shown in its place.
        """
        if not self._motion_group_prim_path:
            return
        if only_if is not None and self._reason != only_if:
            return
        overlay = get_overlay_registry().get_overlay(ROBOT_OVERLAY_NAME)
        if overlay is None:
            return
        overlay.hide(self._motion_group_prim_path)
        self._motion_group_prim_path = None
        self._reason = REASON_PREVIEW

    def destroy(self) -> None:
        self.hide()
        if self._show_task is not None:
            self._show_task.cancel()
            self._show_task = None
