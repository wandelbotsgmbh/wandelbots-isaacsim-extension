"""Collider Preset: applies Nova-compatible CollisionAPI to selected prims.

Strategy:
- Native shape prims (Sphere, Cube, Cylinder, Capsule) get CollisionAPI directly (no approximation).
- Mesh prims get CollisionAPI + MeshCollisionAPI with convexHull approximation.
- Existing prims with triangleMesh approximation are converted to convexHull.
- Skips invisible/inactive prims and prims that already have valid colliders.

The operation is performed asynchronously in batches to avoid freezing Isaac Sim,
with a modal progress dialog showing current status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import carb
import omni.kit.app
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine
from pxr import Usd, UsdGeom, UsdPhysics

from wandelbots.omni.ui.colors import NOVAColor

try:
    import omni.kit.notification_manager as nm

    _HAS_NOTIFICATION_MANAGER = True
except ImportError:
    _HAS_NOTIFICATION_MANAGER = False


_NATIVE_SHAPE_TYPES = {"Sphere", "Cube", "Cylinder", "Capsule"}
_SUPPORTED_APPROXIMATIONS = {"convexHull", "convexDecomposition", "none", ""}

# Number of prims to process per frame to keep the UI responsive
_BATCH_SIZE = 10


class _CandidateAction(Enum):
    APPLY_NATIVE = "apply_native"
    APPLY_CONVEX_HULL = "apply_convex_hull"
    CONVERT_TO_CONVEX_HULL = "convert_to_convex_hull"


@dataclass
class _Candidate:
    prim_path: str
    action: _CandidateAction


@dataclass
class _PresetResult:
    applied: list[str] = field(default_factory=list)
    converted: list[str] = field(default_factory=list)
    skipped: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_prim_active_and_visible(prim: Usd.Prim) -> bool:
    if not prim.IsActive():
        return False
    imageable = UsdGeom.Imageable(prim)
    if imageable:
        vis = imageable.ComputeVisibility(Usd.TimeCode.Default())
        if vis == UsdGeom.Tokens.invisible:
            return False
    return True


def _apply_collision_api(prim: Usd.Prim) -> None:
    """Apply UsdPhysics.CollisionAPI if not already present."""
    if not prim.HasAPI(UsdPhysics.CollisionAPI):
        UsdPhysics.CollisionAPI.Apply(prim)
    collision_api = UsdPhysics.CollisionAPI.Get(prim.GetStage(), prim.GetPath())
    collision_api.GetCollisionEnabledAttr().Set(True)


def _set_mesh_approximation(prim: Usd.Prim, approximation: str) -> None:
    """Set the physics:approximation attribute on a mesh prim."""
    if not prim.HasAPI(UsdPhysics.MeshCollisionAPI):
        UsdPhysics.MeshCollisionAPI.Apply(prim)
    mesh_collision_api = UsdPhysics.MeshCollisionAPI.Get(
        prim.GetStage(), prim.GetPath()
    )
    mesh_collision_api.GetApproximationAttr().Set(approximation)


def _get_current_approximation(prim: Usd.Prim) -> str | None:
    """Get the current physics:approximation value, or None if not set."""
    attr = prim.GetAttribute("physics:approximation")
    if attr and attr.HasValue():
        return attr.Get(Usd.TimeCode.Default())
    return None


# ---------------------------------------------------------------------------
# Phase 1: Collect candidates (read-only traversal)
# ---------------------------------------------------------------------------


def _collect_candidates(
    stage: Usd.Stage, prim_paths: list[str]
) -> tuple[list[_Candidate], int]:
    """Traverse the prim tree and collect candidates without modifying the stage.

    Returns (candidates, skipped_count).
    """
    candidates: list[_Candidate] = []
    skipped = 0

    def _visit(prim: Usd.Prim):
        nonlocal skipped

        if not _is_prim_active_and_visible(prim):
            skipped += 1
            return

        prim_type = prim.GetTypeName()
        prim_path = prim.GetPath().pathString

        if prim_type in _NATIVE_SHAPE_TYPES:
            if not prim.HasAPI(UsdPhysics.CollisionAPI):
                candidates.append(_Candidate(prim_path, _CandidateAction.APPLY_NATIVE))
            return

        if prim_type == "Mesh" or prim.IsA(UsdGeom.Mesh):
            current_approx = _get_current_approximation(prim)

            if prim.HasAPI(UsdPhysics.CollisionAPI):
                if current_approx == "triangleMesh":
                    candidates.append(
                        _Candidate(prim_path, _CandidateAction.CONVERT_TO_CONVEX_HULL)
                    )
                elif (
                    current_approx not in _SUPPORTED_APPROXIMATIONS
                    and current_approx is not None
                ):
                    candidates.append(
                        _Candidate(prim_path, _CandidateAction.CONVERT_TO_CONVEX_HULL)
                    )
            else:
                candidates.append(
                    _Candidate(prim_path, _CandidateAction.APPLY_CONVEX_HULL)
                )
            return

        # Grouping prims — recurse
        for child in prim.GetAllChildren():
            _visit(child)

    for path in prim_paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            carb.log_warn(f"Collider preset: invalid prim path '{path}'")
            continue
        _visit(prim)

    return candidates, skipped


# ---------------------------------------------------------------------------
# Phase 2: Apply in batches (async)
# ---------------------------------------------------------------------------


def _apply_single(stage: Usd.Stage, candidate: _Candidate) -> tuple[str, str]:
    """Apply a single candidate. Returns (prim_path, category) where category is 'applied' or 'converted'."""
    prim = stage.GetPrimAtPath(candidate.prim_path)
    if not prim or not prim.IsValid():
        return (candidate.prim_path, "skipped")

    if candidate.action == _CandidateAction.APPLY_NATIVE:
        _apply_collision_api(prim)
        return (candidate.prim_path, "applied")
    elif candidate.action == _CandidateAction.APPLY_CONVEX_HULL:
        _apply_collision_api(prim)
        _set_mesh_approximation(prim, "convexHull")
        return (candidate.prim_path, "applied")
    elif candidate.action == _CandidateAction.CONVERT_TO_CONVEX_HULL:
        _set_mesh_approximation(prim, "convexHull")
        return (candidate.prim_path, "converted")

    return (candidate.prim_path, "skipped")


# ---------------------------------------------------------------------------
# Progress Modal
# ---------------------------------------------------------------------------


class _ProgressModal:
    """A simple modal window showing collider preset progress."""

    def __init__(self, total: int):
        self._total = total
        self._current = 0
        self._cancelled = False

        self._window = ui.Window(
            "Applying Collider Preset",
            width=420,
            height=140,
            flags=(
                ui.WINDOW_FLAGS_NO_RESIZE
                | ui.WINDOW_FLAGS_NO_COLLAPSE
                | ui.WINDOW_FLAGS_MODAL
                | ui.WINDOW_FLAGS_NO_CLOSE
            ),
        )
        self._build_ui()

    def _build_ui(self):
        with self._window.frame:
            with ui.VStack(spacing=12):
                ui.Spacer(height=12)
                self._status_label = ui.Label(
                    f"Scanning... 0 / {self._total} prims",
                    alignment=ui.Alignment.CENTER,
                    height=24,
                    style={
                        "font_size": 15,
                        "color": NOVAColor.TEXT_PRIMARY.color,
                    },
                )
                with ui.HStack(height=22):
                    ui.Spacer(width=20)
                    self._progress_bar = ui.ProgressBar(
                        style={
                            "color": NOVAColor.PRIMARY_MAIN.color,
                            "background_color": NOVAColor.PROGRESS_BAR_BACKGROUND.color,
                            "border_radius": 4,
                        }
                    )
                    self._progress_bar.model.set_value(0.0)
                    ui.Spacer(width=20)
                ui.Spacer(height=4)
                with ui.HStack(height=30):
                    ui.Spacer()
                    ui.Button(
                        "Cancel",
                        width=90,
                        height=28,
                        clicked_fn=self._on_cancel,
                        style={
                            "font_size": 14,
                            "color": NOVAColor.TEXT_PRIMARY.color,
                            "background_color": NOVAColor.SECONDARY_TONAL.color,
                            "border_radius": 4,
                        },
                    )
                    ui.Spacer()
                ui.Spacer(height=8)

    def update(self, current: int, label: str | None = None):
        self._current = current
        progress = current / self._total if self._total > 0 else 1.0
        self._progress_bar.model.set_value(progress)
        if label:
            self._status_label.text = label
        else:
            self._status_label.text = f"Applying colliders... {current} / {self._total}"

    def close(self):
        if self._window:
            self._window.visible = False
            self._window = None

    def _on_cancel(self):
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


# ---------------------------------------------------------------------------
# Async entry point
# ---------------------------------------------------------------------------


async def _apply_collider_preset_async(prim_paths: list[str]) -> _PresetResult:
    """Async implementation: collect candidates, then apply in batches with progress."""
    stage = omni.usd.get_context().get_stage()
    if not stage:
        carb.log_warn("No active stage for collider preset.")
        return _PresetResult()

    # Phase 1: Collect candidates (fast, synchronous)
    candidates, skipped = _collect_candidates(stage, prim_paths)

    if not candidates:
        if _HAS_NOTIFICATION_MANAGER:
            nm.post_notification(
                "No changes needed — all prims already have valid colliders.",
                status=nm.NotificationStatus.INFO,
                duration=3,
            )
        return _PresetResult(skipped=skipped)

    # Phase 2: Apply in batches with progress modal
    total = len(candidates)
    modal = _ProgressModal(total)
    result = _PresetResult(skipped=skipped)

    try:
        for batch_start in range(0, total, _BATCH_SIZE):
            if modal.cancelled:
                carb.log_info("Collider preset cancelled by user.")
                break

            batch_end = min(batch_start + _BATCH_SIZE, total)
            for i in range(batch_start, batch_end):
                candidate = candidates[i]
                _, category = _apply_single(stage, candidate)
                if category == "applied":
                    result.applied.append(candidate.prim_path)
                elif category == "converted":
                    result.converted.append(candidate.prim_path)

            modal.update(
                batch_end,
                f"Applying colliders... {batch_end} / {total}",
            )

            # Yield to the event loop so the UI can update
            await omni.kit.app.get_app().next_update_async()

    finally:
        modal.close()

    # Show summary notification
    total_changes = len(result.applied) + len(result.converted)
    if total_changes > 0 and _HAS_NOTIFICATION_MANAGER:
        msg_parts = []
        if result.applied:
            msg_parts.append(f"Applied colliders to {len(result.applied)} prim(s)")
        if result.converted:
            msg_parts.append(
                f"Converted {len(result.converted)} triangleMesh collider(s) to convexHull"
            )
        msg = ". ".join(msg_parts) + "."
        nm.post_notification(msg, status=nm.NotificationStatus.INFO, duration=5)
        carb.log_info(f"Collider preset summary: {msg}")

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def can_apply_collider_preset(payload: dict) -> bool:
    """Check if the collider preset can be applied to the current selection."""
    stage = omni.usd.get_context().get_stage()
    if not stage:
        return False
    return True


def apply_collider_preset_from_payload(payload: dict) -> None:
    """Entry point for context menu — applies preset to selected prims or /World.

    Launches async task so Isaac Sim remains responsive.
    """
    prim_paths = omni.usd.get_context().get_selection().get_selected_prim_paths()
    if not prim_paths:
        prim_paths = ["/World"]
    run_coroutine(_apply_collider_preset_async(prim_paths))
