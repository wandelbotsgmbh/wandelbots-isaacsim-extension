"""Viewport overlay: reachability envelope for an explicitly chosen pose.

The overlay is entirely driven by the Reachability Envelope tool window - the
window tells it WHICH motion group, TCP and pose prim to use (no selection
listening, no auto-detection, no fallback guessing). It draws:

* the voxels the TCP can pass through in SOME joint configuration, from a local
  forward-kinematics sweep, shaded by how often the sweep landed there,
* a marker at the pose itself.

The cloud is a hint about where it is worth placing a pose at all, not a
verdict: it ignores orientation, so a point inside it can still be out of reach
at the orientation the pose actually has. The binding answer is the window's
live NOVA inverse-kinematics solve, pushed in through ``set_pose_state``, and
that is what colours the marker.

Moving or rotating the pose therefore only moves the marker. The cloud depends
on the motion group and the TCP, and is recomputed when either changes.
"""

from __future__ import annotations

import asyncio
import math

import carb
from dataclasses import dataclass

import numpy as np
import omni.ui as ui
import omni.ui.scene as sc
import omni.ui_scene as ui_scene
import omni.kit.app
import omni.usd
from omni.kit.async_engine import run_coroutine
from omni.kit.viewport.utility import get_active_viewport_window
from omni.usd import get_watcher
from pxr import Sdf, Usd, UsdGeom

from wandelbots.omni.manipulators.utils import get_link_0_from_motion_group_prim
from wandelbots.omni.reachability.envelope_service import get_envelope_service
from wandelbots.omni.ui.overlay.overlay import ViewportOverlay
from wandelbots.omni.utils.math import numpy_to_scene_matrix44
from wandelbots.omni.utils.prims import (
    PrimUtils,
    is_pose_xform_op,
    xform_op_carries_rotation,
)
from wandelbots.omni.utils.scene import SceneUtils

# A rotation smaller than this cannot visibly move the cloud, and every
# recompute is a round of IK batches - so a drag does not fire one per sample.
_ORIENTATION_EPSILON_RAD = 0.05

# The pose's own live IK solve must get through before a rotation floods the
# solver with the envelope's batches, otherwise the verdict queues behind them.
_DENSITY_DEBOUNCE = 0.35
_VOXEL_MM = 40.0
# Density picks how many joint-space samples the cloud is swept from, not how
# big its voxels are. On a long arm the grid has far more cells than any sample
# count reaches, so the voxel size hardly changes the work - the sample count
# decides both the IK load and how many points the viewport draws.
_MIN_SAMPLES = 5_000
_MAX_SAMPLES = 70_000
# About 12k points and two seconds of IK on a 3 m arm: the shape of the reach
# without taking the frame rate or the live verdict with it.
DEFAULT_DENSITY = 0.3


def samples_for_density(density: float) -> int:
    """Sweep sample count for a density slider position in [0, 1]."""
    density = max(0.0, min(1.0, float(density)))
    return int(round(_MIN_SAMPLES + density * (_MAX_SAMPLES - _MIN_SAMPLES)))


_MAX_FALLBACK_POINTS = 4000
# How many of the drawn voxels the far-field snap fallback re-checks with IK.
# One batch, ordered nearest first, so the check costs a single request.
_SNAP_VOXEL_CANDIDATES = 32

REACHABILITY_ENVELOPE_OVERLAY_NAME = "wandelbots.omni.ui.reachability_envelope_overlay"

# The cloud is a backdrop for the pose placed inside it, so it stays faint
# enough to see the marker and the gizmo through.
DEFAULT_OPACITY = 0.30

# Marker colours for the window's verdict about the pose in hand.
MARKER_UNKNOWN = (0.6, 0.6, 0.6)
MARKER_UNREACHABLE = (0.94, 0.33, 0.31)
MARKER_NOT_PLANNABLE = (1.0, 0.67, 0.25)
MARKER_READY = (0.15, 0.95, 0.25)


GRADIENT_HIGH = (0.15, 0.9, 0.25)  # plenty of joint freedom here (l=1)
GRADIENT_LOW = (1.0, 0.55, 0.05)  # close to a joint limit (m=0)


def rotvec_matches(a, b) -> bool:
    """Whether two rotation vectors are the same to within the redraw epsilon."""
    left = np.asarray(a, dtype=np.float64)
    right = np.asarray(b, dtype=np.float64)
    return bool(np.linalg.norm(left - right) <= _ORIENTATION_EPSILON_RAD)


def _margin_color(m: float, alpha: float) -> list[float]:
    """Linear green (l=1) -> orange (l=0) spectrum by likelihood."""
    m = max(0.0, min(1.0, m))
    return [
        GRADIENT_LOW[0] * (1 - m) + GRADIENT_HIGH[0] * m,
        GRADIENT_LOW[1] * (1 - m) + GRADIENT_HIGH[1] * m,
        GRADIENT_LOW[2] * (1 - m) + GRADIENT_HIGH[2] * m,
        alpha,
    ]


def margin_gradient_color(m: float) -> list[float]:
    """Public green->orange gradient sample [r, g, b] for legend widgets."""
    return _margin_color(m, 1.0)[:3]


# A snap under this distance is the search saying "already reachable" - the
# refinement stops at a quarter millimetre, so anything below that is noise.
_SNAP_NOOP_MM = 0.05


@dataclass
class SnapTarget:
    """Result of a snap search, before anything has been moved."""

    position_mm: np.ndarray  # target position in the base frame
    offset_mm: np.ndarray  # target minus the pose's current position
    distance_mm: float
    already_reachable: bool
    # The local search found nothing and the nearest precomputed envelope voxel
    # was used instead - that is a much coarser answer, worth saying out loud.
    from_envelope_voxel: bool = False

    def describe(self) -> str:
        dx, dy, dz = (float(v) for v in self.offset_mm)
        text = f"{self.distance_mm:.2f} mm (dx {dx:+.2f}, dy {dy:+.2f}, dz {dz:+.2f})"
        return text + " - from the envelope grid" if self.from_envelope_voxel else text


# sc.Points sizes are screen pixels, so one fixed size only ever looks right at
# one zoom level: move the camera closer and the voxels spread apart on screen
# while the dots stay put, until the cloud reads as a handful of specks.
MIN_POINT_PIXELS = 2.0
MAX_POINT_PIXELS = 48.0
# Redraw only past this ratio - rebuilding tens of thousands of points on every
# camera nudge costs far more than the slightly stale size.
POINT_SIZE_REDRAW_RATIO = 1.3
# Checking every frame is pointless; a few times a second tracks a zoom fine.
_CAMERA_WATCH_FRAMES = 10


def voxel_point_pixels(
    voxel_stage_units: float,
    camera_distance: float,
    projection_scale: float,
    viewport_height_pixels: float,
) -> float:
    """How many pixels one voxel covers, clamped to something drawable.

    ``projection_scale`` is the [1][1] entry of the perspective projection
    matrix, which is 1 / tan(fov_y / 2); the projected height of an object is
    that over its distance, times half the viewport height.
    """
    if camera_distance <= 0.0 or viewport_height_pixels <= 0.0:
        return MIN_POINT_PIXELS
    pixels = (voxel_stage_units * projection_scale * viewport_height_pixels) / (
        2.0 * camera_distance
    )
    return max(MIN_POINT_PIXELS, min(MAX_POINT_PIXELS, pixels))


class ReachabilityEnvelopeOverlay(ViewportOverlay):
    def __init__(self, name: str) -> None:
        self.name = name
        self._viewport = None
        self._scene_view: ui_scene.SceneView | None = None
        self._vstack: ui.VStack | None = None

        self._enabled = False
        # The cloud is the expensive half. The marker is not, and it is the
        # answer the user came for, so the two are switched apart: the overlay
        # is live while the tool window is open, the cloud only on request.
        self._cloud_visible = False
        self._point_size = 6.0
        # Size the cloud was last drawn at, so a camera move only forces a
        # rebuild once the voxels would look noticeably wrong.
        self._drawn_point_size: float | None = None
        self._base_world_position: tuple[float, float, float] | None = None
        self._camera_watch_sub = None
        self._camera_watch_frames = 0
        self._camera_probe_logged = False
        self._opacity = DEFAULT_OPACITY
        self._samples = samples_for_density(DEFAULT_DENSITY)
        self._graded = True
        self._reachable_color = [0.15, 0.9, 0.25]  # solid colour when not graded
        self._status_fn = None  # callback(dict) for the tool window
        self._busy = False  # a recompute is in flight
        self._recompute_task: asyncio.Task | None = None
        # Orientation the drawn envelope was computed at, and the pose's own -
        # compared to decide whether a rotation is worth another compute.
        self._envelope_orientation: list[float] | None = None

        self._pose_watch_sub = None
        self._watched_path: str | None = None
        self._density_token = 0

        # Targets - always set EXPLICITLY by the tool window; the overlay never
        # guesses a motion group, TCP or pose on its own.
        self._mg_path: str | None = None
        self._tcp: str | None = None
        self._pose_path: str | None = None
        # Mounting used for the envelope's IK batches. The tool window pushes
        # the (user-editable, API-prefilled) value; None = API description's.
        self._mounting = None

        self._mg_prim: Usd.Prim | None = None
        self._base_path: str | None = None
        self._ctx = None
        # The context (description + API client) only depends on motion group /
        # TCP / mounting - reuse it until one of those changes instead of
        # re-fetching the description per recompute.
        self._ctx_dirty = True
        self._envelope = None  # Envelope
        # Cached (positions, colors) of the drawn voxels; see _points().
        self._point_cache: tuple[list, list] | None = None
        # Last recompute failure, surfaced through get_status().
        self._error: str | None = None
        self._pose_pos_mm: np.ndarray | None = None
        # The window's verdict about the pose in hand; the overlay never
        # decides this from the grid.
        self._pose_reachable: bool | None = None
        self._pose_plannable: bool | None = None

    # -- ViewportOverlay ---------------------------------------------------
    def attach_to_viewport(self, viewport_window) -> None:
        self._viewport = viewport_window or get_active_viewport_window()
        carb.log_info(
            f"[Envelope] attach_to_viewport (viewport={'yes' if self._viewport else 'NONE'})"
        )

    def __del__(self):
        self._teardown()

    def _teardown(self):
        self._unsubscribe_pose_watch()
        get_envelope_service().close(self._ctx)
        self._ctx = None
        if self._scene_view is not None and self._viewport is not None:
            try:
                self._viewport.viewport_api.remove_scene_view(self._scene_view)
            except Exception:
                pass
        self._scene_view = None

    # -- public API (tool window) -------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        """Switch the overlay itself on or off - the tool window's lifetime.

        On, the reachability marker follows the pose whether or not the cloud
        is being drawn. Off, nothing is left in the viewport.
        """
        carb.log_info(f"[Envelope] set_enabled({enabled})")
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._refresh_targets()
            return
        self._cancel_recompute()
        self._unsubscribe_camera_watch()
        self._unsubscribe_pose_watch()
        self._clear_scene()

    def set_cloud_visible(self, visible: bool) -> None:
        """Show or hide the voxel cloud, leaving the marker alone.

        Hiding drops the computed envelope: keeping it would go stale against
        the pose and the joint limits while nothing redraws it, and computing
        it again on demand is what the button is for.
        """
        carb.log_info(f"[Envelope] set_cloud_visible({visible})")
        if visible == self._cloud_visible:
            return
        self._cloud_visible = visible
        if visible:
            self._subscribe_camera_watch()
            self._refresh_targets()
            return
        self._unsubscribe_camera_watch()
        # A sweep still running would keep firing IK batches for a cloud
        # nobody sees; the marker needs none of it.
        self._cancel_recompute()
        self._envelope = None
        self._invalidate_points()
        self._redraw()
        self._emit_status()

    def reset(self) -> None:
        """Forget everything tied to the stage: targets, cloud and marker.

        A replaced stage leaves every prim path the overlay holds pointing at
        nothing, and a cloud computed on the old one would stay drawn over the
        new scene. The switches stay as they are, so the tool picks up the new
        stage's targets without being reopened.
        """
        self._cancel_recompute()
        self._unsubscribe_pose_watch()
        get_envelope_service().close(self._ctx)
        self._ctx = None
        self._ctx_dirty = True
        self._mg_path = None
        self._mg_prim = None
        self._base_path = None
        self._pose_path = None
        self._mounting = None
        self._envelope = None
        self._envelope_orientation = None
        self._pose_pos_mm = None
        self._pose_reachable = None
        self._pose_plannable = None
        self._invalidate_points()
        self._clear_scene()
        self._emit_status()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def cloud_visible(self) -> bool:
        return self._cloud_visible

    @property
    def mounting(self):
        return self._mounting

    def set_motion_group(self, prim_path: str | None) -> None:
        """Explicitly select the motion group (prim path). None clears it."""
        if (prim_path or None) != self._mg_path:
            self._ctx_dirty = True
        self._mg_path = prim_path or None
        carb.log_info(f"[Envelope] motion group = {self._mg_path}")
        self._refresh_targets()

    def set_tcp(self, tcp_name: str | None) -> None:
        """Explicitly select the TCP by name. None = flange."""
        if (tcp_name or None) != self._tcp:
            self._ctx_dirty = True
        self._tcp = tcp_name or None
        carb.log_info(f"[Envelope] TCP = {self._tcp}")
        if self._cloud_visible and self._targets_ready():
            self._start_recompute()

    def set_pose_path(self, path: str | None) -> None:
        """Explicitly select the target pose prim. None clears it."""
        self._pose_path = path or None
        carb.log_info(f"[Envelope] pose = {self._pose_path}")
        self._refresh_targets()

    def set_mounting(self, mounting) -> None:
        """Mounting (NOVA Pose) to use in the envelope's IK requests.
        None = use the API description's own mounting."""
        if mounting == self._mounting:
            return
        self._ctx_dirty = True
        self._mounting = mounting
        if self._cloud_visible and self._targets_ready():
            self._start_recompute()

    def set_pose_state(self, reachable: bool | None, plannable: bool | None) -> None:
        """Verdict for the pose in hand, from the tool window's live IK solve.

        None means "not answered yet"; the marker greys out rather than claiming
        the pose is out of reach.
        """
        if (reachable, plannable) == (self._pose_reachable, self._pose_plannable):
            return
        self._pose_reachable = reachable
        self._pose_plannable = plannable
        self._redraw()

    def set_point_size(self, size: float) -> None:
        self._point_size = max(1.0, size)
        self._redraw()

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.05, min(1.0, opacity))
        self._invalidate_points()
        self._redraw()

    def set_graded(self, graded: bool) -> None:
        self._graded = graded
        self._invalidate_points()
        self._redraw()

    @property
    def sample_count(self) -> int:
        return self._samples

    def set_density(self, density: float) -> None:
        """Set how many samples the cloud is swept from; see samples_for_density.

        Recompute is debounced so dragging the density slider doesn't fire a sweep
        on every intermediate value.
        """
        samples = samples_for_density(density)
        if samples == self._samples:
            return
        self._samples = samples
        carb.log_info(f"[Envelope] samples = {self._samples}")
        if self._cloud_visible and self._ctx is not None:
            self._density_token += 1
            run_coroutine(self._debounced_density(self._density_token))

    async def _debounced_density(self, token: int) -> None:
        await asyncio.sleep(_DENSITY_DEBOUNCE)
        if token != self._density_token:
            return
        self._start_recompute()

    def set_reachable_color(self, rgb: list[float]) -> None:
        self._reachable_color = [float(rgb[0]), float(rgb[1]), float(rgb[2])]
        self._invalidate_points()
        self._redraw()

    def set_status_callback(self, fn) -> None:
        self._status_fn = fn

    def recompute(self) -> bool:
        """Force a full recompute of the current pose/motion-group (manual
        refresh). Returns False if there's nothing to compute yet (overlay
        disabled, or motion group / pose not selected)."""
        if not self._cloud_visible or not self._targets_ready():
            return False
        self._start_recompute()
        return True

    def get_status(self) -> dict:
        return {
            "enabled": self._enabled,
            "cloud_visible": self._cloud_visible,
            "busy": self._busy,
            "pose": self._pose_path,
            "motion_group": self._mg_path,
            "model": getattr(self._ctx, "model_name", None) if self._ctx else None,
            "active_tcp": getattr(self._ctx, "active_tcp", None) if self._ctx else None,
            "voxels": (
                0 if self._envelope is None else int(self._envelope.centres_mm.shape[0])
            ),
            "voxel_mm": (
                _VOXEL_MM if self._envelope is None else self._envelope.voxel_mm
            ),
            "error": self._error,
        }

    def _emit_status(self) -> None:
        if self._status_fn is not None:
            try:
                self._status_fn(self.get_status())
            except Exception:
                pass

    async def find_snap_target(self) -> SnapTarget | None:
        """Nearest reachable point for the selected pose - WITHOUT moving it.

        Split from applying the snap so a caller can show the offset first: the
        search used to move the pose the moment it landed, which silently
        dragged a pose that was only a hair out of reach (and, before the
        millimetre shells, not by a hair at all).

        Continuous local IK search around the pose (spherical shells, nearest
        first) - NOT limited to the precomputed envelope voxels, and it works
        without the envelope being enabled. The precomputed oriented voxels are
        only a far-field fallback when nothing solves within the local search
        radius.

        Returns None when the search could not run or nothing is reachable;
        ``SnapTarget.already_reachable`` marks a pose that needs no snap.
        """
        if self._pose_path is None or self._mg_path is None:
            carb.log_warn("Envelope: snap needs a motion group and a pose selected")
            return None
        if not self._resolve_targets():
            return None
        self._busy = True
        self._emit_status()
        try:
            ctx = await self._ensure_ctx()
            if ctx is None:
                carb.log_warn("Envelope: snap failed (no motion group description)")
                return None
            base = self._read_pose_base_frame()
            if base is None:
                carb.log_warn("Envelope: snap failed (could not read the pose)")
                return None
            service = get_envelope_service()
            target = await service.find_nearest_reachable(ctx, base[:3], list(base[3:]))
            from_voxel = False
            if target is None:
                target = await self._nearest_reachable_voxel(ctx, service, base)
                from_voxel = target is not None
            if target is None:
                carb.log_warn(
                    "Envelope: no reachable point found around the pose at this "
                    "orientation"
                )
                return None
            origin = np.asarray(base[:3], dtype=np.float64)
            point = np.asarray(target, dtype=np.float64)
            offset = point - origin
            distance = float(np.linalg.norm(offset))
            return SnapTarget(
                position_mm=point,
                offset_mm=offset,
                distance_mm=distance,
                already_reachable=distance < _SNAP_NOOP_MM,
                from_envelope_voxel=from_voxel,
            )
        except Exception as exc:
            carb.log_warn(f"Envelope: snap search failed: {exc}")
            return None
        finally:
            self._busy = False
            self._emit_status()

    async def _nearest_reachable_voxel(self, ctx, service, base) -> np.ndarray | None:
        """Far-field fallback: the nearest drawn voxel that IK confirms.

        The drawn cloud is only a starting list here, never the answer: it was
        computed for whatever orientation the pose had when the sweep ran, and
        a rotation below the recompute threshold is enough to make it a
        different question. Every candidate therefore goes back through IK at
        the pose's orientation before it is offered, so a snap can only land
        on a point the verdict also accepts.
        """
        if self._envelope is None:
            return None
        centres = self._envelope.centres_mm
        if not centres.size:
            return None
        distances = np.linalg.norm(
            centres - np.asarray(base[:3], np.float32)[None, :], axis=1
        )
        nearest = np.argsort(distances)[:_SNAP_VOXEL_CANDIDATES]
        target = await service.first_reachable_point(
            ctx, centres[nearest], list(base[3:])
        )
        if target is None:
            carb.log_info(
                "Envelope: no drawn voxel solves at the pose's orientation either"
            )
            return None
        carb.log_info(
            "Envelope: local snap search found nothing; falling back to the "
            "nearest drawn voxel that solves at the pose's orientation"
        )
        return target

    def apply_snap_target(self, target: SnapTarget) -> bool:
        """Move the pose prim onto a previously found snap target."""
        if target.already_reachable:
            carb.log_info("Envelope: pose is already reachable - not moved")
            return False
        try:
            self._move_pose_to_base_position(
                np.asarray(target.position_mm, dtype=np.float32)
            )
            return True
        except Exception as exc:
            carb.log_warn(f"Envelope: applying the snap failed: {exc}")
            return False

    async def _snap_async(self) -> None:
        """Search and apply in one go - kept for callers that want no prompt."""
        target = await self.find_snap_target()
        if target is not None:
            self.apply_snap_target(target)

    def _move_pose_to_base_position(self, target_base_mm: np.ndarray) -> None:
        """Move the pose prim so its base-frame position is ``target_base_mm``,
        keeping its current world orientation."""
        from wandelbots.omni.utils.math import pose_to_matrix

        # target world position = base_world * target (base-frame mm)
        base = PrimUtils.get_prim_pose(
            self._base_path, coordinate_system="world", rotation_type="cartesian"
        )
        base_matrix = pose_to_matrix(list(base.pose))
        world_pos = (base_matrix @ np.array([*target_base_mm, 1.0]))[:3]

        # keep the pose's current world orientation
        cur_world = PrimUtils.get_prim_pose(
            self._pose_path, coordinate_system="world", rotation_type="cartesian"
        )
        target_world = [*world_pos.tolist(), *list(cur_world.pose[3:])]

        self._set_pose_world(target_world)
        self._pose_pos_mm = np.asarray(target_base_mm, dtype=np.float32)
        self._redraw()
        carb.log_info(f"Envelope: snapped {self._pose_path} to nearest reachable point")

    def _set_pose_world(self, world_pose_ws: list[float]) -> None:
        """Set the pose prim so its WORLD pose equals ``world_pose_ws`` (mm, rotvec),
        accounting for any parent transform."""
        from wandelbots.omni.datatypes import WSPose
        from wandelbots.omni.utils.math import matrix_to_pose, pose_to_matrix

        parent_path = self._pose_path.rsplit("/", 1)[0] or "/World"
        world_mat = pose_to_matrix(world_pose_ws)
        try:
            parent = PrimUtils.get_prim_pose(
                parent_path, coordinate_system="world", rotation_type="cartesian"
            )
            parent_mat = pose_to_matrix(list(parent.pose))
            local_mat = np.linalg.inv(parent_mat) @ world_mat
            local_ws = matrix_to_pose(local_mat)
        except Exception:
            local_ws = world_pose_ws
        PrimUtils.set_prim_pose(
            prim_path=self._pose_path, input_pose=WSPose(pose=local_ws)
        )

    # -- scene setup -------------------------------------------------------
    def _ensure_scene(self) -> bool:
        if self._scene_view is not None:
            return True
        viewport = self._viewport or get_active_viewport_window()
        if viewport is None:
            carb.log_warn("Envelope overlay: no active viewport")
            return False
        self._viewport = viewport
        with viewport.get_frame(self.name):
            self._vstack = ui.VStack(content_clipping=False)
            with self._vstack:
                self._scene_view = ui_scene.SceneView()
                with self._scene_view.scene:
                    pass
        viewport.viewport_api.add_scene_view(self._scene_view)
        carb.log_info(
            f"[Envelope] scene view created (sc.Points available={hasattr(sc, 'Points')})"
        )
        return True

    def _clear_scene(self):
        if self._scene_view is not None:
            self._scene_view.scene.clear()

    # -- target resolution ---------------------------------------------------
    def _targets_ready(self) -> bool:
        return self._mg_path is not None and self._pose_path is not None

    def _resolve_targets(self) -> bool:
        """Resolve the selected motion group + pose prims (no recompute)."""
        stage = omni.usd.get_context().get_stage()
        if stage is None or not self._targets_ready():
            return False
        mg = stage.GetPrimAtPath(self._mg_path)
        pose = stage.GetPrimAtPath(self._pose_path)
        if not mg or not mg.IsValid() or not pose or not pose.IsValid():
            return False
        self._mg_prim = mg
        # NOVA states its IK/FK poses in the frame the DH chain starts from,
        # which is link_0 where the asset has one. Measured on the MANUS arms:
        # every DH frame of the chain lands exactly on its USD link through
        # link_0, and ~416 mm beside it through the motion group prim.
        self._base_path = get_link_0_from_motion_group_prim(mg).GetPath().pathString
        return True

    async def _ensure_ctx(self):
        """Reuse the prepared context while motion group / TCP / mounting are
        unchanged; (re)prepare - closing the previous context's API client -
        only when one of them changed."""
        service = get_envelope_service()
        if self._ctx is not None and not self._ctx_dirty:
            return self._ctx
        service.close(self._ctx)
        self._ctx = None
        if self._mg_prim is None and not self._resolve_targets():
            return None
        carb.log_info("[Envelope] preparing context (fetching description)...")
        ctx = await service.prepare(
            self._mg_prim,
            tcp_name=self._tcp,
            mounting_override=self._mounting,
        )
        if ctx is not None:
            self._ctx = ctx
            self._ctx_dirty = False
        return ctx

    def _refresh_targets(self):
        """Re-resolve the explicitly selected motion group + pose and recompute.

        No guessing: if either selection is missing/invalid the scene is simply
        cleared until the tool window provides a complete selection.
        """
        if not self._enabled:
            return
        if not self._resolve_targets():
            if self._targets_ready():
                carb.log_warn(
                    f"[Envelope] invalid selection (motion group={self._mg_path}, "
                    f"pose={self._pose_path}) - clearing"
                )
            self._unsubscribe_pose_watch()
            self._clear_scene()
            return
        carb.log_info(
            f"[Envelope] pose={self._pose_path} motion group={self._mg_path} "
            f"base={self._base_path}; computing envelope..."
        )
        self._subscribe_pose_watch(self._pose_path)
        self._refresh_marker_position()
        if not self._cloud_visible:
            # The marker still has to find its way onto the screen.
            self._redraw()
            return
        self._start_recompute()

    # -- pose watching -----------------------------------------------------
    def _subscribe_pose_watch(self, path: str):
        if path == self._watched_path and self._pose_watch_sub is not None:
            return
        self._unsubscribe_pose_watch()
        try:
            self._pose_watch_sub = get_watcher().subscribe_to_change_info_path(
                Sdf.Path(path), self._on_pose_changed
            )
            self._watched_path = path
        except Exception as exc:
            carb.log_warn(f"Envelope: could not watch {path}: {exc}")

    def _subscribe_camera_watch(self) -> None:
        """Redraw when the camera moved far enough to change the voxel size.

        The size lives in screen pixels, so it has to be recomputed as the
        camera moves; checking a few times a second costs nothing, and the
        redraw only happens once the size is actually off.
        """
        if self._camera_watch_sub is not None:
            return
        self._camera_watch_sub = (
            omni.kit.app.get_app()
            .get_update_event_stream()
            .create_subscription_to_pop(
                self._on_camera_watch, name="wandelbots.omni.envelope_camera_watch"
            )
        )

    def _unsubscribe_camera_watch(self) -> None:
        self._camera_watch_sub = None
        self._camera_watch_frames = 0

    def _on_camera_watch(self, _event) -> None:
        self._camera_watch_frames += 1
        if self._camera_watch_frames % _CAMERA_WATCH_FRAMES:
            return
        if not self._cloud_visible or self._drawn_point_size is None:
            return
        wanted = self._camera_point_size()
        drawn = self._drawn_point_size
        ratio = wanted / drawn if drawn else 0.0
        if ratio > POINT_SIZE_REDRAW_RATIO or ratio < 1.0 / POINT_SIZE_REDRAW_RATIO:
            self._redraw()

    def _camera_point_size(self) -> float:
        """Pixel size for one voxel at the current camera, or the fixed size
        when the viewport does not hand out its matrices."""
        if self._scene_view is None or self._viewport is None:
            return self._point_size
        if self._base_world_position is None:
            return self._point_size
        try:
            model = self._scene_view.model
            view = list(model.get_as_floats("view"))
            projection = list(model.get_as_floats("projection"))
            height_pixels = float(self._viewport.viewport_api.resolution[1])
        except (AttributeError, TypeError, ValueError, IndexError) as error:
            if not self._camera_probe_logged:
                self._camera_probe_logged = True
                carb.log_info(
                    f"[Envelope] no camera matrices ({error}); "
                    "keeping a fixed point size"
                )
            return self._point_size
        if len(view) < 16 or len(projection) < 16:
            return self._point_size

        # The view matrix maps world to camera as a row vector, so the camera
        # sits at -translation rotated back by the (orthonormal) basis.
        translation = view[12:15]
        camera = [
            -sum(translation[column] * view[row * 4 + column] for column in range(3))
            for row in range(3)
        ]
        distance = math.dist(camera, self._base_world_position)
        return voxel_point_pixels(
            _VOXEL_MM * self._unit_factor(),
            distance,
            projection[5],
            height_pixels,
        )

    def _unsubscribe_pose_watch(self):
        if self._pose_watch_sub is not None:
            try:
                self._pose_watch_sub.unsubscribe()
            except Exception:
                pass
            self._pose_watch_sub = None
        self._watched_path = None

    def _on_pose_changed(self, path: Sdf.Path = None):
        if not is_pose_xform_op(path):
            return
        if not self._refresh_marker_position():
            return
        if (
            self._cloud_visible
            and xform_op_carries_rotation(path)
            and self._orientation_moved()
        ):
            # Which positions are reachable depends on the orientation asked
            # for, so a rotation changes the cloud as much as a move does.
            self._start_recompute()
            return
        # Hidden cloud: the marker is all there is to update.
        self._redraw()

    def _orientation_moved(self) -> bool:
        """Whether the pose turned far enough to be worth another compute.

        A drag emits a notice per mouse sample, and each recompute is a round
        of IK batches; below the threshold the cloud would not visibly change
        anyway.
        """
        base = self._read_pose_base_frame()
        if base is None:
            return False
        previous = self._envelope_orientation
        if previous is None:
            return True
        return not rotvec_matches(base[3:], previous)

    # -- pose geometry -----------------------------------------------------
    def _refresh_marker_position(self) -> bool:
        """Put the marker where the pose is. False when the frame is unreadable.

        Called before every draw that shows the marker, not only after a
        recompute: with the cloud hidden there is no recompute to ride along
        with, and the marker is then the only thing on screen.
        """
        base = self._read_pose_base_frame()
        if not base:
            return False
        self._pose_pos_mm = np.asarray(base[:3], dtype=np.float32)
        return True

    def _read_pose_base_frame(self) -> list[float] | None:
        if not self._pose_path or not self._base_path:
            return None
        try:
            ws = PrimUtils.get_relative_prim_pose(
                prim_path_a=self._base_path,
                prim_path_b=self._pose_path,
                rotation_type="cartesian",
            )
            return list(ws.pose)
        except Exception as exc:
            carb.log_verbose(f"Envelope: pose read failed: {exc}")
            return None

    # -- compute -----------------------------------------------------------
    def _start_recompute(self) -> None:
        """Restart the sweep, dropping whatever was still running.

        The sweep itself is synchronous, but reaching it can await the motion
        group description, so two starts could still overlap. With the cloud
        hidden nothing starts: every trigger - a rotation, the density slider,
        a TCP or mounting change - lands here, and the marker does not need
        the cloud.
        """
        self._cancel_recompute()
        if not self._cloud_visible:
            return
        self._recompute_task = run_coroutine(self._recompute())

    def _cancel_recompute(self) -> None:
        if self._recompute_task is not None and not self._recompute_task.done():
            self._recompute_task.cancel()
        self._recompute_task = None

    async def _recompute(self):
        self._error = None
        self._busy = True
        self._emit_status()
        try:
            service = get_envelope_service()
            ctx = await self._ensure_ctx()
            if ctx is None:
                carb.log_warn(
                    "[Envelope] prepare() returned None (no description / "
                    "dh_parameters / not a motion group) - nothing to draw"
                )
                self._clear_scene()
                return
            self._refresh_marker_position()
            base = self._read_pose_base_frame()
            if base is None:
                carb.log_warn("[Envelope] could not read the pose orientation")
                self._clear_scene()
                return
            orientation = list(base[3:])
            self._envelope = await service.compute_oriented_envelope(
                ctx, orientation, _VOXEL_MM, num_samples=self._samples
            )
            self._envelope_orientation = orientation
            # After the assignment: invalidating before it would let a redraw in
            # between cache the previous envelope.
            self._invalidate_points()
            self._redraw()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # run_coroutine leaves this task unretained, so without catching here
            # the failure only shows up as "Task exception was never retrieved"
            # and the overlay silently stays empty.
            carb.log_error(f"[Envelope] recompute failed: {exc}")
            self._clear_scene()
            self._error = str(exc)
        finally:
            self._busy = False
            self._emit_status()

    # -- rendering ---------------------------------------------------------
    def _base_world_matrix(self):
        """Where the DH chain starts, in world.

        Read straight from the composed USD transform. PrimUtils.get_prim_pose
        would take the physics view for a rigid body - and the arm base is one -
        while the tool window reads the same frame from USD. Two different reads
        of one frame drift apart while the simulation runs, which puts the cloud
        somewhere the verdict in the window was never computed for.
        """
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(self._base_path) if stage else None
        if prim is None or not prim.IsValid():
            raise ValueError(f"No prim at {self._base_path}")
        matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        # Asset roots are often scaled; the cloud carries its own mm scale.
        matrix.Orthonormalize()
        return numpy_to_scene_matrix44(np.array(matrix).T)

    def _base_world_translation(self) -> tuple[float, float, float]:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(self._base_path) if stage else None
        matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        translation = matrix.ExtractTranslation()
        return (translation[0], translation[1], translation[2])

    def _unit_factor(self) -> float:
        """Stage units per millimetre, read from the stage rather than assumed."""
        return SceneUtils.millimeters_to_stage_value(1.0)

    def _redraw(self):
        if not self._enabled:
            carb.log_verbose("[Envelope] _redraw skipped: disabled")
            return
        if not self._ensure_scene():
            carb.log_warn("[Envelope] _redraw skipped: no scene view")
            return
        if self._base_path is None:
            carb.log_verbose("[Envelope] _redraw skipped: no motion group frame")
            return
        self._scene_view.scene.clear()
        scale = self._unit_factor()
        try:
            xform = self._base_world_matrix()
            self._base_world_position = self._base_world_translation()
        except Exception as exc:
            carb.log_warn(f"Envelope: motion group transform failed: {exc}")
            return

        positions, colors = self._points() if self._cloud_visible else ([], [])

        carb.log_verbose(
            f"[Envelope] redraw: points={len(positions)} "
            f"marker={'yes' if self._pose_pos_mm is not None else 'no'}"
        )
        with self._scene_view.scene:
            with sc.Transform(transform=xform):
                self._draw_points(positions, colors)
                self._draw_marker(scale)
        self._emit_status()

    def _invalidate_points(self) -> None:
        """Drop the cached point arrays; the next redraw rebuilds them."""
        self._point_cache = None

    def _points(self) -> tuple[list[tuple], list[list[float]]]:
        """Positions and colours of the envelope voxels, cached.

        Building these walks every voxel in Python, and a dense envelope has tens
        of thousands. _redraw runs on every translation of the watched pose - i.e.
        per frame while the gizmo is dragged - where only the marker moves, so the
        arrays are rebuilt only when something they depend on actually changed.
        """
        if self._point_cache is not None:
            return self._point_cache

        positions: list[tuple] = []
        colors: list[list[float]] = []
        if self._envelope is not None and self._envelope.centres_mm.size:
            scale = self._unit_factor()
            solid = self._reachable_color
            for centre, likelihood in zip(
                self._envelope.centres_mm, self._envelope.likelihood
            ):
                positions.append(
                    (centre[0] * scale, centre[1] * scale, centre[2] * scale)
                )
                if self._graded:
                    colors.append(_margin_color(float(likelihood), self._opacity))
                else:
                    colors.append([solid[0], solid[1], solid[2], self._opacity])

        self._point_cache = (positions, colors)
        return self._point_cache

    def _draw_points(self, positions: list[tuple], colors: list[list[float]]):
        if not positions:
            carb.log_info("[Envelope] draw_points: nothing to draw (0 points)")
            return
        point_size = self._camera_point_size()
        self._drawn_point_size = point_size
        sizes = [point_size] * len(positions)
        if hasattr(sc, "Points"):
            try:
                sc.Points(positions, colors=colors, sizes=sizes)
                carb.log_info(f"[Envelope] drew {len(positions)} points via sc.Points")
                return
            except Exception as exc:
                carb.log_warn(
                    f"[Envelope] sc.Points failed ({exc}); using line fallback"
                )
        else:
            carb.log_warn("[Envelope] sc.Points not available; using line fallback")
        # Fallback: small crosses via sc.Line, downsampled to keep it cheap.
        step = max(1, len(positions) // _MAX_FALLBACK_POINTS)
        h = self._point_size * 0.0015
        drawn = 0
        for i in range(0, len(positions), step):
            x, y, z = positions[i]
            c = colors[i]
            sc.Line((x - h, y, z), (x + h, y, z), color=c)
            sc.Line((x, y - h, z), (x, y + h, z), color=c)
            sc.Line((x, y, z - h), (x, y, z + h), color=c)
            drawn += 1
        carb.log_info(
            f"[Envelope] drew {drawn} points via sc.Line fallback (of {len(positions)})"
        )

    def _draw_marker(self, scale: float):
        if self._pose_pos_mm is None:
            return
        position = self._pose_pos_mm * scale
        color = [*self._marker_color(), 1.0]
        arm = self._point_size * 0.004
        x, y, z = (float(v) for v in position)
        sc.Line((x - arm, y, z), (x + arm, y, z), color=color)
        sc.Line((x, y - arm, z), (x, y + arm, z), color=color)
        sc.Line((x, y, z - arm), (x, y, z + arm), color=color)
        if hasattr(sc, "Points"):
            try:
                sc.Points([(x, y, z)], colors=[color], sizes=[self._point_size * 2.5])
            except Exception:
                pass

    def _marker_color(self) -> tuple[float, float, float]:
        """Marker colour for the window's verdict about the pose in hand.

        Amber is the case worth separating out: the pose is reachable, but no
        motion to it could be planned from where the robot stands.
        """
        if self._pose_reachable is None:
            return MARKER_UNKNOWN
        if not self._pose_reachable:
            return MARKER_UNREACHABLE
        if self._pose_plannable is False:
            return MARKER_NOT_PLANNABLE
        return MARKER_READY
