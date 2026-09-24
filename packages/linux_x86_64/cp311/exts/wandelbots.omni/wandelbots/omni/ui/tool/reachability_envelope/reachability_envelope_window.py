"""Reachability Envelope tool window.

Answers one question about a pose or ghost object: can the selected motion
group reach it - and where else could it reach? Then moves the robot there.

The window is three cards over one action bar:

- "Target" picks the motion group, the pose and the TCP. Poses and ghost
  objects are the only things listed, and selecting one in the stage points
  the tool at it (a ghost object also names its own motion group).
- "Envelope" draws the tool's namesake: the positions the TCP can pass
  through in some joint configuration, shaded by how often a local
  forward-kinematics sweep landed there. It is a hint about where placing a
  pose is worth trying, not a verdict - it ignores orientation. Moving or
  rotating the pose only moves the marker; the cloud is recomputed when the
  motion group, the TCP or the density changes.
- "Solution" leads with the reachability verdict and carries the pose, the
  joint values behind it and the last placement's result.

Reachability is decided by an inverse kinematics request that runs once the
pose has settled; the motion group's mounting and kinematic_chain_offset from
the NOVA description travel with it automatically.

"Snap pose" runs a local IK search - it works without the envelope and
without a solution - and reports how far the nearest reachable point is
BEFORE moving anything; the pose only moves once the offset is applied.
"Place robot" puts the robot at the solved joint values directly: the virtual
controller is teleported and the scene follows through the motion stream, with
no trajectory planned and nothing moving through space. A stopped simulation
is started for that, as a ghost-teaching move does. Whatever stops either of them is
listed under the action bar rather than only greying the button out.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from typing import Callable

import carb
import omni.kit.actions.core
import omni.kit.app
import omni.kit.menu.utils
import omni.timeline
import omni.ui as ui
import omni.usd
import numpy as np
from omni.kit.async_engine import run_coroutine
from pxr import Sdf, Usd, UsdGeom

import wandelbots_api_client.v2 as wb

from wandelbots.omni.constants import EXTENSION_ID, EXTENSION_WINDOW_MENU_ROOT
from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.manipulators import (
    get_motion_group_configuration_from_prim,
    get_scene_motion_group_prim_paths,
)
from wandelbots.omni.teaching.place_service import (
    PlacementResult,
    place_motion_group_at_joints,
)
from wandelbots.omni.manipulators import get_motion_group_service
from wandelbots.omni.manipulators.utils import get_link_0_from_motion_group_prim
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.overlay import (
    REACHABILITY_ENVELOPE_OVERLAY_NAME,
    get_overlay_registry,
)
from wandelbots.omni.ui.overlay.reachability_envelope.reachability_envelope_overlay import (
    DEFAULT_DENSITY,
    DEFAULT_OPACITY,
    margin_gradient_color,
)
from wandelbots.omni.ui.wb_theme import (
    BUTTON_HEIGHT,
    BUTTON_PRIMARY_STYLE,
    BUTTON_STYLE,
    COMBOBOX_STYLE,
    CORNER_RADIUS,
    FONT_SIZE_SM,
    SPACING_MD,
    SPACING_SM,
    SPACING_XS,
)
from wandelbots.omni.ui.utils import copy_to_clipboard
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.widgets.coordinates_input import (
    CoordinateInputFieldModel,
    CoordinatesInput,
)
from wandelbots.omni.ui.widgets.icon_button import ICON_BUTTON_SIZE, IconButton
from wandelbots.omni.ui.widgets.progress_status_bar import ProgressStatusBar
from wandelbots.omni.ui.widgets.section_divider import section_divider
from wandelbots.omni.ui.widgets.styled_checkbox import styled_checkbox
from wandelbots.omni.ui.tool.planner_utils import (
    PlanSuccess,
    check_plannable_from_current_state,
)
from wandelbots.omni.utils.kinematics import fetch_joint_configs_for_pose
from wandelbots.omni.ui.create_context_menu.pose import (
    is_pose_prim as is_pose_marker_prim,
)
from wandelbots.omni.utils.teaching import (
    GhostObjectUtils,
    make_ghost_tcp_matcher,
)
from wandelbots.omni.ui.tool.trajectory_planner.pose_utils import (
    get_pose_motion_metadata,
    set_pose_motion_metadata,
)
from wandelbots.omni.utils.prims import PrimUtils, is_pose_xform_op
from wandelbots.omni.utils.scene import SceneUtils

WINDOW_MENU_ROOT = "Tools"
WINDOW_TITLE = "Reachability Envelope"

_HINT_STYLE = {"color": NOVAColor.TEXT_SECONDARY.color, "font_size": FONT_SIZE_SM}

# The button state and the timers still tick; the pose itself arrives through
# the USD change watcher.
_POLL_S = 1.0 / 30.0
# What counts as the pose having moved. The pose is read relative to the robot's
# base, which is a simulated rigid body, so while the timeline plays PhysX
# rewrites that frame every step and the relative pose jitters. A threshold below
# that noise makes every poll look like a move: the settle timer never runs out,
# no solve ever starts, and "Place robot" waits for a solution that cannot arrive.
_POSE_POSITION_EPS_MM = 0.05
_POSE_ROTATION_EPS_RAD = 1e-3


def adopted_tcp_index(
    names: list[str], current_index: int, target_tcp: str | None
) -> int:
    """TCP to solve with once a new target is selected.

    The TCP the target names wins; a target that names none keeps whatever is
    selected, so a hand-picked TCP survives moving to a plain pose.
    """
    if target_tcp and target_tcp in names:
        return names.index(target_tcp)
    return current_index


def tcp_index_for(names: list[str], preferred: str | None) -> int:
    """Position of *preferred* in *names*, or 0 when it is not on offer."""
    if preferred and preferred in names:
        return names.index(preferred)
    return 0


def pose_vectors_match(left, right) -> bool:
    """Whether two [x, y, z, rx, ry, rz] vectors describe the same pose.

    Position entries are millimetres and rotation entries radians, so they get
    their own tolerances rather than one number standing in for both.
    """
    if left is None or right is None or len(left) != len(right):
        return False
    for index, (a, b) in enumerate(zip(left, right)):
        limit = _POSE_POSITION_EPS_MM if index < 3 else _POSE_ROTATION_EPS_RAD
        if abs(float(a) - float(b)) > limit:
            return False
    return True


_ERROR_BACKOFF_S = 2.0  # pause auto-solving after a failed IK request
_SOLVE_WATCHDOG_S = 15.0  # a solve stuck longer than this releases the guard
# The pose has to stand still this long before it is solved. A drag would
# otherwise fire a request per watcher notice, and NOVA's solver is the one
# resource in this tool worth being frugal with.
_LEGEND_STEPS = 24

# A value label's text IS its clipboard payload - the row name lives in its own
# label so it never ends up in the copy. This placeholder doubles as the
# "nothing to copy yet" marker.
_VALUE_EMPTY = "-"
# Shared by the combo rows and the value rows so both columns line up.
_LABEL_COLUMN_W = 80

# A new or closed stage: everything the tool and its overlay hold belongs to
# the old one.
_STAGE_REPLACED_EVENTS = (
    int(omni.usd.StageEventType.OPENED),
    int(omni.usd.StageEventType.CLOSED),
)

# Below this the density slider means "no cloud" rather than "the coarsest one".
# A slider rarely lands on an exact zero, hence a threshold and not == 0.
_DENSITY_OFF = 1e-3

# Idle captions of the action buttons; both also caption their busy state.
_SNAP_LABEL = "Snap pose"
_SNAP_APPLY_LABEL = "Apply snap"
_SNAP_DISCARD_LABEL = "Keep pose"
_PLACE_LABEL = "Place robot"
_CANCEL_LABEL = "Cancel"
# A placement that reports nothing for this long is treated as hung: the guard
# is released and the buttons come back rather than staying stuck.
_PLACE_TIMEOUT_S = 30.0

# Reachability chip captions.
_VERDICT_IDLE = "no pose selected"
_VERDICT_SOLVING = "solving..."
_VERDICT_REACHABLE = "reachable"
_VERDICT_UNREACHABLE = "not reachable"

# Motion chip captions. The check needs a settled pose, so a pose in motion
# reads as idle rather than claiming an answer it does not have.
_MOTION_IDLE = "-"
_MOTION_CHECKING = "checking..."
_MOTION_PLANNABLE = "plannable"
_MOTION_BLOCKED = "not plannable"
# A planning request costs far more than an IK solve, so it waits for the pose
# to stop moving; the IK verdict does not.
_PLAN_SETTLE_S = 0.4
_PLAN_WATCHDOG_S = 30.0


def state_chip(selected: bool, solved: bool, answered: bool) -> tuple[str, int]:
    """Caption + colour of the State chip - the pose's own IK answer."""
    if not selected:
        return _VERDICT_IDLE, NOVAColor.TEXT_SECONDARY.color
    if solved:
        return _VERDICT_REACHABLE, NOVAColor.SUCCESS_LIGHT.color
    if not answered:
        return _VERDICT_SOLVING, NOVAColor.TEXT_SECONDARY.color
    return _VERDICT_UNREACHABLE, NOVAColor.WARNING_LIGHT.color


def motion_chip(
    solved: bool, checking: bool, plannable: bool | None
) -> tuple[str, int]:
    """Caption + colour of the Motion chip - can the robot get there from here.

    Without an IK solution there is nothing to plan to, so the chip stays idle
    rather than reporting a failure the pose is not responsible for.
    """
    if not solved:
        return _MOTION_IDLE, NOVAColor.TEXT_SECONDARY.color
    if checking or plannable is None:
        return _MOTION_CHECKING, NOVAColor.TEXT_SECONDARY.color
    if plannable:
        return _MOTION_PLANNABLE, NOVAColor.SUCCESS_LIGHT.color
    return _MOTION_BLOCKED, NOVAColor.WARNING_LIGHT.color


def format_tcp_pose(values) -> str:
    """The pose as the copy chip hands it out: [x,y,z,rx,ry,rz].

    Position in millimetres, rotation as a rotation vector in radians - the
    same six numbers the coordinate fields show, so what is copied is what is
    on screen.
    """
    x, y, z, rx, ry, rz = (float(v) for v in values)
    return f"[{x:.2f},{y:.2f},{z:.2f},{rx:.4f},{ry:.4f},{rz:.4f}]"


def envelope_is_wanted(enabled: bool, density: float) -> bool:
    """Whether a cloud should be on screen at all.

    A density of zero asks for no cloud, so none is drawn. Answering it with
    the coarsest cloud the slider can produce reads as a broken control.
    """
    return bool(enabled) and density > _DENSITY_OFF


def deepest_pose_ancestor(path: str, poses) -> str | None:
    """Innermost pose in *poses* that *path* lies under, not the first found.

    Sorted order puts an enclosing folder before the poses inside it, so
    stopping at the first match resolved a selection to the folder.
    """
    best: str | None = None
    for pose in poses:
        if path == pose or path.startswith(pose + "/"):
            if best is None or len(pose) > len(best):
                best = pose
    return best


def resolve_pose_prim_path(path: str, is_pose) -> str | None:
    """Innermost pose or ghost object at or above the clicked *path*, or None.

    A viewport click lands on the mesh inside a ghost object, not on the prim
    carrying GhostObjectAPI, so the walk goes up towards the stage root. It
    stops before /World, which is never a pose.

    ``is_pose`` maps a prim path to True/False; keeping the USD lookup out of
    here makes the walk itself testable.
    """
    if not path or not path.startswith("/"):
        return None
    candidate = path
    while candidate.count("/") > 1:
        if is_pose(candidate):
            return candidate
        candidate = candidate.rsplit("/", 1)[0]
    return None


class ReachabilityEnvelopeWindow:
    def __init__(self) -> None:
        self._motion_groups: list[str] = []
        self._poses: list[str] = []
        self._tcps: list[str] = []
        self._mg_index = 0
        self._pose_index = 0
        self._tcp_index = 0
        self._description = None
        # Guards against an older description answering after the selection moved.
        self._description_token = 0

        self._enable_model = ui.SimpleBoolModel(False)
        self._density_model = ui.SimpleFloatModel(DEFAULT_DENSITY)
        self._opacity_model = ui.SimpleFloatModel(DEFAULT_OPACITY)
        # The solved TCP pose, shown in Isaac Sim's own coordinate fields.
        self._tcp_position_models = [ui.SimpleFloatModel(0.0) for _ in range(3)]
        self._tcp_rotation_models = [ui.SimpleFloatModel(0.0) for _ in range(3)]

        self._ik_configs: list[list[float]] = []
        # Rounded pose vector the current _ik_configs were solved for - lets
        # "Place robot" detect stale solutions after the pose moved again.
        self._configs_pose_vec: tuple | None = None
        self._last_moved_joints: list[float] | None = None
        # Last solved solution - IK seed for the next solve (avoids the
        # per-solve current-state fetch and keeps the branch stable).
        self._seed_joints: list[float] | None = None
        self._snap_pending = False
        # "Place robot" was clicked on stale solutions: place the robot as
        # soon as the fresh solve for the current pose lands.
        self._move_after_solve = False

        # Action-button state: a placement in flight and the overlay's busy
        # flag (snap search / envelope recompute).
        self._moving = False
        self._overlay_busy = False
        # (fraction, hint) while the envelope computes, None when it is idle.
        # A snap target found but not applied yet - the prompt's payload.
        self._snap_target = None
        # Last (enabled, text) pair pushed to the buttons - see _refresh_actions.
        self._actions_state: tuple | None = None

        # Live-solve state. The pose is followed through the USD change
        # watcher, so the verdict keeps up with a drag instead of waiting for
        # the user to let go.
        self._update_sub = None
        self._stage_event_sub = None
        self._pose_watch_subs: list = []
        self._watched_pose_path: str | None = None
        self._last_poll = 0.0
        self._pose_readable = True
        self._last_pose_vec: tuple | None = None
        # Newest pose waiting for a solve; a solve already in flight picks it
        # up when it lands, so requests never queue up behind a drag.
        self._pending_pose = None
        self._pose_still_since = 0.0
        self._solving = False
        self._solving_since = 0.0
        self._backoff_until = 0.0

        # Plannability of the selected pose from where the robot stands now.
        # Unlike the IK solve this is a planning request, so it waits for the
        # pose to come to rest.
        self._plannable: bool | None = None
        self._plan_error: str | None = None
        self._planning = False
        self._planning_since = 0.0
        self._plan_task = None
        self._planned_joints: list[float] | None = None

        # Widgets (created in _build).
        self._mg_frame = None
        self._tcp_frame = None
        self._pose_frame = None
        self._tcp_pose_readable = False
        self._tcp_pose_fields = None
        self._tcp_pose_empty_row = None
        self._density_label = None
        self._motion_label = None
        self._joints_label = None
        self._residual_label = None
        self._snap_offset_label = None
        self._status_label = None
        self._snap_button = None
        self._move_button = None
        self._cancel_button = None
        self._verdict_label = None
        self._readiness_frame = None
        self._progress: ProgressStatusBar | None = None

        self._window = ui.Window(WINDOW_TITLE, width=340, height=380)
        # Same dock target as every other tool window (Collision Setup,
        # Trajectory Planner, Mounting Assistant): the right-hand panel next to
        # Property / Settings, instead of opening as a floating window.
        self._window.deferred_dock_in(
            "Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE
        )
        self._window.visible = False
        self._window.set_visibility_changed_fn(self._on_visibility_changed)
        self._build()

    @property
    def window(self) -> ui.Window:
        return self._window

    def destroy(self) -> None:
        self._snap_target = None
        self._stop_live_updates()
        self._remove_visualizations()
        ov = self._overlay(attach_status=False)
        if ov is not None:
            ov.set_status_callback(None)
        if self._window:
            self._window.set_visibility_changed_fn(None)
        self._window = None

    def _on_visibility_changed(self, visible: bool) -> None:
        if visible:
            # Start the loop FIRST - a failure in the list refreshes must never
            # leave the window open without live updates.
            self._start_live_updates()
            try:
                self._refresh_motion_groups()
                self._refresh_poses()
                self._adopt_current_selection()
                self._subscribe_pose_watch(self._selected_pose_path())
                # The overlay lives as long as the window does, so the marker is
                # on the pose from the moment the tool is open. The cloud waits
                # for its checkbox.
                self._with_overlay(lambda ov: ov.set_enabled(True), quiet=True)
            except Exception as e:
                carb.log_warn(f"{WINDOW_TITLE}: refresh on open failed: {e}")
        else:
            self._stop_live_updates()
            self._remove_visualizations()

    def _remove_visualizations(self) -> None:
        """Leave the viewport as it was found: cloud off, then overlay off."""
        if self._enable_model.get_value_as_bool():
            self._enable_model.set_value(False)  # fires _on_enable -> hide cloud
        ov = self._overlay(attach_status=False)
        if ov is not None:
            ov.set_enabled(False)

    # -- data access ---------------------------------------------------------
    def _stage(self):
        return omni.usd.get_context().get_stage()

    def _overlay(self, attach_status: bool = True):
        ov = get_overlay_registry().get_overlay(REACHABILITY_ENVELOPE_OVERLAY_NAME)
        if ov is not None and attach_status:
            ov.set_status_callback(self._on_status)
        return ov

    def _selected_mg_path(self) -> str | None:
        if not self._motion_groups or not (
            0 <= self._mg_index < len(self._motion_groups)
        ):
            return None
        return self._motion_groups[self._mg_index]

    def _selected_mg_prim(self):
        path = self._selected_mg_path()
        stage = self._stage()
        if path is None or stage is None:
            return None
        prim = stage.GetPrimAtPath(path)
        return prim if prim and prim.IsValid() else None

    def _selected_pose_path(self) -> str | None:
        if not self._poses or not (0 <= self._pose_index < len(self._poses)):
            return None
        return self._poses[self._pose_index]

    def _stream_config(self):
        prim = self._selected_mg_prim()
        if prim is None:
            return None
        config = get_motion_group_configuration_from_prim(prim)
        return config.motion_stream_configuration if config else None

    # -- list refresh ----------------------------------------------------------
    def _refresh_motion_groups(self) -> None:
        selected = self._selected_mg_path()
        self._motion_groups = list(get_scene_motion_group_prim_paths())
        self._mg_index = (
            self._motion_groups.index(selected)
            if selected in self._motion_groups
            else 0
        )
        self._rebuild_mg_combo()
        self._on_mg_selected()

    @staticmethod
    def _is_pose_prim(prim) -> bool:
        """A pose prim or a ghost object - the only two things this tool targets.

        Both predicates come from the objects that define them (POSE custom data
        via the convert-to-pose service, GhostObjectAPI via GhostObjectUtils)
        rather than being re-derived here, so the dropdown lists exactly what the
        rest of the extension calls a pose.
        """
        if prim is None or not prim.IsValid():
            return False
        try:
            return is_pose_marker_prim(prim) or GhostObjectUtils.is_ghost_object(prim)
        except Exception as e:
            carb.log_verbose(f"Could not classify {prim.GetPath()} as a pose: {e}")
            return False

    def _refresh_poses(self) -> None:
        selected = self._selected_pose_path()
        stage = self._stage()
        poses: set[str] = set()
        if stage:
            for prim in stage.Traverse():
                if self._is_pose_prim(prim):
                    poses.add(prim.GetPath().pathString)
        self._poses = sorted(poses)
        self._pose_index = self._poses.index(selected) if selected in self._poses else 0
        self._rebuild_pose_combo()
        self._on_pose_selected()

    def _refresh_description(self) -> None:
        self._description = None
        self._tcps = []
        self._tcp_index = 0
        self._rebuild_tcp_combo()
        stream = self._stream_config()
        if stream is None:
            return

        self._description_token += 1
        token = self._description_token
        # Until the new description lands the overlay prepares from its own
        # fetch of it; the previous robot's mounting belongs to another robot.
        self._with_overlay(lambda ov: ov.set_mounting(None), quiet=True)

        async def _load():
            try:
                async with stream.get_api_client() as api:
                    desc = await wb.MotionGroupApi(api).get_motion_group_description(
                        cell=stream.cell,
                        controller=stream.controller,
                        motion_group=stream.motion_group,
                    )
                if token != self._description_token:
                    # The selection moved on while this was in flight. Letting
                    # the older answer land would pair the new stream config
                    # with another robot's model, TCPs and mounting.
                    return
                self._description = desc
                self._tcps = list((desc.tcps or {}).keys())
                self._tcp_index = self._preferred_tcp_index(desc.tcps or {})
                self._rebuild_tcp_combo()
                # Forwarded even when None: a robot without a mounting must not
                # inherit the one the previous selection left in the overlay.
                self._with_overlay(
                    lambda ov: ov.set_mounting(desc.mounting), quiet=True
                )
                self._push_tcp_to_overlay()
                self._mark_pose_dirty()  # solve against the fresh description
            except Exception as e:
                self._set_status(f"Could not load motion group description: {e}")

        run_coroutine(_load())

    def _preferred_tcp_index(self, tcps: dict) -> int:
        """Index of the TCP the selected target already carries, else the first.

        A ghost object names its source TCP and a pose prim persists the choice
        in its ``wandelbots`` custom data. Resetting to index 0 silently
        re-solves the target against whichever TCP the description happens to
        list first, which is a different tool and a different answer.
        """
        return tcp_index_for(self._tcps, self._target_tcp_name(tcps))

    def _target_tcp_name(self, tcps: dict) -> str | None:
        stage = self._stage()
        path = self._selected_pose_path()
        if stage is None or path is None:
            return None
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            return None
        if GhostObjectUtils.is_ghost_object(prim):
            return make_ghost_tcp_matcher(prim)(tcps)
        return get_pose_motion_metadata(prim)

    def _selected_tcp(self) -> str | None:
        if not self._tcps or not (0 <= self._tcp_index < len(self._tcps)):
            return None
        return self._tcps[self._tcp_index]

    def _push_tcp_to_overlay(self) -> None:
        self._with_overlay(lambda ov: ov.set_tcp(self._selected_tcp()), quiet=True)

    # -- UI ---------------------------------------------------------------------
    def _build(self) -> None:
        self._window.frame.style_type_name_override = "RootFrame"
        self._window.frame.style = {
            "RootFrame": {"background_color": NOVAColor.LAYER_BASE.color},
        }
        with self._window.frame:
            with ui.ScrollingFrame(
                vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                width=ui.Percent(100),
                height=ui.Percent(100),
                style={
                    "ScrollingFrame": {"background_color": NOVAColor.LAYER_BASE.color}
                },
            ):
                with ui.HStack():
                    ui.Spacer(width=SPACING_SM)
                    with ui.VStack(spacing=SPACING_SM):
                        ui.Spacer(height=SPACING_SM)
                        self._build_content()
                        ui.Spacer(height=SPACING_SM)
                    ui.Spacer(width=SPACING_SM)
        self._refresh_motion_groups()
        self._refresh_poses()

    def _build_content(self) -> None:
        """Three cards - Target, Solution, Envelope - over one action bar.

        The cards follow the Collision Setup window's shell (scrolling frame,
        inset content, one card per concern); what differs is that every row
        here is a live read-out, so each card owns a Frame it rebuilds on its
        own instead of the whole window being torn down on every change.
        """
        target = CollapsibleSection("Target", collapsed=False)
        with target.body:
            with ui.VStack(height=0, spacing=SPACING_SM):
                ui.Spacer(height=SPACING_XS)
                self._build_row_frames()
                ui.Spacer(height=SPACING_XS)

        # The envelope is what this tool is for, so it sits right under the
        # target selection and starts open - the numbers below it read as the
        # detail behind the picture, not the other way round.
        envelope = CollapsibleSection("Envelope", collapsed=False)
        with envelope.body:
            with ui.VStack(height=0, spacing=SPACING_SM):
                ui.Spacer(height=SPACING_XS)
                self._build_envelope_controls()
                ui.Spacer(height=SPACING_XS)

        solution = CollapsibleSection("Solution", collapsed=False)
        with solution.body:
            with ui.VStack(height=0, spacing=SPACING_SM):
                ui.Spacer(height=SPACING_XS)
                self._build_verdict_row()
                self._build_tcp_pose_rows()
                self._joints_label = self._copyable_value_row(
                    "Joints",
                    "Copy (j1, j2, ... jn) - joint values in rad, for the "
                    "solution the robot would move to",
                )
                self._snap_offset_label = self._value_row(
                    "Snap offset",
                    "How far Snap pose would move the pose, and in which "
                    "direction - shown before anything is moved",
                )
                self._residual_label = self._value_row(
                    "Last placement",
                    "Result of the last placement: how far the controller "
                    "reports the robot from the joint values it was given",
                )
                ui.Spacer(height=SPACING_XS)

        ui.Spacer(height=SPACING_XS)
        section_divider()
        ui.Spacer(height=SPACING_XS)

        self._build_action_bar()
        self._readiness_frame = ui.Frame(height=0)
        self._status_label = ui.Label("", word_wrap=True, style=_HINT_STYLE)
        self._refresh_actions()

    def _build_row_frames(self) -> None:
        """Host the three selector rows inside the Target card."""
        with ui.VStack(height=0, spacing=SPACING_SM):
            self._mg_frame = ui.Frame(height=0)
            self._tcp_frame = ui.Frame(height=0)
            self._pose_frame = ui.Frame(height=0)

    def _build_verdict_row(self) -> None:
        """The two answers, as chips: can the robot stand there, can it get there."""
        self._verdict_label = self._chip_row(
            "State",
            _VERDICT_IDLE,
            "Whether inverse kinematics solves for the pose as it stands right "
            "now. Recomputed live while the pose is moved",
        )
        self._motion_label = self._chip_row(
            "Motion",
            _MOTION_IDLE,
            "Whether NOVA can plan a point-to-point move to the pose from where "
            "the robot stands right now. Checked once the pose comes to rest; "
            "the plan ignores collision setups, so this is a kinematic check",
        )

    def _chip_row(self, name: str, caption: str, tooltip: str) -> ui.Label:
        """Name column + a rounded chip, and return the chip's label."""
        with ui.HStack(height=ui.Pixel(22), spacing=SPACING_SM):
            ui.Label(name, width=ui.Pixel(_LABEL_COLUMN_W), style=_HINT_STYLE)
            with ui.ZStack(width=0):
                ui.Rectangle(
                    style={
                        "background_color": NOVAColor.SURFACE_OVERLAY.color,
                        "border_radius": CORNER_RADIUS,
                    }
                )
                with ui.HStack(height=0):
                    ui.Spacer(width=SPACING_MD)
                    label = ui.Label(
                        caption,
                        width=0,
                        style={
                            "color": NOVAColor.TEXT_SECONDARY.color,
                            "font_size": FONT_SIZE_SM,
                        },
                        tooltip=tooltip,
                    )
                    ui.Spacer(width=SPACING_MD)
            ui.Spacer()
        return label

    def _build_envelope_controls(self) -> None:
        with ui.HStack(height=0, spacing=SPACING_SM):
            styled_checkbox(self._enable_model, width=20)
            ui.Label("Show envelope", width=0)
            ui.Spacer()
        with ui.HStack(height=0, spacing=SPACING_SM):
            ui.Label(
                "Density",
                width=ui.Pixel(_LABEL_COLUMN_W),
                style=_HINT_STYLE,
                tooltip=(
                    "How many points the cloud is sampled from. More means "
                    "more inverse-kinematics requests, a slower recompute and "
                    "more to draw; at zero no cloud is drawn"
                ),
            )
            ui.FloatSlider(self._density_model, min=0.0, max=1.0)
        self._density_label = self._value_row(
            "Voxels",
            "How many voxels are drawn and how big they are",
        )
        with ui.HStack(height=0, spacing=SPACING_SM):
            ui.Label("Opacity", width=ui.Pixel(_LABEL_COLUMN_W), style=_HINT_STYLE)
            ui.FloatSlider(self._opacity_model, min=0.05, max=1.0)
        self._enable_model.add_value_changed_fn(self._on_enable)
        self._density_model.add_value_changed_fn(self._on_density)
        self._opacity_model.add_value_changed_fn(self._on_opacity)
        self._build_legend()

    def _build_action_bar(self) -> None:
        with ui.HStack(height=BUTTON_HEIGHT, spacing=SPACING_SM):
            self._snap_button = ui.Button(
                _SNAP_LABEL,
                style=BUTTON_STYLE,
                clicked_fn=self._on_snap,
                tooltip=(
                    "Local IK search around the pose: find the nearest point "
                    "reachable at its current orientation. The offset is shown "
                    "first - the pose only moves once you apply it"
                ),
            )
            self._move_button = ui.Button(
                _PLACE_LABEL,
                style=BUTTON_PRIMARY_STYLE,
                clicked_fn=lambda: run_coroutine(self._place_robot_async()),
                tooltip=(
                    "Put the robot at the solved joint values directly, with no "
                    "trajectory: the virtual controller is teleported and the "
                    "scene follows through the motion stream. Starts the "
                    "simulation if it is stopped"
                ),
            )
            self._cancel_button = ui.Button(
                _SNAP_DISCARD_LABEL,
                width=ui.Pixel(90),
                style=BUTTON_STYLE,
                visible=False,
                clicked_fn=self._on_discard_snap,
                tooltip="Leave the pose where it is and drop the snap offset",
            )
        self._progress = ProgressStatusBar()
        self._progress.build()

    def _build_tcp_pose_rows(self) -> None:
        """The solved TCP pose in Isaac Sim's coordinate fields.

        Read-only: the pose is driven by the prim in the viewport, and these
        are its read-out. Isaac Sim's own X/Y/Z colouring is what a user
        already reads transforms by, so a pose here looks like a pose
        everywhere else in the app.
        """
        # Without a pose the fields would read as a pose at the origin, so
        # they give way to an empty row until there is one to show.
        self._tcp_pose_empty_row = ui.HStack(height=0, spacing=SPACING_SM)
        with self._tcp_pose_empty_row:
            ui.Label("TCP pose", width=ui.Pixel(_LABEL_COLUMN_W), style=_HINT_STYLE)
            ui.Label(_VALUE_EMPTY, style=_HINT_STYLE)
        self._tcp_pose_fields = ui.VStack(height=0, spacing=SPACING_SM, visible=False)
        with self._tcp_pose_fields:
            self._build_tcp_pose_field_rows()

    def _build_tcp_pose_field_rows(self) -> None:
        with ui.HStack(height=0, spacing=SPACING_SM):
            ui.Label("Position", width=ui.Pixel(_LABEL_COLUMN_W), style=_HINT_STYLE)
            self._coordinate_fields(
                self._tcp_position_models,
                ("X", "Y", "Z"),
                "Millimetres, relative to the robot base",
            )
            IconButton(
                "copy.svg",
                tooltip=(
                    "Copy [x,y,z,rx,ry,rz] - position in mm relative to the "
                    "robot base, rotation as a rotation vector in rad"
                ),
                # A weak proxy, not self: a bound method on the chip would
                # keep the window alive across an extension reload.
                clicked_fn=lambda w=weakref.proxy(self): w._copy_tcp_pose(),
            )
        with ui.HStack(height=0, spacing=SPACING_SM):
            ui.Label("Rotation", width=ui.Pixel(_LABEL_COLUMN_W), style=_HINT_STYLE)
            self._coordinate_fields(
                self._tcp_rotation_models,
                ("rx", "ry", "rz"),
                "Rotation vector in rad, relative to the robot base",
            )
            # Keep the fields under the ones above: without this the row is
            # wider by exactly the copy chip the first row carries.
            ui.Spacer(width=ICON_BUTTON_SIZE)

    @staticmethod
    def _coordinate_fields(models, labels, tooltip: str) -> CoordinatesInput:
        """Isaac Sim's X/Y/Z coordinate fields over the given models.

        A read-out, so the fields are not editable but keep their axis
        colours rather than looking disabled.
        """
        return CoordinatesInput(
            [
                CoordinateInputFieldModel(
                    model=model, label=label, tooltip=tooltip, step=0.01
                )
                for model, label in zip(models, labels)
            ],
            readonly=True,
            gray_when_readonly=False,
        )

    def _copyable_value_row(self, name: str, tooltip: str) -> ui.Label:
        """Name column + read-only value + a copy chip, and return the value label.

        Copying reads the value label rather than re-deriving the value, so what
        lands on the clipboard is always what the user is looking at - and the
        row name, living in its own label, stays out of it. The closure holds the
        label only; capturing ``self`` here would keep the window alive across
        extension reloads.
        """
        with ui.HStack(height=0, spacing=SPACING_SM):
            ui.Label(name, width=ui.Pixel(_LABEL_COLUMN_W), style=_HINT_STYLE)
            # One line, elided rather than wrapped. A wrapped value in a
            # height-0 row is laid out against the label's own preferred width,
            # so it breaks early and the second line runs into the row below.
            # The copy chip carries the full value either way.
            value = ui.Label(_VALUE_EMPTY, elided_text=True, style=_HINT_STYLE)
            IconButton(
                "copy.svg",
                tooltip=tooltip,
                clicked_fn=lambda ref=value: (
                    copy_to_clipboard(ref.text) if ref.text != _VALUE_EMPTY else None
                ),
            )
        return value

    def _value_row(self, name: str, tooltip: str) -> ui.Label:
        """Name column + read-only value, for values that are not worth copying."""
        with ui.HStack(height=0, spacing=SPACING_SM):
            ui.Label(name, width=ui.Pixel(_LABEL_COLUMN_W), style=_HINT_STYLE)
            value = ui.Label(
                _VALUE_EMPTY, elided_text=True, style=_HINT_STYLE, tooltip=tooltip
            )
        return value

    # -- readiness -------------------------------------------------------------------
    def _blockers(self) -> list[str]:
        """Everything that stops "Place robot" from doing anything right now.

        Stated as what the user has to fix, in the order they would fix it, and
        shown in the window - a disabled button on its own never explains why.
        """
        problems: list[str] = []
        if self._selected_mg_prim() is None:
            problems.append("Select a motion group.")
        elif self._stream_config() is None:
            problems.append("The selected prim is not a configured motion group.")
        if self._selected_pose_path() is None:
            problems.append("Select a pose prim to place the robot at.")
        elif not self._pose_readable:
            problems.append("The pose prim's transform cannot be read.")
        if self._description is None:
            problems.append("Waiting for the motion group description...")
        elif not self._selected_tcp():
            problems.append("The motion group has no TCP defined.")
        elif self._solving:
            problems.append("Solving inverse kinematics...")
        elif not self._has_fresh_solution():
            problems.append("No IK solution for this pose - try Snap pose.")
        return problems

    def _warnings(self) -> list[str]:
        """Reasons the robot IN THE SCENE may lag the controller.

        These never block a placement: the controller is set either way, and a
        stopped simulation is started by it. They are the difference between
        "nothing happened" and "the scene is not listening", which is the
        confusing case worth naming up front.

        The plan failure joins them for the same reason: it says the robot
        cannot drive to a pose it can nonetheless be placed at, and the reason
        NOVA gave is the only way to tell those apart.
        """
        notes: list[str] = []
        if self._plannable is False and self._plan_error:
            notes.append(f"No motion could be planned to this pose: {self._plan_error}")
        stream = self._stream_config()
        if stream is None:
            return notes
        service = get_motion_group_service()
        if service is None or not service.has_streamable_motion_group(stream):
            return notes  # nothing in the scene follows this motion group anyway
        if not omni.timeline.get_timeline_interface().is_playing():
            notes.append("Simulation is stopped - Place robot starts it.")
        elif not service.is_stream_live(stream):
            notes.append(
                "Motion stream not connected - the robot in the scene will not follow."
            )
        return notes

    def _rebuild_readiness(self, lines: list[tuple[str, int]]) -> None:
        """Render the blocker/warning list; each entry is (text, color)."""
        if self._readiness_frame is None:
            return
        self._readiness_frame.clear()
        with self._readiness_frame:
            # One VStack: a Frame keeps only its last direct child, so the rows
            # have to share a single container or all but one disappear.
            with ui.VStack(height=0, spacing=SPACING_XS):
                for text, color in lines:
                    with ui.HStack(height=0, spacing=SPACING_SM):
                        ui.Label(
                            "-",
                            width=ui.Pixel(8),
                            style={"color": color, "font_size": FONT_SIZE_SM},
                        )
                        ui.Label(
                            text,
                            word_wrap=True,
                            style={"color": color, "font_size": FONT_SIZE_SM},
                        )

    def _verdict(self) -> tuple[str, int]:
        return state_chip(
            selected=self._selected_pose_path() is not None
            and self._selected_mg_prim() is not None,
            solved=self._has_fresh_solution(),
            answered=not self._solving and self._configs_pose_vec is not None,
        )

    def _motion_verdict(self) -> tuple[str, int]:
        return motion_chip(
            solved=self._has_fresh_solution(),
            checking=self._planning,
            plannable=self._plannable,
        )

    def _build_legend(self) -> None:
        """Green->orange spectrum: how freely the arm can reach each voxel.

        Every voxel is a NOVA inverse-kinematics solution at the orientation the
        cloud is drawn for, graded by how far that solution sits from the joint
        limits. It is still a hint about where to put a pose, not the verdict on
        one: that comes from the single solve behind the "State" row, which
        answers for the pose exactly as it is authored.
        """

        def _color_int(rgb: list[float]) -> int:
            r, g, b = (int(max(0.0, min(1.0, c)) * 255) for c in rgb)
            return 0xFF000000 | (b << 16) | (g << 8) | r

        with ui.VStack(height=0, spacing=2):
            with ui.HStack(height=ui.Pixel(8), spacing=0):
                for step in range(_LEGEND_STEPS):
                    # left = orange (near joint limits) ... right = green (free)
                    margin = step / (_LEGEND_STEPS - 1)
                    ui.Rectangle(
                        style={
                            "background_color": _color_int(
                                margin_gradient_color(margin)
                            )
                        }
                    )
            with ui.HStack(height=0):
                ui.Label("hard to reach", width=0, style=_HINT_STYLE)
                ui.Spacer()
                ui.Label("easy to reach", width=0, style=_HINT_STYLE)
            ui.Label(
                "Every point is somewhere the tool fits, turned the way it is "
                "turned now. Orange means a joint gets close to its limit "
                "there, green means every joint stays at least half its range "
                'away from one. For the pose you picked, read "State" above.',
                word_wrap=True,
                style=_HINT_STYLE,
            )

    def _combo_block(
        self, frame, label, items, index, on_change, refresh_fn=None, select_fn=None
    ):
        """Label + combo in one row (label column left, combo fills)."""
        with frame:
            with ui.HStack(height=0, spacing=SPACING_SM):
                ui.Label(label, width=ui.Pixel(_LABEL_COLUMN_W), style=_HINT_STYLE)
                if select_fn:
                    IconButton(
                        "crosshair.svg",
                        tooltip=f"Select this {label.lower()} in the stage",
                        clicked_fn=select_fn,
                    )
                display = items if items else ["<none>"]
                index = min(max(index, 0), len(display) - 1)
                combo = ui.ComboBox(index, *display, style=COMBOBOX_STYLE)
                combo.model.add_item_changed_fn(
                    lambda m, _i, cb=on_change: cb(m.get_item_value_model().as_int)
                )
                if refresh_fn:
                    IconButton(
                        "refresh.svg",
                        tooltip=f"Re-read {label.lower()} list",
                        clicked_fn=refresh_fn,
                    )
        return combo

    def _rebuild_mg_combo(self) -> None:
        if self._mg_frame:
            self._combo_block(
                self._mg_frame,
                "Motion group",
                self._motion_groups,
                self._mg_index,
                self._on_mg_changed,
                refresh_fn=self._refresh_motion_groups,
            )

    def _rebuild_pose_combo(self) -> None:
        if self._pose_frame:
            self._combo_block(
                self._pose_frame,
                "Pose",
                self._poses,
                self._pose_index,
                self._on_pose_changed,
                refresh_fn=self._refresh_poses,
                select_fn=self._select_pose_in_scene,
            )

    def _rebuild_tcp_combo(self) -> None:
        if self._tcp_frame:
            self._combo_block(
                self._tcp_frame,
                "TCP",
                self._tcps,
                self._tcp_index,
                self._on_tcp_changed,
            )

    def _on_tcp_changed(self, index: int) -> None:
        self._tcp_index = index
        self._persist_tcp_choice()
        self._push_tcp_to_overlay()
        self._mark_pose_dirty()
        self._request_solve()

    def _persist_tcp_choice(self) -> None:
        """Store the chosen TCP on the pose prim so reopening restores it.

        Without this the next description load falls back to the first TCP in
        the list and silently solves the same pose against a different tool. A
        ghost object names its own TCP through the description, so its choice
        is not ours to write.
        """
        stage = self._stage()
        path = self._selected_pose_path()
        tcp_name = self._selected_tcp()
        if stage is None or path is None or tcp_name is None:
            return
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            return
        if GhostObjectUtils.is_ghost_object(prim):
            return
        set_pose_motion_metadata(stage, path, tcp_name=tcp_name)

    def _update_joints_label(self) -> None:
        if self._joints_label is None:
            return
        joints = self._selected_solution()
        if joints is None:
            self._joints_label.text = _VALUE_EMPTY
        else:
            self._joints_label.text = "(" + ", ".join(f"{v:.4f}" for v in joints) + ")"

    def _update_tcp_pose_fields(self, pose) -> None:
        """Mirror the pose the IK is solved for into the coordinate fields."""
        self._tcp_pose_readable = pose is not None
        if self._tcp_pose_fields is not None:
            self._tcp_pose_fields.visible = pose is not None
            self._tcp_pose_empty_row.visible = pose is None
        if pose is None:
            return
        for model, value in zip(
            self._tcp_position_models + self._tcp_rotation_models,
            (float(v) for v in pose.pose),
        ):
            model.set_value(value)

    def _copy_tcp_pose(self) -> None:
        if not self._tcp_pose_readable:
            return
        copy_to_clipboard(
            format_tcp_pose(
                [model.as_float for model in self._tcp_position_models]
                + [model.as_float for model in self._tcp_rotation_models]
            )
        )

    # -- selection handlers -------------------------------------------------------
    def _on_mg_changed(self, index: int) -> None:
        self._mg_index = index
        self._on_mg_selected()

    def _on_mg_selected(self) -> None:
        self._seed_joints = None  # seed belongs to the previous motion group
        self._last_moved_joints = None
        self._refresh_description()
        self._with_overlay(
            lambda ov: ov.set_motion_group(self._selected_mg_path()), quiet=True
        )
        self._mark_pose_dirty()
        self._request_solve()

    def _on_pose_changed(self, index: int) -> None:
        self._pose_index = index
        self._select_pose_in_scene()
        self._on_pose_selected()

    def _adopt_target_tcp(self) -> None:
        """Solve the newly selected target with the TCP it names.

        The description usually lands before a pose is selected, so the TCP it
        restores then belongs to no target at all - typically the first one in
        the list, the flange. Without switching here a gripper's target is
        solved for the flange, and the gripper lands its own offset away.
        """
        if self._description is None:
            return
        index = adopted_tcp_index(
            self._tcps,
            self._tcp_index,
            self._target_tcp_name(self._description.tcps or {}),
        )
        if index == self._tcp_index:
            return
        self._tcp_index = index
        self._rebuild_tcp_combo()
        self._push_tcp_to_overlay()

    def _select_pose_in_scene(self) -> None:
        """Mirror the combo pick into the viewport selection so the pose gets
        the manipulator gizmo right away."""
        path = self._selected_pose_path()
        if not path:
            return
        try:
            omni.usd.get_context().get_selection().set_selected_prim_paths([path], True)
        except Exception as e:
            carb.log_verbose(f"Could not select pose prim in scene: {e}")

    def _on_pose_selected(self) -> None:
        path = self._selected_pose_path()
        self._adopt_target_tcp()
        self._with_overlay(lambda ov: ov.set_pose_path(path), quiet=True)
        self._last_pose_vec = None
        self._mark_pose_dirty()
        self._subscribe_pose_watch(path)

    # -- target pose (always link_0-relative, never world) --------------------------
    def _current_target_pose(self):
        """Pose prim in the frame the DH chain starts from, pure-USD (mm / rotvec).

        That is link_0 where the asset has one, which is what NOVA states its IK
        poses in. Reading the pose relative to the motion group prim instead
        offsets every answer by however far the two sit apart - nothing on a
        UR-style arm, ~416 mm on a MANUS one.

        Deliberately NOT PrimUtils.get_relative_prim_pose: that reads rigid
        bodies through the physics view (RigidPrim), which needs a live
        simulation backend and wedges after play/pause cycles - the authored
        USD transforms are valid in every timeline state and much cheaper to
        poll.
        """
        motion_group_prim = self._selected_mg_prim()
        ref_prim = (
            get_link_0_from_motion_group_prim(motion_group_prim)
            if motion_group_prim is not None
            else None
        )
        pose_path = self._selected_pose_path()
        stage = self._stage()
        if ref_prim is None or pose_path is None or stage is None:
            return None
        try:
            pose_prim = stage.GetPrimAtPath(pose_path)
            if not pose_prim or not pose_prim.IsValid():
                return None
            time_code = Usd.TimeCode.Default()
            ref_g = UsdGeom.Xformable(ref_prim).ComputeLocalToWorldTransform(time_code)
            pose_g = UsdGeom.Xformable(pose_prim).ComputeLocalToWorldTransform(
                time_code
            )
            # Strip inherited scale (asset roots are often scaled), keep
            # translation - same normalization as PrimUtils' xformable path.
            ref_g.Orthonormalize()
            pose_g.Orthonormalize()
            # Gf matrices are row-vector convention -> transpose to column.
            ref_m = np.array(ref_g).T
            pose_m = np.array(pose_g).T
            rel = np.linalg.inv(ref_m) @ pose_m
            rel[:3, 3] = SceneUtils.value_to_millimeters(rel[:3, 3], stage)
            return WSPose(pose=PrimUtils.matrix_to_pose(rel).tolist())
        except Exception as e:
            carb.log_verbose(f"Target pose read failed: {e}")
            return None

    # -- live solve loop ------------------------------------------------------------
    def _start_live_updates(self) -> None:
        if self._update_sub is None:
            self._update_sub = (
                omni.kit.app.get_app()
                .get_update_event_stream()
                .create_subscription_to_pop(
                    self._on_app_update, name="wandelbots.reachability_envelope.live"
                )
            )
            carb.log_info(f"{WINDOW_TITLE}: live solve loop started")
        if self._stage_event_sub is None:
            self._stage_event_sub = (
                omni.usd.get_context()
                .get_stage_event_stream()
                .create_subscription_to_pop(
                    self._on_stage_event,
                    name="wandelbots.reachability_envelope.selection",
                )
            )

    def _stop_live_updates(self) -> None:
        if self._update_sub is not None:
            self._update_sub.unsubscribe()
            self._update_sub = None
        if self._stage_event_sub is not None:
            self._stage_event_sub.unsubscribe()
            self._stage_event_sub = None
        self._unsubscribe_pose_watch()
        self._cancel_plan_check()

    def _on_stage_event(self, event) -> None:
        """Mirror a viewport pose selection back into the pose dropdown, and
        start over when the stage itself is replaced."""
        if event.type in _STAGE_REPLACED_EVENTS:
            self._on_stage_replaced()
            return
        if event.type != int(omni.usd.StageEventType.SELECTION_CHANGED):
            return
        self._adopt_current_selection()

    def _on_stage_replaced(self) -> None:
        """Clear the viewport and re-read targets from the stage now open.

        The cloud, the marker and every prim path the tool holds belong to the
        stage that was closed; left alone they stay drawn over the new scene
        and solve against prims that no longer exist.
        """
        self._with_overlay(lambda ov: ov.reset(), quiet=True)
        self._unsubscribe_pose_watch()
        self._cancel_plan_check()
        self._update_tcp_pose_fields(None)
        self._refresh_motion_groups()
        self._refresh_poses()
        self._adopt_current_selection()
        self._subscribe_pose_watch(self._selected_pose_path())

    def _adopt_current_selection(self) -> bool:
        """Take over the viewport's selection - pose prim or ghost object.

        Also runs when the window opens: a pose selected BEFORE the tool was
        opened is exactly the one the user wants to work on, and having to
        re-click it is the kind of small friction that makes the list feel
        disconnected from the scene.

        The last selected path wins (it is the one just clicked), and earlier
        entries are still tried so a multi-selection that happens to contain a
        pose is not ignored.
        """
        try:
            paths = omni.usd.get_context().get_selection().get_selected_prim_paths()
        except Exception as e:
            carb.log_verbose(f"Could not read the stage selection: {e}")
            return False
        for path in reversed(list(paths or [])):
            if self._adopt_pose_path(path):
                return True
        return False

    def _adopt_pose_path(self, path: str) -> bool:
        """Point the tool at the pose *path* belongs to; False if it is none."""
        picked = self._pose_for_selected_path(path)
        if picked is None:
            return False
        if picked != self._selected_pose_path():
            self._pose_index = self._poses.index(picked)
            self._rebuild_pose_combo()
            self._on_pose_selected()
        # A ghost object carries its own motion group, so following it keeps the
        # two dropdowns from describing different robots.
        self._adopt_motion_group_of_pose(picked)
        return True

    def _pose_for_selected_path(self, path: str) -> str | None:
        """Pose the selected path belongs to, refreshing the list for a new one.

        The stage is asked FIRST. Matching against the known list up front used
        to win here, and it returned the first entry the path happened to sit
        under - which in sorted order is the enclosing folder, not the pose
        inside it. So selecting /World/poses/group/Pose_02 landed the tool on
        /World/poses/group and the actual selection was never used.
        """
        resolved = self._resolve_pose_prim(path)
        if resolved is None:
            # Nothing on the way up declares itself, but the path may still sit
            # inside a pose the list knows (e.g. a prim the stage scan skipped).
            return self._deepest_known_pose(path)
        if resolved not in self._poses:
            self._refresh_poses()  # a pose the list has not seen yet
        return resolved if resolved in self._poses else None

    def _deepest_known_pose(self, path: str) -> str | None:
        return deepest_pose_ancestor(path, self._poses)

    def _resolve_pose_prim(self, path: str) -> str | None:
        """Walk up from *path* to the pose / ghost prim it belongs to.

        Clicking a ghost object in the viewport selects the mesh inside it, not
        the prim carrying GhostObjectAPI, so resolving only the selected prim
        would miss the selections users actually make.
        """
        stage = self._stage()
        if stage is None:
            return None

        return resolve_pose_prim_path(
            path, lambda candidate: self._is_pose_prim(stage.GetPrimAtPath(candidate))
        )

    def _adopt_motion_group_of_pose(self, pose_path: str) -> None:
        """Follow a ghost object's linked motion group into the dropdown."""
        stage = self._stage()
        prim = stage.GetPrimAtPath(pose_path) if stage else None
        if prim is None or not prim.IsValid():
            return
        try:
            if not GhostObjectUtils.is_ghost_object(prim):
                return  # a plain pose prim names no motion group
            mg_prim = GhostObjectUtils.get_linked_motion_group_to_ghost_object_prim(
                prim
            )
        except Exception as e:
            carb.log_verbose(f"Could not resolve the ghost object's motion group: {e}")
            return
        if mg_prim is None or not mg_prim.IsValid():
            return
        mg_path = mg_prim.GetPath().pathString
        if mg_path == self._selected_mg_path():
            return
        if mg_path not in self._motion_groups:
            self._refresh_motion_groups()
        if mg_path not in self._motion_groups:
            return
        self._mg_index = self._motion_groups.index(mg_path)
        self._rebuild_mg_combo()
        self._on_mg_selected()

    def _blank_joints_label(self) -> None:
        """The shown joints no longer belong to the shown TCP pose.

        Blanking beats leaving the stale values up, because the copy chip would
        otherwise hand out a joint set that does not match the copied pose.
        """
        if self._joints_label is not None:
            self._joints_label.text = _VALUE_EMPTY

    def _mark_pose_dirty(self) -> None:
        """Invalidate everything the old pose was answered with."""
        self._configs_pose_vec = None
        self._plannable = None
        self._plan_error = None
        self._planned_joints = None
        self._cancel_plan_check()
        self._blank_joints_label()
        self._push_pose_state()
        self._refresh_actions()

    # -- action buttons ---------------------------------------------------------
    def _has_fresh_solution(self) -> bool:
        """Whether a solution for the pose as it stands right now is available.

        ``_ik_configs`` outlives a pose change on purpose (the solutions stay
        usable as a seed), so the pose vector they were solved for is what
        decides - it is cleared by _mark_pose_dirty.
        """
        if not self._ik_configs or self._configs_pose_vec is None:
            return False
        if self._last_pose_vec is None:
            return False
        return pose_vectors_match(self._last_pose_vec, self._configs_pose_vec)

    def _refresh_actions(self) -> None:
        """Mirror the tool's state onto the two action buttons.

        Move is the reachability read-out: enabled exactly when the robot can
        go to the pose as it stands. Snap deliberately does NOT need a solution
        - an unreachable pose is precisely what it is for.

        Runs from the 30Hz poll, so the widget writes are skipped while nothing
        changed (each one is a UI property write plus a redraw).
        """
        if self._move_button is None or self._snap_button is None:
            return
        pending = self._snap_target is not None
        move_enabled = self._has_fresh_solution() and not self._moving and not pending
        move_text = "Placing..." if self._moving else _PLACE_LABEL
        # With a snap target waiting, Snap becomes the confirm button: the
        # offset is on screen and the pose has NOT moved yet.
        if pending:
            snap_enabled = True
            snap_text = _SNAP_APPLY_LABEL
        else:
            snap_enabled = (
                not self._snap_pending
                and not self._overlay_busy
                and not self._moving
                and self._selected_pose_path() is not None
                and self._selected_mg_prim() is not None
            )
            snap_text = "Searching..." if self._snap_pending else _SNAP_LABEL
        verdict_text, verdict_color = self._verdict()
        motion_text, motion_color = self._motion_verdict()
        # While a placement runs, its state is the only thing worth reading;
        # the blockers are all about starting one.
        lines: list[tuple[str, int]] = []
        if not self._moving and not pending:
            lines = [
                (text, NOVAColor.TEXT_SECONDARY.color) for text in self._blockers()
            ]
        lines += [(text, NOVAColor.WARNING_LIGHT.color) for text in self._warnings()]

        state = (
            move_enabled,
            move_text,
            snap_enabled,
            snap_text,
            verdict_text,
            motion_text,
            self._moving,
            pending,
            tuple(lines),
        )
        if state == self._actions_state:
            return
        self._actions_state = state
        self._move_button.enabled = move_enabled
        self._move_button.text = move_text
        self._snap_button.enabled = snap_enabled
        self._snap_button.text = snap_text
        if self._cancel_button is not None:
            # The cancel slot doubles as "keep the pose" while a snap is
            # pending; a placement itself is a single state write and has
            # nothing to cancel.
            self._cancel_button.visible = pending
            self._cancel_button.enabled = pending
            self._cancel_button.text = _SNAP_DISCARD_LABEL
        self._set_chip(self._verdict_label, verdict_text, verdict_color)
        self._set_chip(self._motion_label, motion_text, motion_color)
        self._refresh_progress(move_text)
        self._rebuild_readiness(lines)

    @staticmethod
    def _set_chip(label, text: str, color: int) -> None:
        if label is None:
            return
        label.text = text
        label.set_style({"color": color, "font_size": FONT_SIZE_SM})

    def _refresh_progress(self, move_text: str) -> None:
        """Only a placement has progress worth showing; the sweep is local."""
        if self._progress is None:
            return
        if self._moving:
            self._progress.show(0.5)
            self._progress.set_hint(move_text)
        else:
            self._progress.hide()

    def _on_app_update(self, _event) -> None:
        """Keep the buttons honest and run the timers the watcher cannot.

        The pose itself is followed by the USD change watcher, not from here -
        a poll can only answer as fast as it ticks, and the verdict has to keep
        up with a drag. What is left here is the button state, the settle timer
        the plan check waits on, and the watchdogs that release a guard a hung
        request would otherwise hold forever.
        """
        now = time.monotonic()
        if now - self._last_poll < _POLL_S:
            return
        self._last_poll = now

        self._refresh_actions()
        self._release_stuck_guards(now)
        # Re-arms a solve the error backoff or a missing description deferred.
        self._start_pending_solve()
        self._maybe_check_plannable(now)

    def _release_stuck_guards(self, now: float) -> None:
        if self._solving and now - self._solving_since > _SOLVE_WATCHDOG_S:
            carb.log_warn("IK solve watchdog fired - releasing the guard")
            self._solving = False
            self._start_pending_solve()
        if self._planning and now - self._planning_since > _PLAN_WATCHDOG_S:
            carb.log_warn("Plan check watchdog fired - releasing the guard")
            self._planning = False

    # -- pose watching ----------------------------------------------------------------
    def _subscribe_pose_watch(self, path: str | None) -> None:
        """Follow the pose prim and its ancestors through the USD watcher.

        The ancestors matter because moving a parent moves the pose without
        touching the pose prim's own xformOps, and the tool would keep showing
        an answer for where the pose used to be.
        """
        if path == self._watched_pose_path and self._pose_watch_subs:
            return
        self._unsubscribe_pose_watch()
        stage = self._stage()
        prim = stage.GetPrimAtPath(path) if (stage and path) else None
        if prim is None or not prim.IsValid():
            return
        while prim and prim.IsValid() and not prim.IsPseudoRoot():
            try:
                self._pose_watch_subs.append(
                    omni.usd.get_watcher().subscribe_to_change_info_path(
                        prim.GetPath(), self._on_pose_attribute_changed
                    )
                )
            except Exception as e:
                carb.log_warn(f"Could not watch {prim.GetPath()}: {e}")
            prim = prim.GetParent()
        self._watched_pose_path = path
        self._on_pose_attribute_changed()

    def _unsubscribe_pose_watch(self) -> None:
        for subscription in self._pose_watch_subs:
            try:
                subscription.unsubscribe()
            except Exception:
                pass
        self._pose_watch_subs = []
        self._watched_pose_path = None

    def _on_pose_attribute_changed(self, path: Sdf.Path = None) -> None:
        """Read the moved pose and keep the solver pointed at the newest one."""
        if path is not None and not is_pose_xform_op(path):
            return
        pose = self._current_target_pose()
        if pose is None:
            if self._pose_readable:
                self._pose_readable = False
                self._update_tcp_pose_fields(None)
                self._set_status("Cannot read the target pose - check selections.")
            return
        self._pose_readable = True
        # The label is what the copy chip hands out, so it must never lag
        # behind the marker.
        self._update_tcp_pose_fields(pose)

        vector = tuple(round(float(v), 4) for v in pose.pose)
        if pose_vectors_match(vector, self._last_pose_vec):
            return
        self._last_pose_vec = vector
        self._pose_still_since = time.monotonic()
        self._mark_pose_dirty()
        self._pending_pose = pose
        self._start_pending_solve()

    def _request_solve(self) -> None:
        """Re-solve the pose where it stands - the pose did not move, but what
        it is solved against (motion group, TCP) did."""
        self._pending_pose = self._current_target_pose()
        self._start_pending_solve()

    def _start_pending_solve(self) -> None:
        """Solve the newest pose as soon as nothing else is in flight.

        Deliberately no settle window: the verdict has to follow the pose while
        it is being dragged, not appear once it is let go. One request at a time
        and the newest pose always wins, so a drag produces a stream of verdicts
        at whatever rate NOVA answers instead of a queue that outlives the drag.
        The plan check is the one that still waits for the pose to come to rest -
        it is a planning request, not an IK call.
        """
        if self._solving or self._pending_pose is None:
            return
        if time.monotonic() < self._backoff_until:
            return
        pose = self._pending_pose
        self._pending_pose = None
        self._solve_ik(pose)

    # -- IK ---------------------------------------------------------------------------
    def _solve_ik(self, pose_ws) -> None:
        stream = self._stream_config()
        if stream is None or pose_ws is None or self._description is None:
            # Not ready (description still loading?) - re-arm with a small
            # backoff instead of dropping this pose change on the floor.
            self._set_status("Waiting for the motion group description...")
            self._backoff_until = time.monotonic() + 1.0
            self._pending_pose = pose_ws
            self._mark_pose_dirty()
            return
        description = self._description
        tcp_name = self._selected_tcp()
        self._solving = True
        self._solving_since = time.monotonic()

        async def _solve():
            try:
                tcp_ws = None
                if tcp_name and description.tcps:
                    tcp_off = description.tcps.get(tcp_name)
                    if tcp_off is not None:
                        tcp_ws = WSPose(
                            pose=[*tcp_off.pose.position, *tcp_off.pose.orientation]
                        )
                # Seed priority: last solved solution (keeps the branch stable
                # AND skips the extra current-state fetch inside the helper),
                # else the last moved-to joints.
                seed = self._seed_joints or self._last_moved_joints
                # Hard timeout: a hung request must never wedge the live loop
                # for longer than the watchdog window.
                result = await asyncio.wait_for(
                    fetch_joint_configs_for_pose(
                        stream_config=stream,
                        pose=pose_ws,
                        tcp_offset=tcp_ws,
                        preferred_joint_values=seed,
                        description=description,
                        diagnostics=False,  # unreachable is a normal state here
                    ),
                    timeout=8.0,
                )
                self._ik_configs = list(result.joint_configs) if result else []
                self._configs_pose_vec = tuple(round(float(v), 4) for v in pose_ws.pose)
                solution = self._selected_solution()
                if solution is not None:
                    self._seed_joints = solution
                self._update_joints_label()
                # No reachable/unreachable message: the Move button being
                # enabled (or not) is that statement, and the joint values are
                # right above it. The status line stays for actual events - so a
                # landed solve retires whatever it said, unless a placement is
                # reporting through it right now.
                if not self._moving:
                    self._set_status("")
                self._refresh_actions()
                if self._move_after_solve:
                    self._move_after_solve = False
                    if self._ik_configs:
                        run_coroutine(self._place_robot_async(user_initiated=False))
            except Exception as e:
                carb.log_warn(f"IK solve failed: {e}")
                self._set_status(f"IK failed: {e}")
                self._move_after_solve = False
                # Pause the live loop briefly, then retry the same pose - a
                # transient backend error must not stall updates permanently.
                self._backoff_until = time.monotonic() + _ERROR_BACKOFF_S
                self._pending_pose = pose_ws
                self._mark_pose_dirty()
            finally:
                self._solving = False
                self._push_pose_state()
                # A drag that moved on while this was in flight left its newest
                # pose behind; answer that one next instead of going idle.
                self._start_pending_solve()

        run_coroutine(_solve())

    def _push_pose_state(self) -> None:
        """Hand the overlay the verdict, so the marker never has to guess it."""
        if self._has_fresh_solution():
            reachable = True
        elif self._solving or self._configs_pose_vec is None:
            reachable = None  # not answered yet - the marker greys out
        else:
            reachable = False
        self._with_overlay(
            lambda ov: ov.set_pose_state(reachable, self._plannable), quiet=True
        )

    # -- plannability -----------------------------------------------------------------
    def _maybe_check_plannable(self, now: float) -> None:
        """Ask NOVA for a plan once the pose has come to rest.

        Deliberately not on every pose change: a planning request is orders of
        magnitude more expensive than an inverse-kinematics solve, and an answer
        for a pose the user is still dragging is worthless by the time it lands.
        """
        if self._planning or self._plannable is not None:
            return
        if not self._has_fresh_solution() or self._moving:
            return
        if now - self._pose_still_since < _PLAN_SETTLE_S:
            return
        solution = self._selected_solution()
        if solution is None:
            return
        stream = self._stream_config()
        if stream is None:
            return
        self._planning = True
        self._planning_since = now
        self._planned_joints = list(solution)
        self._plan_task = run_coroutine(self._check_plannable_async(stream, solution))

    async def _check_plannable_async(self, stream, target_joints) -> None:
        try:
            result = await check_plannable_from_current_state(
                stream.get_api_configuration(),
                cell=stream.cell,
                controller=stream.controller,
                motion_group=stream.motion_group,
                target_joint_position=list(target_joints),
                tcp_name=self._selected_tcp(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            carb.log_warn(f"Plan check failed: {e}")
            self._plannable = False
            self._plan_error = str(e)
        else:
            # A pose change while this was in flight cleared the target; the
            # answer belongs to a pose nobody is looking at any more.
            if self._planned_joints != list(target_joints):
                return
            self._plannable = isinstance(result, PlanSuccess)
            self._plan_error = None if self._plannable else result.error
        finally:
            self._planning = False
            self._plan_task = None
        self._push_pose_state()
        self._refresh_actions()

    def _cancel_plan_check(self) -> None:
        if self._plan_task is not None and not self._plan_task.done():
            self._plan_task.cancel()
        self._plan_task = None
        self._planning = False

    def _selected_solution(self) -> list[float] | None:
        """Solution nearest the last moved-to joints (branch stability)."""
        if not self._ik_configs:
            return None
        ref = self._last_moved_joints
        if ref is None:
            return list(self._ik_configs[0])
        best, best_d = 0, None
        for i, sol in enumerate(self._ik_configs):
            d = sum((a - b) ** 2 for a, b in zip(sol, ref))
            if best_d is None or d < best_d:
                best, best_d = i, d
        return list(self._ik_configs[best])

    # -- envelope --------------------------------------------------------------------
    def _with_overlay(self, fn, quiet: bool = False) -> None:
        ov = self._overlay()
        if ov is None:
            if not quiet:
                self._set_status("No envelope overlay registered.")
            return
        fn(ov)

    def _on_enable(self, model) -> None:
        """The checkbox shows and hides the cloud, not the whole overlay - the
        reachability marker stays on the pose either way."""
        self._push_envelope_visibility()

    def _envelope_wanted(self) -> bool:
        return envelope_is_wanted(
            self._enable_model.as_bool, self._density_model.as_float
        )

    def _push_envelope_visibility(self) -> None:
        self._with_overlay(lambda ov: ov.set_cloud_visible(self._envelope_wanted()))

    def _on_density(self, model) -> None:
        # Size first, then visibility: coming back up from zero has to compute
        # at the density the slider now shows, not at the one it left behind.
        if model.as_float > _DENSITY_OFF:
            self._with_overlay(lambda ov: ov.set_density(model.as_float), quiet=True)
        self._push_envelope_visibility()

    def _on_opacity(self, model) -> None:
        self._with_overlay(lambda ov: ov.set_opacity(model.as_float), quiet=True)

    def _on_snap(self) -> None:
        """First click searches and reports the offset, second click applies it.

        The search used to move the pose the moment it landed, which is a
        surprise when the offset is large - and the user has no way to see how
        far the pose was about to travel. So the offset is shown first and the
        pose stays put until it is applied.
        """
        if self._snap_target is not None:
            self._apply_snap()
            return
        ov = self._overlay()
        if ov is None:
            self._set_status("No envelope overlay registered.")
            self._refresh_actions()
            return
        self._snap_pending = True
        self._set_status("Searching for the nearest reachable point...")
        self._refresh_actions()
        run_coroutine(self._search_snap_async(ov))

    async def _search_snap_async(self, ov) -> None:
        try:
            target = await ov.find_snap_target()
        except Exception as e:
            carb.log_warn(f"Snap search failed: {e}")
            self._snap_pending = False
            self._set_status(f"Snap search failed: {e}")
            self._refresh_actions()
            return
        self._snap_pending = False
        if target is None:
            self._set_status(
                "No reachable point found around this pose at its current orientation."
            )
        elif target.already_reachable:
            self._set_status("The pose is already reachable - nothing to snap.")
        else:
            # Held, not applied: this IS the prompt.
            self._snap_target = target
            self._set_status(
                f"Snap would move the pose {target.describe()}. "
                f"Apply it, or keep the pose where it is."
            )
        self._set_snap_offset(target)
        self._refresh_actions()

    def _apply_snap(self) -> None:
        target, self._snap_target = self._snap_target, None
        ov = self._overlay()
        if target is None or ov is None:
            self._refresh_actions()
            return
        if ov.apply_snap_target(target):
            self._set_status(f"Pose snapped {target.describe()}.")
            # The pose moved, so the solutions no longer belong to it.
            self._mark_pose_dirty()
        else:
            self._set_status("Could not move the pose - see log.")
        self._set_snap_offset(None)
        self._refresh_actions()

    def _on_discard_snap(self) -> None:
        """Drop a pending snap offset without touching the pose."""
        if self._snap_target is None:
            return
        self._snap_target = None
        self._set_status("Snap discarded - the pose was not moved.")
        self._set_snap_offset(None)
        self._refresh_actions()

    def _set_snap_offset(self, target) -> None:
        """Mirror the pending snap offset into its own row, so it stays readable."""
        if self._snap_offset_label is None:
            return
        self._snap_offset_label.text = (
            _VALUE_EMPTY if target is None else target.describe()
        )

    # -- place robot -------------------------------------------------------------------
    async def _place_robot_async(self, user_initiated: bool = True) -> None:
        """Place the robot at the solved joint values.

        ``user_initiated`` is False for the automatic retry after a re-solve,
        which must not re-arm another retry.
        """
        if self._moving:
            return  # already in flight (double click, or a queued re-solve)
        joints = self._selected_solution()
        if joints is None:
            self._set_status("No IK solution to place the robot at.")
            return
        # Never place at a solution solved for a different pose (e.g. the pose
        # was snapped/dragged and the re-solve has not landed yet). No pose
        # vector at all means exactly that too: _mark_pose_dirty clears it and
        # leaves the solutions in place as the next solve's seed.
        current = self._current_target_pose()
        stale = self._configs_pose_vec is None or (
            current is not None
            and not pose_vectors_match(
                [round(float(v), 4) for v in current.pose], self._configs_pose_vec
            )
        )
        if stale:
            # One re-solve, then place with what the solve returned. Re-arming
            # on every stale check turns a pose that keeps changing into an
            # endless solve/place ping-pong that never moves the robot.
            if not user_initiated:
                self._set_status(
                    "The pose kept moving while it was being solved - hold it "
                    "still and press Place robot again."
                )
                return
            self._set_status("Pose changed - re-solving, then placing...")
            self._move_after_solve = True
            self._mark_pose_dirty()
            return
        stream = self._stream_config()
        if stream is None:
            self._set_status("Could not resolve the selected motion group.")
            return
        self._moving = True
        self._set_status("Placing the robot...")
        self._set_residual(None)
        try:
            result = await asyncio.wait_for(
                place_motion_group_at_joints(
                    stream_config=stream,
                    joint_position=joints,
                ),
                timeout=_PLACE_TIMEOUT_S,
            )
            if result.ok:
                self._last_moved_joints = list(joints)
                self._seed_joints = list(joints)
            self._set_status(result.message)
            self._set_residual(result)
        except asyncio.TimeoutError:
            carb.log_warn("Placing the robot timed out - releasing the guard")
            self._set_status(
                f"Placing the robot timed out after {int(_PLACE_TIMEOUT_S)}s - "
                "check the controller."
            )
        except asyncio.CancelledError:
            self._set_status("Placement cancelled.")
            raise
        except Exception as e:
            carb.log_warn(f"Placing the robot failed: {e}")
            self._set_status(f"Placing the robot failed: {e}")
        finally:
            self._moving = False
            self._refresh_actions()

    def _set_residual(self, result: PlacementResult | None) -> None:
        """Keep the last placement's outcome visible after the status moves on."""
        if self._residual_label is None:
            return
        if result is None:
            self._residual_label.text = _VALUE_EMPTY
            return
        self._residual_label.text = result.summary

    # -- status --------------------------------------------------------------------------
    def _on_status(self, status: dict) -> None:
        self._overlay_busy = bool(status.get("busy"))
        if not self._overlay_busy:
            self._update_density_label(status)
        self._refresh_actions()

    def _update_density_label(self, status: dict) -> None:
        if self._density_label is None:
            return
        if self._enable_model.as_bool and not self._envelope_wanted():
            # An empty viewport with the box ticked needs a reason on screen.
            self._density_label.text = "none - the density slider is at zero"
            return
        voxels = int(status.get("voxels") or 0)
        if not voxels:
            self._density_label.text = _VALUE_EMPTY
            return
        self._density_label.text = f"{voxels} at {float(status['voxel_mm']):.0f} mm"

    def _set_status(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.text = text


class ReachabilityEnvelopeWindowSubscription:
    def __init__(self, window: ReachabilityEnvelopeWindow, menu_subscriptions: list):
        self.window = window
        self.menu_subscriptions = menu_subscriptions

    def __del__(self) -> None:
        if self.window:
            self.window.destroy()
            self.window = None
        if self.menu_subscriptions:
            omni.kit.menu.utils.remove_menu_items(
                self.menu_subscriptions, WINDOW_MENU_ROOT
            )


def register_reachability_envelope_window():
    win = ReachabilityEnvelopeWindow()

    def toggle_visibility():
        win.window.visible = not win.window.visible

    def _is_visible(
        ref: Callable[[], ReachabilityEnvelopeWindow | None] = weakref.ref(win),
    ):
        return ref().window.visible if ref() else False

    ext_id = EXTENSION_ID
    name = WINDOW_TITLE
    action_unique = f"{ext_id}_{name}_toggle"
    registry = omni.kit.actions.core.get_action_registry()
    registry.deregister_action(ext_id, action_unique)
    registry.register_action(
        ext_id, action_unique, toggle_visibility, display_name=name, tag="MenuItem"
    )
    carb.log_info(f"Registered {WINDOW_TITLE} window")
    return ReachabilityEnvelopeWindowSubscription(
        win,
        omni.kit.menu.utils.add_menu_items(
            [
                omni.kit.menu.utils.MenuItemDescription(
                    name=EXTENSION_WINDOW_MENU_ROOT,
                    sub_menu=[
                        omni.kit.menu.utils.MenuItemDescription(
                            name=name,
                            onclick_action=(ext_id, action_unique),
                            ticked_fn=_is_visible,
                        )
                    ],
                )
            ],
            WINDOW_MENU_ROOT,
        ),
    )
