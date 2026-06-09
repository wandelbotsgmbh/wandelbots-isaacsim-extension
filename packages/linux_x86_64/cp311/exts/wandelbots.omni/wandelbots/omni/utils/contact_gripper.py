import fnmatch
import time
from collections.abc import Callable

import carb
import omni.kit.app
import omni.timeline
import omni.usd
from pxr import Gf, Usd, UsdGeom, UsdPhysics

_RECOVERY_KEY = "wandelbotsGripperRecovery"
_RELEASE_GRACE_S = 0.2


class ContactGripperModel:
    """Contact-gripper logic decoupled from any OmniGraph node.

    Call attach() to grab the first candidate prim inside the helper volume,
    release() to let go, and restore_all() to reset all touched prims (e.g. on
    simulation stop).  Register on_attached / on_released callbacks to react to
    state changes from any consumer (UI, tests, …).
    """

    def __init__(self) -> None:
        # attachment state
        self.attached_prim_path: str = ""
        self.attached_to_helper: Gf.Matrix4d = Gf.Matrix4d(1.0)
        self.restore_kinematic_enabled: bool | None = None

        # pending-release grace period
        self.pending_release_prim_path: str = ""
        self.pending_release_restore_kinematic_enabled: bool | None = None
        self.pending_release_deadline: float = 0.0

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
        return bool(self.attached_prim_path)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def attach(
        self,
        helper_path: str,
        candidate_paths: list[str],
        exclude_paths: list[str],
    ) -> bool:
        """Attach the first overlapping candidate prim to the helper volume.

        Returns True if a prim was successfully attached. Fires on_attached.
        """
        if self.attached_prim_path:
            return False

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return False

        self._update_pending_release(stage)

        helper_prim = stage.GetPrimAtPath(helper_path)
        if not helper_prim.IsValid():
            carb.log_error(
                f"Contact Gripper: helper prim does not exist: {helper_path}"
            )
            return False

        self.helper_prim_path = helper_path

        candidate_prim = self._find_candidate_prim(
            stage, helper_prim, candidate_paths, exclude_paths
        )
        if candidate_prim is None:
            return False

        self._do_attach(stage, helper_prim, candidate_prim)
        if self.on_attached:
            self.on_attached(self.attached_prim_path)
        return True

    def release(self) -> bool:
        """Release the currently attached prim.

        Returns True if a prim was released. Fires on_released.
        """
        if not self.attached_prim_path:
            return False

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return False

        self._update_pending_release(stage)

        helper_prim = stage.GetPrimAtPath(self.helper_prim_path)
        if not helper_prim.IsValid():
            return False

        attached_prim = stage.GetPrimAtPath(self.attached_prim_path)
        if not attached_prim.IsValid():
            carb.log_error(
                f"Contact Gripper: attached prim no longer exists: {self.attached_prim_path}"
            )
            return False

        self._snap_attached_prim(helper_prim, attached_prim, self.attached_to_helper)
        released_path = self.attached_prim_path
        self._clear_attachment_state(stage, restore_transform=False)

        if self.on_released:
            self.on_released(released_path)
        return True

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

        self._update_pending_release(stage)

        if not self.attached_prim_path or not self.helper_prim_path:
            if not self.pending_release_prim_path:
                self._stop_frame_updates()
            return

        helper_prim = stage.GetPrimAtPath(self.helper_prim_path)
        if not helper_prim.IsValid():
            return

        attached_prim = stage.GetPrimAtPath(self.attached_prim_path)
        if not attached_prim.IsValid():
            carb.log_warn(
                f"Contact Gripper: attached prim no longer exists: {self.attached_prim_path}"
            )
            self.attached_prim_path = ""
            if not self.pending_release_prim_path:
                self._stop_frame_updates()
            return

        self._snap_attached_prim(helper_prim, attached_prim, self.attached_to_helper)

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
        self.attached_to_helper = self._compute_attach_offset(
            helper_prim, candidate_prim
        )
        self._prepare_transform_control(candidate_prim)
        self.touched_prim_paths.add(candidate_path)
        self.attached_prim_path = candidate_path

        if recovery_metadata is not None and "kinematic_enabled" in recovery_metadata:
            self.restore_kinematic_enabled = bool(
                recovery_metadata["kinematic_enabled"]
            )
            self._set_kinematic_enabled(candidate_prim, True)
        else:
            self.restore_kinematic_enabled = self._set_kinematic_while_held(
                candidate_prim
            )

        if candidate_path not in self.original_kinematic_enabled:
            self.original_kinematic_enabled[candidate_path] = (
                self.restore_kinematic_enabled
            )

        self._snap_attached_prim(helper_prim, candidate_prim, self.attached_to_helper)
        self._start_frame_updates()

    def _clear_attachment_state(
        self,
        stage: Usd.Stage,
        restore_transform: bool,
    ) -> None:
        if not self.attached_prim_path:
            self.attached_prim_path = ""
            self.attached_to_helper = Gf.Matrix4d(1.0)
            self.restore_kinematic_enabled = None
            return

        released_prim = stage.GetPrimAtPath(self.attached_prim_path)
        if released_prim.IsValid():
            remaining_holders = self._decrement_active_holders(released_prim)
            if restore_transform:
                self._restore_original_transform_state(
                    released_prim,
                    self.original_xform_states.get(self.attached_prim_path),
                    self.original_local_transforms.get(self.attached_prim_path),
                )
            elif remaining_holders <= 0:
                self._schedule_pending_release(
                    released_prim,
                    self.restore_kinematic_enabled,
                )

        self.attached_prim_path = ""
        self.attached_to_helper = Gf.Matrix4d(1.0)
        self.restore_kinematic_enabled = None

    def _restore_all_touched_objects(self, stage: Usd.Stage) -> None:
        self._stop_frame_updates()

        if self.attached_prim_path:
            self.touched_prim_paths.add(self.attached_prim_path)

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

        self.attached_prim_path = ""
        self.attached_to_helper = Gf.Matrix4d(1.0)
        self.restore_kinematic_enabled = None
        self.pending_release_prim_path = ""
        self.pending_release_restore_kinematic_enabled = None
        self.pending_release_deadline = 0.0
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
        enabled: bool | None,
    ) -> None:
        if prim is None or not prim.IsValid():
            return
        self.pending_release_prim_path = prim.GetPath().pathString
        self.pending_release_restore_kinematic_enabled = enabled
        self.pending_release_deadline = time.monotonic() + _RELEASE_GRACE_S

    def _cancel_pending_release(self, prim_path: str) -> None:
        if self.pending_release_prim_path != prim_path:
            return
        self.pending_release_prim_path = ""
        self.pending_release_restore_kinematic_enabled = None
        self.pending_release_deadline = 0.0

    def _update_pending_release(self, stage: Usd.Stage) -> None:
        if not self.pending_release_prim_path:
            return

        prim = stage.GetPrimAtPath(self.pending_release_prim_path)
        if not prim.IsValid():
            self._cancel_pending_release(self.pending_release_prim_path)
            return

        metadata = self._get_recovery_metadata(prim)
        active_holders = int(metadata.get("active_holders", 0)) if metadata else 0
        if active_holders > 0:
            self._cancel_pending_release(self.pending_release_prim_path)
            return

        if time.monotonic() < self.pending_release_deadline:
            return

        self._reset_rigid_body_after_release(
            prim, self.pending_release_restore_kinematic_enabled
        )
        self._cancel_pending_release(self.pending_release_prim_path)

    # -------------------------------------------------------------------------
    # Candidate selection
    # -------------------------------------------------------------------------

    def _find_candidate_prim(
        self,
        stage: Usd.Stage,
        helper_prim,
        candidate_paths: list[str],
        exclude_paths: list[str],
    ):
        helper_bounds = self._compute_world_aligned_bounds(helper_prim)
        if helper_bounds.IsEmpty():
            carb.log_error(
                "Contact Gripper: helper prim has no world bounds. Use a helper prim with "
                f"visible geometry or bounded children: {helper_prim.GetPath().pathString}"
            )
            return None

        helper_path = helper_prim.GetPath()

        if candidate_paths:
            for candidate_prim in stage.Traverse():
                if self._matches_filter(candidate_prim, exclude_paths):
                    continue
                if not self._matches_filter(candidate_prim, candidate_paths):
                    continue
                if self._is_attachable_candidate(
                    helper_path, candidate_prim, helper_bounds
                ):
                    return candidate_prim
            return None

        for candidate_prim in stage.Traverse():
            if self._matches_filter(candidate_prim, exclude_paths):
                continue
            if self._is_attachable_candidate(
                helper_path, candidate_prim, helper_bounds
            ):
                return candidate_prim

        return None

    @staticmethod
    def _matches_filter(candidate_prim, filters: list[str]) -> bool:
        candidate_path = candidate_prim.GetPath().pathString
        candidate_path_without_root = candidate_path.lstrip("/")

        for pattern in filters:
            normalized = pattern.strip()
            if not normalized:
                continue
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
    def _is_attachable_candidate(helper_path, candidate_prim, helper_bounds) -> bool:
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

        candidate_bounds = ContactGripperModel._compute_world_aligned_bounds(
            candidate_prim
        )
        if candidate_bounds.IsEmpty():
            return False

        return ContactGripperModel._ranges_intersect(helper_bounds, candidate_bounds)

    @staticmethod
    def _compute_world_aligned_bounds(prim) -> Gf.Range3d:
        bbox_cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            includedPurposes=[
                UsdGeom.Tokens.default_,
                UsdGeom.Tokens.render,
                UsdGeom.Tokens.proxy,
            ],
            useExtentsHint=True,
        )
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
        helper_world = omni.usd.get_world_transform_matrix(helper_prim)
        attached_world = omni.usd.get_world_transform_matrix(attached_prim)
        return attached_world * helper_world.GetInverse()

    @staticmethod
    def _snap_attached_prim(
        helper_prim, attached_prim, attached_to_helper: Gf.Matrix4d
    ) -> None:
        helper_world = omni.usd.get_world_transform_matrix(helper_prim)
        target_world = attached_to_helper * helper_world
        xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
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
        xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
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
            try:
                value = op.Get()
            except Exception:
                value = None
            op_name = str(op.GetName())
            name_parts = op_name.split(":")
            suffix = ":".join(name_parts[2:]) if len(name_parts) > 2 else ""
            ops.append(
                {
                    "op_type": op.GetOpType(),
                    "precision": op.GetPrecision(),
                    "suffix": suffix,
                    "is_inverse": op.IsInverseOp(),
                    "value": value,
                }
            )
        return {"reset_stack": reset_stack, "ops": ops}

    @staticmethod
    def _restore_xform_state(prim, xform_state: dict) -> None:
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
            if op_state["value"] is not None:
                op.Set(op_state["value"])
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
