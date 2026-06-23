"""Viewport overlay for previewing mounting candidate positions as colored spheres."""

from __future__ import annotations

import weakref
from typing import Callable, Optional

import carb
import omni.ui as ui
import omni.ui.scene as sc
import omni.ui_scene as ui_scene
from omni.kit.async_engine import run_coroutine
from omni.kit.viewport.utility import get_active_viewport_window
from omni.kit.viewport.window import ViewportWindow

from wandelbots.omni.utils.scene import SceneUtils

PENDING = 0
CALCULATING = 1
REACHABLE = 2
UNREACHABLE = 3
ERROR = 4
HIDDEN = 5

_STATUS_COLORS: dict[int, list[float]] = {
    PENDING: [0.5, 0.5, 0.5, 0.5],
    CALCULATING: [1.0, 0.75, 0.0, 0.5],
    REACHABLE: [0.15, 0.65, 0.60, 0.5],
    UNREACHABLE: [0.94, 0.33, 0.31, 0.5],
    ERROR: [0.9, 0.45, 0.1, 0.5],
}
_SELECTED_COLOR: list[float] = [1.0, 1.0, 1.0, 0.9]
_HOVER_ALPHA_BOOST = 0.35  # added to alpha on hover

_SPHERE_RADIUS = 0.025
_HIT_SIZE = _SPHERE_RADIUS * 5.0  # invisible hit rectangle half-size


class MountingPreview:
    """Renders colored spheres in the viewport for each mounting candidate."""

    def __init__(self) -> None:
        self._viewport: ViewportWindow | None = None
        self._scene_view: ui_scene.SceneView | None = None
        self._vstack: ui.VStack | None = None
        self._frame_name = "mounting_assistant_preview"
        self._candidates: list[tuple[list[float], int]] = []
        self._center_mm: list[float] | None = None
        self._selected_idx: int | None = None
        self._hover_idx: int | None = None
        self._on_select: Optional[Callable[[int | None], None]] = None
        self._redraw_pending: bool = False

    def set_on_select(self, callback: Callable[[int | None], None]) -> None:
        self._on_select = callback

    def set_candidates(
        self,
        positions_mm: list[list[float]],
        center_mm: list[float] | None = None,
    ) -> None:
        self._candidates = [(pos, PENDING) for pos in positions_mm]
        self._center_mm = center_mm
        self._selected_idx = None
        self._hover_idx = None
        self._redraw()

    def update_candidate(self, idx: int, status: int) -> None:
        if 0 <= idx < len(self._candidates):
            pos, _ = self._candidates[idx]
            self._candidates[idx] = (pos, status)
            self._redraw()

    def set_selected(self, idx: int | None) -> None:
        self._selected_idx = idx
        self._redraw()

    def _on_sphere_clicked(self, idx: int) -> None:
        new_sel = None if self._selected_idx == idx else idx
        self._selected_idx = new_sel
        self._schedule_redraw()
        if self._on_select:
            self._on_select(new_sel)

    def _on_sphere_hover_began(self, idx: int) -> None:
        if self._hover_idx != idx:
            self._hover_idx = idx
            self._schedule_redraw()

    def _on_sphere_hover_ended(self, idx: int) -> None:
        if self._hover_idx == idx:
            self._hover_idx = None
            self._schedule_redraw()

    def _schedule_redraw(self) -> None:
        """Defer _redraw to the next event-loop tick, outside the gesture callback."""
        if not self._redraw_pending:
            self._redraw_pending = True
            run_coroutine(self._deferred_redraw())

    async def _deferred_redraw(self) -> None:
        self._redraw_pending = False
        self._redraw()

    def _redraw(self) -> None:
        if not self._ensure_scene():
            return
        self._scene_view.scene.clear()
        if not self._candidates:
            return

        stage_units = SceneUtils.get_stage_units()
        unit_factor = stage_units / 1000.0

        with self._scene_view.scene:
            for idx, (pos_mm, status) in enumerate(self._candidates):
                if status == HIDDEN:
                    continue
                color = list(_STATUS_COLORS.get(status, _STATUS_COLORS[PENDING]))
                x = pos_mm[0] * unit_factor
                y = pos_mm[1] * unit_factor
                z = pos_mm[2] * unit_factor
                selected = idx == self._selected_idx
                hovered = idx == self._hover_idx
                with sc.Transform(
                    transform=sc.Matrix44.get_translation_matrix(x, y, z)
                ):
                    # Invisible hit rect with click + hover gestures
                    sc.Rectangle(
                        _HIT_SIZE * 2,
                        _HIT_SIZE * 2,
                        color=[0, 0, 0, 0],
                        gestures=[
                            sc.ClickGesture(
                                on_ended_fn=lambda _s, i=idx, ws=weakref.ref(self): (
                                    ws()._on_sphere_clicked(i) if ws() else None
                                )
                            ),
                            sc.HoverGesture(
                                on_began_fn=lambda _s, i=idx, ws=weakref.ref(self): (
                                    ws()._on_sphere_hover_began(i) if ws() else None
                                ),
                                on_ended_fn=lambda _s, i=idx, ws=weakref.ref(self): (
                                    ws()._on_sphere_hover_ended(i) if ws() else None
                                ),
                            ),
                        ],
                    )
                    # Selection highlight rings
                    if selected:
                        sc.Arc(_SPHERE_RADIUS * 1.6, axis=0, color=_SELECTED_COLOR)
                        sc.Arc(_SPHERE_RADIUS * 1.6, axis=1, color=_SELECTED_COLOR)
                        sc.Arc(_SPHERE_RADIUS * 1.6, axis=2, color=_SELECTED_COLOR)
                    # Hover: boost alpha and size slightly
                    draw_color = color
                    draw_radius = _SPHERE_RADIUS
                    if hovered and not selected:
                        draw_color = color[:3] + [
                            min(1.0, color[3] + _HOVER_ALPHA_BOOST)
                        ]
                        draw_radius = _SPHERE_RADIUS * 1.25
                    sc.Arc(draw_radius, axis=0, color=draw_color)
                    sc.Arc(draw_radius, axis=1, color=draw_color)
                    sc.Arc(draw_radius, axis=2, color=draw_color)

    def _ensure_scene(self) -> bool:
        if self._scene_view is not None:
            return True
        viewport = get_active_viewport_window()
        if viewport is None:
            carb.log_warn("No active viewport for mounting assistant preview")
            return False
        self._viewport = viewport
        with viewport.get_frame(self._frame_name):
            self._vstack = ui.VStack(content_clipping=False)
            with self._vstack:
                self._scene_view = ui_scene.SceneView()
                with self._scene_view.scene:
                    pass
        viewport.viewport_api.add_scene_view(self._scene_view)
        return True

    def clear(self) -> None:
        self._candidates = []
        self._center_mm = None
        self._selected_idx = None
        self._hover_idx = None
        if self._scene_view is not None:
            self._scene_view.scene.clear()

    def restore_candidates(
        self,
        positions_mm: list[list[float]],
        statuses: list[int],
        selected_idx: int | None,
        center_mm: list[float] | None = None,
    ) -> None:
        """Restore full candidate state in a single redraw."""
        self._candidates = list(zip(positions_mm, statuses))
        self._center_mm = center_mm
        self._selected_idx = selected_idx
        self._hover_idx = None
        self._redraw()

    def destroy(self) -> None:
        self.clear()
        if self._viewport is not None and self._scene_view is not None:
            try:
                self._viewport.viewport_api.remove_scene_view(self._scene_view)
            except Exception as exc:
                carb.log_warn(f"Error removing mounting preview scene view: {exc}")
        self._scene_view = None
        self._vstack = None
        self._viewport = None
