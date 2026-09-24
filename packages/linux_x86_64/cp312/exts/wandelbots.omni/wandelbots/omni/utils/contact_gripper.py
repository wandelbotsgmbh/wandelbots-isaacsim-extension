import fnmatch
import time
from collections.abc import Callable
from dataclasses import dataclass

import carb
import omni.kit.app
import omni.timeline
import omni.usd
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

_RECOVERY_KEY = "wandelbotsGripperRecovery"
_RELEASE_GRACE_S = 0.2
_BOUNDS_PURPOSES = [
    UsdGeom.Tokens.default_,
    UsdGeom.Tokens.render,
    UsdGeom.Tokens.proxy,
]


def _current_time_code(stage: Usd.Stage) -> Usd.TimeCode:
    """Time code the viewport shows right now.

    Physics writes default values, animation writes time samples. Reading at
    the current time covers both, reading at Default would test an animated
    prim at its rest pose.
    """
    timeline = omni.timeline.get_timeline_interface()
    if timeline is None:
        return Usd.TimeCode.Default()
    return Usd.TimeCode(timeline.get_current_time() * stage.GetTimeCodesPerSecond())


@dataclass
class _PendingRelease:
    deadline: float
    restore_kinematic_enabled: bool | None


class ContactGripperModel:
    """Contact-gripper logic decoupled from any OmniGraph node.

    Call attach() to grab the first candidate prim inside the helper volume,
    or every candidate with attach_all, release() to let go of all of them,
    and restore_all() to reset all touched prims (e.g. on simulation stop).
    Register on_attached / on_released callbacks to react to state changes
    from any consumer (UI, tests, ...).
    """

    def __init__(self) -> None:
        # held prims in attach order, each with its offset to the helper
        self.attached_offsets: dict[str, Gf.Matrix4d] = {}
        self.restore_kinematic_by_path: dict[str, bool | None] = {}

        # released prims waiting out the grace period before their rigid
        # body state is restored
        self.pending_releases: dict[str, _PendingRelease] = {}

        # per-prim recovery data
        self.original_local_transforms: dict[str, Gf.Matrix4d] = {}
        self.original_xform_states: dict[str, dict] = {}
        self.original_kinematic_enabled: dict[str, bool | None] = {}
        self.touched_prim_paths: set[str] = set()

        # configuration kept between calls
        self.helper_prim_path: str = ""

        # edge-detection flag used by the OGN controller
        self.prev_stick: bool = False

        # optional callbacks fired on state transitions
        self.on_attached: Callable[[str], None] | None = None
        self.on_released: Callable[[str], None] | None = None

        self._timeline_sub = None
        self._update_sub = None
        self._subscribe_timeline_stop()

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def is_attached(self) -> bool:
        return bool(self.attached_offsets)

    @property
    def attached_prim_paths(self) -> list[str]:
        return list(self.attached_offsets)

    @property
    def attached_prim_path(self) -> str:
        """Path of the most recently attached prim, empty when nothing is held."""
        if not self.attached_offsets:
            return ""
        return next(reversed(self.attached_offsets))

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def attach(
        self,
        helper_path: str,
        candidate_paths: list[str],
        exclude_paths: list[str],
        attach_all: bool = False,
    ) -> bool:
        """Attach candidate prims inside the helper volume.

        Attaches the first overlapping candidate, or with attach_all every
        overlapping candidate that is not held yet. Returns True if at least
        one prim was attached. Fires on_attached once per prim.
        """
        if self.attached_offsets and not attach_all:
            return False

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return False

        self._update_pending_releases(stage)

        helper_prim = stage.GetPrimAtPath(helper_path)
        if not helper_prim.IsValid():
            carb.log_error(
                f"Contact Gripper: helper prim does not exist: {helper_path}"
            )
            return False

        self.helper_prim_path = helper_path

        candidate_prims = self._find_candidate_prims(
            stage,
            helper_prim,
            candidate_paths,
            exclude_paths,
            limit=None if attach_all else 1,
        )
        for candidate_prim in candidate_prims:
            self._do_attach(stage, helper_prim, candidate_prim)
            if self.on_attached:
                self.on_attached(candidate_prim.GetPath().pathString)
        return bool(candidate_prims)

    def release(self) -> bool:
        """Release every attached prim.

        Returns True if at least one prim was released. Fires on_released once
        per prim.
        """
        if not self.attached_offsets:
            return False

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return False

        self._update_pending_releases(stage)

        helper_prim = stage.GetPrimAtPath(self.helper_prim_path)
        if not helper_prim.IsValid():
            return False

        released_paths = []
        for prim_path, offset in list(self.attached_offsets.items()):
            attached_prim = stage.GetPrimAtPath(prim_path)
            if not attached_prim.IsValid():
                carb.log_error(
                    f"Contact Gripper: attached prim no longer exists: {prim_path}"
                )
                self._forget_prim(prim_path)
                continue
            self._snap_attached_prim(helper_prim, attached_prim, offset)
            self._release_prim(stage, prim_path)
            released_paths.append(prim_path)

        for prim_path in released_paths:
            if self.on_released:
                self.on_released(prim_path)
        return bool(released_paths)

    def restore_all(self) -> None:
        """Restore all touched prims to their original state and clear all held state."""
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        self._restore_all_touched_objects(stage)

    def destroy(self) -> None:
        """Clean up event subscriptions."""
        self._stop_frame_updates()
        self._timeline_sub = None

    # -------------------------------------------------------------------------
    # Timeline / frame subscriptions
    # -------------------------------------------------------------------------

    def _subscribe_timeline_stop(self) -> None:
        timeline = omni.timeline.get_timeline_interface()
        if timeline is None:
            return
        self._timeline_sub = (
            timeline.get_timeline_event_stream().create_subscription_to_pop(
                self._on_timeline_event
            )
        )

    def _on_timeline_event(self, event) -> None:
        if event.type != int(omni.timeline.TimelineEventType.STOP):
            return
        self._stop_frame_updates()
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        self._restore_all_touched_objects(stage)

    def _start_frame_updates(self) -> None:
        if self._update_sub is not None:
            return
        app = omni.kit.app.get_app()
        if app is None:
            return
        self._update_sub = app.get_update_event_stream().create_subscription_to_pop(
            self._on_frame_update
        )

    def _stop_frame_updates(self) -> None:
        self._update_sub = None

    def _on_frame_update(self, _event) -> None:
        timeline = omni.timeline.get_timeline_interface()
        if timeline is not None and not timeline.is_playing():
            return

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return

        self._update_pending_releases(stage)

        if not self.attached_offsets or not self.helper_prim_path:
            if not self.pending_releases:
                self._stop_frame_updates()
            return

        helper_prim = stage.GetPrimAtPath(self.helper_prim_path)
        if not helper_prim.IsValid():
            return

        for prim_path, offset in list(self.attached_offsets.items()):
            attached_prim = stage.GetPrimAtPath(prim_path)
            if not attached_prim.IsValid():
                carb.log_warn(
                    f"Contact Gripper: attached prim no longer exists: {prim_path}"
                )
                self._forget_prim(prim_path)
                continue
            self._snap_attached_prim(helper_prim, attached_prim, offset)

    # -------------------------------------------------------------------------
    # Attachment helpers
    # -------------------------------------------------------------------------

    def _do_attach(self, stage: Usd.Stage, helper_prim, candidate_prim) -> None:
        candidate_path = candidate_prim.GetPath().pathString
        current_local = self._get_local_transformation(candidate_prim)
        recovery_metadata = self._get_recovery_metadata(candidate_prim)

        if recovery_metadata is None:
            if candidate_path not in self.original_xform_states:
                self.original_xform_states[candidate_path] = self._capture_xform_state(
                    candidate_prim
                )
            if (
                candidate_path not in self.original_local_transforms
                and current_local is not None
            ):
                self.original_local_transforms[candidate_path] = Gf.Matrix4d(
                    current_local
                )
            self._ensure_recovery_metadata(candidate_prim, current_local)
        else:
            original_local = recovery_metadata.get("original_local_transform")
            if (
                candidate_path not in self.original_local_transforms
                and original_local is not None
            ):
                self.original_local_transforms[candidate_path] = Gf.Matrix4d(
                    original_local
                )

        self._increment_active_holders(candidate_prim)
        self._cancel_pending_release(candidate_path)
        offset = self._compute_attach_offset(helper_prim, candidate_prim)
        self.attached_offsets[candidate_path] = offset
        self._prepare_transform_control(candidate_prim)
        self.touched_prim_paths.add(candidate_path)

        if recovery_metadata is not None and "kinematic_enabled" in recovery_metadata:
            restore_kinematic_enabled = bool(recovery_metadata["kinematic_enabled"])
            self._set_kinematic_enabled(candidate_prim, True)
        else:
            restore_kinematic_enabled = self._set_kinematic_while_held(candidate_prim)
        self.restore_kinematic_by_path[candidate_path] = restore_kinematic_enabled

        if candidate_path not in self.original_kinematic_enabled:
            self.original_kinematic_enabled[candidate_path] = restore_kinematic_enabled

        self._snap_attached_prim(helper_prim, candidate_prim, offset)
        self._start_frame_updates()

    def _release_prim(self, stage: Usd.Stage, prim_path: str) -> None:
        restore_kinematic_enabled = self.restore_kinematic_by_path.get(prim_path)
        self._forget_prim(prim_path)

        released_prim = stage.GetPrimAtPath(prim_path)
        if not released_prim.IsValid():
            return
        remaining_holders = self._decrement_active_holders(released_prim)
        if remaining_holders <= 0:
            self._schedule_pending_release(released_prim, restore_kinematic_enabled)

    def _forget_prim(self, prim_path: str) -> None:
        self.attached_offsets.pop(prim_path, None)
        self.restore_kinematic_by_path.pop(prim_path, None)

    def _restore_all_touched_objects(self, stage: Usd.Stage) -> None:
        self._stop_frame_updates()

        self.touched_prim_paths.update(self.attached_offsets)

        for prim_path in list(self.touched_prim_paths):
            prim = stage.GetPrimAtPath(prim_path)
            if not prim.IsValid():
                continue
            self._restore_original_transform_state(
                prim,
                self.original_xform_states.get(prim_path),
                self.original_local_transforms.get(prim_path),
            )
            self._restore_original_physics_state(
                prim, self._get_recovery_metadata(prim)
            )
            self._clear_recovery_metadata(prim)

        self.attached_offsets.clear()
        self.restore_kinematic_by_path.clear()
        self.pending_releases.clear()
        self.touched_prim_paths.clear()
        self.original_local_transforms.clear()
        self.original_xform_states.clear()
        self.original_kinematic_enabled.clear()

    # -------------------------------------------------------------------------
    # Pending release helpers
    # -------------------------------------------------------------------------

    def _schedule_pending_release(
        self,
        prim,
        restore_kinematic_enabled: bool | None,
    ) -> None:
        if prim is None or not prim.IsValid():
            return
        self.pending_releases[prim.GetPath().pathString] = _PendingRelease(
            deadline=time.monotonic() + _RELEASE_GRACE_S,
            restore_kinematic_enabled=restore_kinematic_enabled,
        )

    def _cancel_pending_release(self, prim_path: str) -> None:
        self.pending_releases.pop(prim_path, None)

    def _update_pending_releases(self, stage: Usd.Stage) -> None:
        now = time.monotonic()
        for prim_path, pending in list(self.pending_releases.items()):
            prim = stage.GetPrimAtPath(prim_path)
            if not prim.IsValid():
                self._cancel_pending_release(prim_path)
                continue

            metadata = self._get_recovery_metadata(prim)
            active_holders = int(metadata.get("active_holders", 0)) if metadata else 0
            if active_holders > 0:
                self._cancel_pending_release(prim_path)
                continue

            if now < pending.deadline:
                continue

            self._reset_rigid_body_after_release(
                prim, pending.restore_kinematic_enabled
            )
            self._cancel_pending_release(prim_path)

    # -------------------------------------------------------------------------
    # Candidate selection
    # -------------------------------------------------------------------------

    def _find_candidate_prims(
        self,
        stage: Usd.Stage,
        helper_prim,
        candidate_paths: list[str],
        exclude_paths: list[str],
        limit: int | None,
    ) -> list:
        # One cache for the whole scan, instead of a new one per prim, which
        # caches nothing.
        bbox_cache = self._make_bbox_cache(stage)

        helper_bounds = self._compute_world_aligned_bounds(helper_prim, bbox_cache)
        if helper_bounds.IsEmpty():
            carb.log_error(
                "Contact Gripper: helper prim has no world bounds. Use a helper prim with "
                f"geometry or bounded children: {helper_prim.GetPath().pathString}"
            )
            return []

        helper_path = helper_prim.GetPath()
        exclude_patterns = self._normalize_patterns(exclude_paths)
        candidate_patterns = self._normalize_patterns(candidate_paths)
        found = []

        # A prim's bound covers its whole subtree, so if it does not touch the
        # helper, nothing inside it can either and the scan skips it. Prims are
        # still visited in the same order, so the same ones get returned. A
        # subtree with no bound of its own, such as an empty Scope, is never
        # skipped.
        it = iter(Usd.PrimRange.Stage(stage))
        for candidate_prim in it:
            candidate_path = candidate_prim.GetPath()

            # Ancestors of the helper are never candidates, and their bounds
            # enclose the whole branch (computing /World's bound means unioning
            # the entire stage), so reject them before touching bounds.
            if helper_path.HasPrefix(candidate_path):
                continue
            # The helper's own subtree is excluded wholesale.
            if candidate_path.HasPrefix(helper_path):
                it.PruneChildren()
                continue
            # A held prim already follows the helper, and so does everything
            # below it, so none of that may be attached a second time.
            if candidate_path.pathString in self.attached_offsets:
                it.PruneChildren()
                continue

            # An excluded prim takes everything below it along.
            if exclude_patterns and self._matches_patterns(
                candidate_prim, exclude_patterns
            ):
                it.PruneChildren()
                continue

            # An ancestor of a held prim would drag the held prim, and every
            # sibling still lying elsewhere, along a second time. Its other
            # children stay candidates, so the subtree is not pruned.
            if self._holds_descendant_of(candidate_path):
                continue

            subtree_bounds = self._compute_world_aligned_bounds(
                candidate_prim, bbox_cache
            )
            if not subtree_bounds.IsEmpty() and not self._ranges_intersect(
                helper_bounds, subtree_bounds
            ):
                it.PruneChildren()
                continue

            if candidate_patterns and not self._matches_patterns(
                candidate_prim, candidate_patterns
            ):
                continue
            if not self._is_attachable_candidate(
                helper_path, candidate_prim, helper_bounds, bbox_cache
            ):
                continue

            found.append(candidate_prim)
            if limit is not None and len(found) >= limit:
                break
            # Descendants follow their parent already.
            it.PruneChildren()

        return found

    def _holds_descendant_of(self, prim_path: Sdf.Path) -> bool:
        return any(
            Sdf.Path(held_path).HasPrefix(prim_path)
            for held_path in self.attached_offsets
        )

    @staticmethod
    def _normalize_patterns(filters: list[str]) -> list[str]:
        """Strip and drop empty patterns once, instead of per candidate prim."""
        return [p for p in (f.strip() for f in filters or []) if p]

    @staticmethod
    def _matches_patterns(candidate_prim, normalized_patterns: list[str]) -> bool:
        candidate_path = candidate_prim.GetPath().pathString
        candidate_path_without_root = candidate_path.lstrip("/")

        for normalized in normalized_patterns:
            if (
                candidate_path == normalized
                or candidate_path_without_root == normalized
                or fnmatch.fnmatchcase(candidate_path, normalized)
                or fnmatch.fnmatchcase(candidate_path_without_root, normalized)
                or fnmatch.fnmatchcase(candidate_path, f"*/{normalized}")
                or fnmatch.fnmatchcase(candidate_path_without_root, f"*/{normalized}")
            ):
                return True

        return False

    @staticmethod
    def _is_attachable_candidate(
        helper_path, candidate_prim, helper_bounds, bbox_cache
    ) -> bool:
        if not candidate_prim.IsValid():
            return False
        if not candidate_prim.IsActive():
            return False
        if candidate_prim.IsInstanceProxy():
            return False

        candidate_path = candidate_prim.GetPath()
        if candidate_path == helper_path:
            return False
        if candidate_path.HasPrefix(helper_path) or helper_path.HasPrefix(
            candidate_path
        ):
            return False

        if not UsdGeom.Xformable(candidate_prim) or not UsdGeom.Imageable(
            candidate_prim
        ):
            return False

        return ContactGripperModel._geometry_touches(
            candidate_prim, helper_bounds, bbox_cache
        )

    @staticmethod
    def _geometry_touches(prim, helper_bounds, bbox_cache) -> bool:
        """True when a geometry prim at or below the prim overlaps the helper.

        A group's bound is the hull of everything below it, which can span the
        helper with no geometry anywhere near it, so the hull alone does not
        count. Hidden geometry below the prim does not count either; only the
        prim itself may be hidden, so a hidden part named by a filter is still
        found. Instance proxies are visited so an instanceable asset can be
        attached by its root.
        """
        it = iter(Usd.PrimRange(prim, Usd.TraverseInstanceProxies()))
        for descendant in it:
            if descendant != prim and ContactGripperModel._is_invisible(
                descendant, bbox_cache.GetTime()
            ):
                it.PruneChildren()
                continue
            bounds = ContactGripperModel._compute_world_aligned_bounds(
                descendant, bbox_cache
            )
            if bounds.IsEmpty() or not ContactGripperModel._ranges_intersect(
                helper_bounds, bounds
            ):
                it.PruneChildren()
                continue
            if UsdGeom.Boundable(descendant):
                return True
        return False

    @staticmethod
    def _is_invisible(prim, time_code: Usd.TimeCode) -> bool:
        imageable = UsdGeom.Imageable(prim)
        if not imageable:
            return False
        return imageable.ComputeVisibility(time_code) == UsdGeom.Tokens.invisible

    @staticmethod
    def _make_bbox_cache(stage: Usd.Stage) -> UsdGeom.BBoxCache:
        # Extents hints stay off. A model prim reports its authored extentsHint
        # as its bound, and an out-of-date hint can be smaller than the
        # geometry below it, so the scan would skip prims it should have found.
        # Visibility is ignored so that a hidden part is still found and a
        # hidden sensor volume still has a bound.
        return UsdGeom.BBoxCache(
            _current_time_code(stage),
            includedPurposes=_BOUNDS_PURPOSES,
            useExtentsHint=False,
            ignoreVisibility=True,
        )

    @staticmethod
    def _compute_world_aligned_bounds(prim, bbox_cache) -> Gf.Range3d:
        return bbox_cache.ComputeWorldBound(prim).ComputeAlignedBox()

    @staticmethod
    def _ranges_intersect(a: Gf.Range3d, b: Gf.Range3d) -> bool:
        a_min, a_max = a.GetMin(), a.GetMax()
        b_min, b_max = b.GetMin(), b.GetMax()
        return (
            a_min[0] <= b_max[0]
            and a_max[0] >= b_min[0]
            and a_min[1] <= b_max[1]
            and a_max[1] >= b_min[1]
            and a_min[2] <= b_max[2]
            and a_max[2] >= b_min[2]
        )

    # -------------------------------------------------------------------------
    # Transform helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _compute_attach_offset(helper_prim, attached_prim) -> Gf.Matrix4d:
        time_code = _current_time_code(helper_prim.GetStage())
        helper_world = omni.usd.get_world_transform_matrix(helper_prim, time_code)
        attached_world = omni.usd.get_world_transform_matrix(attached_prim, time_code)
        return attached_world * helper_world.GetInverse()

    @staticmethod
    def _snap_attached_prim(
        helper_prim, attached_prim, attached_to_helper: Gf.Matrix4d
    ) -> None:
        time_code = _current_time_code(helper_prim.GetStage())
        helper_world = omni.usd.get_world_transform_matrix(helper_prim, time_code)
        target_world = attached_to_helper * helper_world
        xform_cache = UsdGeom.XformCache(time_code)
        current_local = xform_cache.GetLocalTransformation(attached_prim)
        if isinstance(current_local, tuple):
            current_local = current_local[0]
        parent_world = xform_cache.GetParentToWorldTransform(attached_prim)
        target_local = target_world * parent_world.GetInverse()

        xformable = UsdGeom.Xformable(attached_prim)
        if not xformable:
            return
        transform_op = ContactGripperModel._get_or_create_transform_op(
            xformable, current_local
        )
        transform_op.Set(target_local)

    @staticmethod
    def _get_local_transformation(prim) -> Gf.Matrix4d | None:
        xform_cache = UsdGeom.XformCache(_current_time_code(prim.GetStage()))
        local_transform = xform_cache.GetLocalTransformation(prim)
        if isinstance(local_transform, tuple):
            local_transform = local_transform[0]
        return local_transform

    @staticmethod
    def _prepare_transform_control(prim) -> Gf.Matrix4d | None:
        xformable = UsdGeom.Xformable(prim)
        if not xformable:
            return None
        current_local = ContactGripperModel._get_local_transformation(prim)
        if current_local is None:
            return None
        transform_op = ContactGripperModel._get_or_create_transform_op(
            xformable, current_local
        )
        transform_op.Set(current_local)
        return current_local

    @staticmethod
    def _restore_original_transform_state(
        prim, xform_state: dict | None, local_transform: Gf.Matrix4d | None
    ) -> None:
        if xform_state is not None:
            ContactGripperModel._restore_xform_state(prim, xform_state)
            return
        if local_transform is not None:
            ContactGripperModel._restore_local_transform(prim, local_transform)

    @staticmethod
    def _restore_local_transform(prim, local_transform: Gf.Matrix4d) -> None:
        xformable = UsdGeom.Xformable(prim)
        if not xformable:
            return
        transform_op = ContactGripperModel._get_or_create_transform_op(
            xformable, local_transform
        )
        transform_op.Set(local_transform)

    @staticmethod
    def _get_or_create_transform_op(
        xformable: UsdGeom.Xformable, current_local: Gf.Matrix4d
    ) -> UsdGeom.XformOp:
        ordered_ops = list(xformable.GetOrderedXformOps())
        for op in ordered_ops:
            if op.GetOpType() == UsdGeom.XformOp.TypeTransform and not op.IsInverseOp():
                ContactGripperModel._remove_extra_xform_ops(xformable, keep_op=op)
                xformable.SetXformOpOrder([op], resetXformStack=False)
                op.Set(current_local)
                return op

        xformable.ClearXformOpOrder()
        transform_op = xformable.AddTransformOp(
            precision=UsdGeom.XformOp.PrecisionDouble
        )
        transform_op.Set(current_local)
        ContactGripperModel._remove_extra_xform_ops(xformable, keep_op=transform_op)
        return transform_op

    @staticmethod
    def _remove_extra_xform_ops(
        xformable: UsdGeom.Xformable, keep_op: UsdGeom.XformOp
    ) -> None:
        keep_name = str(keep_op.GetName())
        for op in list(xformable.GetOrderedXformOps()):
            op_name = str(op.GetName())
            if op_name != keep_name:
                xformable.GetPrim().RemoveProperty(op_name)

        prim = xformable.GetPrim()
        for prop in list(prim.GetProperties()):
            prop_name = prop.GetName()
            if prop_name.startswith("xformOp:") and prop_name != keep_name:
                prim.RemoveProperty(prop_name)

    @staticmethod
    def _remove_all_xform_ops(xformable: UsdGeom.Xformable) -> None:
        xformable.ClearXformOpOrder()
        prim = xformable.GetPrim()
        for prop in list(prim.GetProperties()):
            prop_name = prop.GetName()
            if prop_name.startswith("xformOp:"):
                prim.RemoveProperty(prop_name)

    @staticmethod
    def _capture_xform_state(prim) -> dict | None:
        xformable = UsdGeom.Xformable(prim)
        if not xformable:
            return None
        ordered_ops = list(xformable.GetOrderedXformOps())
        reset_stack = False
        if hasattr(xformable, "GetResetXformStack"):
            reset_stack = bool(xformable.GetResetXformStack())

        ops = []
        for op in ordered_ops:
            attr = op.GetAttr()
            try:
                default_value = attr.Get(Usd.TimeCode.Default())
            except Exception:
                default_value = None
            try:
                time_samples = {
                    sample_time: attr.Get(sample_time)
                    for sample_time in attr.GetTimeSamples()
                }
            except Exception:
                time_samples = {}
            op_name = str(op.GetName())
            name_parts = op_name.split(":")
            suffix = ":".join(name_parts[2:]) if len(name_parts) > 2 else ""
            ops.append(
                {
                    "op_type": op.GetOpType(),
                    "precision": op.GetPrecision(),
                    "suffix": suffix,
                    "is_inverse": op.IsInverseOp(),
                    "default_value": default_value,
                    "time_samples": time_samples,
                }
            )
        return {"reset_stack": reset_stack, "ops": ops}

    @staticmethod
    def _restore_xform_state(prim, xform_state: dict) -> None:
        """Recreate each op with its original default value and time samples.

        A one-shot Set() would flatten an animated (conveyor-driven) prim to
        the single pose it had when grabbed.
        """
        xformable = UsdGeom.Xformable(prim)
        if not xformable or xform_state is None:
            return
        ContactGripperModel._remove_all_xform_ops(xformable)
        restored_ops = []
        for op_state in xform_state["ops"]:
            op = xformable.AddXformOp(
                op_state["op_type"],
                precision=op_state["precision"],
                opSuffix=op_state["suffix"],
                isInverseOp=op_state["is_inverse"],
            )
            attr = op.GetAttr()
            if op_state["default_value"] is not None:
                attr.Set(op_state["default_value"])
            for sample_time, value in op_state["time_samples"].items():
                if value is not None:
                    attr.Set(value, sample_time)
            restored_ops.append(op)
        xformable.SetXformOpOrder(
            restored_ops, resetXformStack=xform_state["reset_stack"]
        )

    # -------------------------------------------------------------------------
    # Physics helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _set_kinematic_while_held(prim) -> bool | None:
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            return None
        try:
            kinematic_attr = UsdPhysics.RigidBodyAPI(prim).GetKinematicEnabledAttr()
            was_kinematic = kinematic_attr.Get()
            if was_kinematic is None:
                was_kinematic = False
            kinematic_attr.Set(True)
            return was_kinematic
        except Exception:
            return None

    @staticmethod
    def _set_kinematic_enabled(prim, enabled: bool) -> None:
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            return
        try:
            UsdPhysics.RigidBodyAPI(prim).GetKinematicEnabledAttr().Set(enabled)
        except Exception:
            pass

    @staticmethod
    def _reset_rigid_body_after_release(prim, enabled: bool | None) -> None:
        if enabled is None or not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            return
        try:
            UsdPhysics.RigidBodyAPI(prim).GetKinematicEnabledAttr().Set(enabled)
            if not enabled:
                ContactGripperModel._wake_rigid_body(prim)
        except Exception:
            pass

    @staticmethod
    def _restore_original_physics_state(prim, metadata: dict | None) -> None:
        if metadata is None:
            return
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            if not metadata.get("rigid_body_enabled", False):
                return
            try:
                UsdPhysics.RigidBodyAPI.Apply(prim)
            except Exception:
                return
        try:
            rigid_body_api = UsdPhysics.RigidBodyAPI(prim)
            if "rigid_body_enabled" in metadata:
                rigid_body_api.GetRigidBodyEnabledAttr().Set(
                    bool(metadata["rigid_body_enabled"])
                )
            if "kinematic_enabled" in metadata:
                rigid_body_api.GetKinematicEnabledAttr().Set(
                    bool(metadata["kinematic_enabled"])
                )
        except Exception:
            pass

    @staticmethod
    def _wake_rigid_body(prim) -> None:
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            return
        try:
            import numpy as np
            import omni.physics.tensors as physics_tensors

            simulation_view = physics_tensors.create_simulation_view("numpy")
            rigid_body_view = simulation_view.create_rigid_body_view(
                prim.GetPath().pathString
            )
            if rigid_body_view.count <= 0:
                return
            indices = np.arange(rigid_body_view.count, dtype=np.int32)
            rigid_body_view.wake_up(indices)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Recovery metadata helpers
    # -------------------------------------------------------------------------

    def _ensure_recovery_metadata(
        self, prim, local_transform: Gf.Matrix4d | None
    ) -> None:
        if prim is None or not prim.IsValid():
            return
        if self._get_recovery_metadata(prim):
            return

        metadata: dict = {}
        if local_transform is not None:
            metadata["original_local_transform"] = Gf.Matrix4d(local_transform)

        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            rigid_body_api = UsdPhysics.RigidBodyAPI(prim)
            metadata["rigid_body_enabled"] = bool(
                rigid_body_api.GetRigidBodyEnabledAttr().Get()
            )
            metadata["kinematic_enabled"] = bool(
                rigid_body_api.GetKinematicEnabledAttr().Get()
            )

        metadata["active_holders"] = 0
        prim.SetCustomDataByKey(_RECOVERY_KEY, metadata)

    @staticmethod
    def _get_recovery_metadata(prim) -> dict | None:
        if prim is None or not prim.IsValid():
            return None
        metadata = prim.GetCustomDataByKey(_RECOVERY_KEY)
        return metadata if metadata else None

    @staticmethod
    def _set_recovery_metadata(prim, metadata: dict) -> None:
        if prim is None or not prim.IsValid():
            return
        prim.SetCustomDataByKey(_RECOVERY_KEY, metadata)

    @staticmethod
    def _increment_active_holders(prim) -> int:
        metadata = ContactGripperModel._get_recovery_metadata(prim)
        if metadata is None:
            return 0
        holder_count = int(metadata.get("active_holders", 0)) + 1
        metadata["active_holders"] = holder_count
        ContactGripperModel._set_recovery_metadata(prim, metadata)
        return holder_count

    @staticmethod
    def _decrement_active_holders(prim) -> int:
        metadata = ContactGripperModel._get_recovery_metadata(prim)
        if metadata is None:
            return 0
        holder_count = max(0, int(metadata.get("active_holders", 0)) - 1)
        metadata["active_holders"] = holder_count
        ContactGripperModel._set_recovery_metadata(prim, metadata)
        return holder_count

    @staticmethod
    def _clear_recovery_metadata(prim) -> None:
        if prim is None or not prim.IsValid():
            return
        try:
            prim.ClearCustomDataByKey(_RECOVERY_KEY)
        except Exception:
            pass
