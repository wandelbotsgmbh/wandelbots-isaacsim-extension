from typing import Callable, cast
import weakref
import carb.events
import numpy as np
import isaacsim.core.utils.prims as prims_utils
import isaacsim.core.utils.stage as stage_utils
from wandelbots.omni.datatypes import (
    COORDINATE_SYSTEM,
    ROTATION_TYPES,
    Pose,
    QuatPose,
    WSPose,
    RelativePoseMode,
)
from wandelbots.omni.environment import host_database
from isaacsim.core.prims import RigidPrim
from isaacsim.sensors.camera import Camera
from pxr import Gf, Usd, UsdGeom, UsdPhysics, Sdf
import carb
import omni.usd
from wandelbots.omni.utils.scene import SceneUtils
from wandelbots.omni.utils.math import (
    quat_to_rotvec,
    rotvec_to_quat,
    pose_to_matrix as math_pose_to_matrix,
    matrix_to_pose as math_matrix_to_pose,
)
from omni.usd import get_watcher
import omni.timeline
from wandelbots.omni.manipulators.utils import get_link_0_from_motion_group_prim


class PrimUtils:
    # Constructing a RigidPrim view is expensive: its init classifies the
    # prim via a full-subtree scan (is_prim_non_root_articulation_link),
    # ~17ms for a robot link. The views are designed to be long-lived (they
    # track prim deletion via is_valid and re-attach to the physics sim
    # view through SimulationManager callbacks), so reuse one per prim path
    # instead of rebuilding per pose access.
    _rigid_prim_cache: dict[str, RigidPrim] = {}

    @staticmethod
    def _get_rigid_prim(prim_path: str) -> RigidPrim:
        view = PrimUtils._rigid_prim_cache.get(prim_path)
        # A cached view is only reusable while its prim is alive and belongs
        # to the stage a fresh RigidPrim would bind to: isaacsim's current
        # stage, which stage changes and the use_stage context swap without
        # touching the omni.usd context. A same-named prim on another stage
        # must never reuse it.
        if (
            view is None
            or not view.is_valid()
            or not view.prims
            or not view.prims[0].IsValid()
            or view.prims[0].GetStage() != stage_utils.get_current_stage()
        ):
            view = RigidPrim(prim_path)
            PrimUtils._rigid_prim_cache[prim_path] = view
        return view

    @staticmethod
    def get_prim(prim_path: str, stage: Usd.Stage = None) -> Usd.Prim:
        if stage is None:
            stage = omni.usd.get_context().get_stage()
        return stage.GetPrimAtPath(prim_path)

    @staticmethod
    def is_prim_valid(prim_path: str) -> bool:
        return prims_utils.is_prim_path_valid(prim_path)

    def prim_has_transform(prim: Usd.Prim) -> bool:
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            return True
        if prim.GetTypeName() == "Camera":
            return True
        return prim.HasAttribute("xformOp:translate") and (
            prim.HasAttribute("xformOp:orient") or prim.HasAttribute("xformOp:rotate")
        )

    def _get_xformable_prim_pose(
        prim: Usd.Prim,
        coordinate_system: COORDINATE_SYSTEM = "local",
        rotation_type: ROTATION_TYPES = "cartesian",
        stage: Usd.Stage = None,
    ) -> Pose:
        if not prim.IsA(UsdGeom.Xformable):
            raise ValueError(f"Prim {prim.GetPath()} is not Xformable.")

        xform = UsdGeom.Xformable(prim)
        time = Usd.TimeCode.Default()
        transformation: Gf.Matrix4d = (
            xform.ComputeLocalToWorldTransform(time)
            if coordinate_system == "world"
            else xform.GetLocalTransformation()
        )

        # Orthonormalize the transformation matrix to avoid scaling issues
        if not transformation.Orthonormalize():
            carb.log_warn(f"Transform for prim {prim.GetPath()} orthonormalize failed.")
        position = np.array(transformation.ExtractTranslation())

        if rotation_type == "cartesian":
            # Extract quaternion and convert to rotation vector (axis-angle representation)
            # Wandelbots poses use rotation vectors: [rx, ry, rz] = axis.normalized * angle (radians)
            quat = transformation.ExtractRotation().GetQuaternion()
            w, (x, y, z) = quat.GetReal(), quat.GetImaginary()
            rotation = quat_to_rotvec(x, y, z, w)
        else:
            orientation = transformation.ExtractRotation().GetQuaternion()
            w, (x, y, z) = orientation.GetReal(), orientation.GetImaginary()
            rotation = [w, x, y, z]

        pose = (
            (position / SceneUtils.get_stage_units(stage)) * 1000
        ).tolist() + rotation

        return (
            WSPose(pose=pose) if rotation_type == "cartesian" else QuatPose(pose=pose)
        )

    def get_prim_pose(
        prim_path: str,
        coordinate_system: COORDINATE_SYSTEM = "local",
        rotation_type: ROTATION_TYPES = "cartesian",
        stage: Usd.Stage = None,
    ) -> Pose:
        prim = PrimUtils.get_prim(prim_path, stage)
        if prim is None or not prim.IsValid():
            raise ValueError(f"Prim at path {prim_path} is not valid.")
        if prim.HasAPI(UsdPhysics.RigidBodyAPI) and prim.GetAttribute(
            "physics:rigidBodyEnabled"
        ).Get(Usd.TimeCode.Default()):
            # RigidPrim throws when "physics:rigidBodyEnabled" is false
            # since its static then we can just get the pose via the xformable method
            try:
                rigid_prim = PrimUtils._get_rigid_prim(prim_path)
                poses = (
                    rigid_prim.get_world_poses()
                    if coordinate_system == "world"
                    else rigid_prim.get_local_poses()
                )
                position = poses[0][0]
                quat = poses[1][0]
            except Exception as error:
                # The physics simulation view can be invalidated out from under
                # us -- e.g. an articulation link prim deleted/recreated during
                # restructuring, or any stage edit while the timeline plays.
                # Constructing/querying a RigidPrim then raises "Simulation view
                # object is invalidated". Several callers run on stage-event and
                # async paths (ghost tool bar, teaching overlay) and must not
                # crash, so drop the stale cached view and fall back to the
                # authored USD xform pose, which is the best available transform
                # while physics has no valid state.
                carb.log_verbose(
                    f"Physics pose for {prim_path} unavailable ({error}); "
                    "falling back to USD xform pose."
                )
                PrimUtils._rigid_prim_cache.pop(prim_path, None)
                if prim.IsA(UsdGeom.Xformable):
                    return PrimUtils._get_xformable_prim_pose(
                        prim, coordinate_system, rotation_type, stage
                    )
                raise

        elif prim.GetTypeName() == "Camera":
            camera = Camera(prim.GetPrimPath().pathString)
            position, quat = (
                camera.get_world_pose()
                if coordinate_system == "world"
                else camera.get_local_pose()
            )

        elif prim.IsA(UsdGeom.Xformable):
            return PrimUtils._get_xformable_prim_pose(
                prim, coordinate_system, rotation_type, stage
            )
        else:
            parent = prim.GetParent()
            if not parent:
                raise ValueError(
                    f"Prim {prim_path} has no transform definition and has no parent to get the pose from."
                )
            return PrimUtils.get_prim_pose(
                parent.GetPrimPath().pathString,
                coordinate_system=coordinate_system,
                rotation_type=rotation_type,
                stage=stage,
            )

        rotation = (
            quat_to_rotvec(quat[1], quat[2], quat[3], quat[0])
            if rotation_type == "cartesian"
            else quat.tolist()
        )
        pose = ((position / SceneUtils.get_stage_units(stage)) * 1000).tolist() + (
            rotation if isinstance(rotation, list) else rotation.tolist()
        )

        return (
            WSPose(pose=pose) if rotation_type == "cartesian" else QuatPose(pose=pose)
        )

    def set_prim_pose(
        prim_path: str, input_pose: WSPose, stage: Usd.Stage = None
    ) -> None:
        position = tuple(each / 1000 for each in input_pose.pose[:3])
        rot = tuple(input_pose.pose[3:])
        # Convert rotation vector to quaternion [x, y, z, w]
        quat_xyzw = rotvec_to_quat(*rot)
        # Reorder to [w, x, y, z] for USD
        rotation = [quat_xyzw[3], quat_xyzw[0], quat_xyzw[1], quat_xyzw[2]]

        prim = PrimUtils.get_prim(prim_path, stage)
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            prim = PrimUtils._get_rigid_prim(prim_path)
            # (IsaacSim +5.0)
            if hasattr(prim, "set_local_poses"):
                prim.set_local_poses(
                    translations=np.array([position]), orientations=np.array([rotation])
                )
            else:
                prim.set_local_pose(translation=position, orientation=rotation)
            return
        elif prim.GetTypeName() == "Camera":
            camera = Camera(prim.GetPrimPath().pathString)
            camera.set_local_pose(position, rotation)
            return

        all_attributes = prims_utils.get_prim_attribute_names(prim_path)
        if "xformOp:translate" in all_attributes:
            prims_utils.set_prim_property(
                prim_path,
                property_name="xformOp:translate",
                property_value=position,
            )
        if "xformOp:orient" in all_attributes:
            w, x, y, z = rotation
            try:
                prims_utils.set_prim_property(
                    prim_path,
                    property_name="xformOp:orient",
                    property_value=Gf.Quatf(w, x, y, z),
                )
            except Exception as _:
                prims_utils.set_prim_property(
                    prim_path,
                    property_name="xformOp:orient",
                    property_value=Gf.Quatd(w, x, y, z),
                )

    @staticmethod
    def pose_to_matrix(pose: list[float]) -> np.ndarray:
        return math_pose_to_matrix(pose)

    @staticmethod
    def matrix_to_pose(mat: np.ndarray) -> np.ndarray:
        return np.array(math_matrix_to_pose(mat))

    @staticmethod
    def get_motion_group_base_world_pose(
        motion_group_prim: Usd.Prim,
    ) -> WSPose | None:
        """World pose of a robot's kinematic base (link_0).

        This is the frame NOVA calls the mounting, and the frame link-relative
        collider poses are built on.
        """
        base_prim = get_link_0_from_motion_group_prim(motion_group_prim)
        if base_prim is None or not base_prim.IsValid():
            return None
        return PrimUtils.get_prim_pose(
            base_prim.GetPath().pathString, coordinate_system="world"
        )

    def get_relative_prim_pose(
        prim_path_a: str,
        prim_path_b: str,
        mode: RelativePoseMode = RelativePoseMode.NORMAL,
        rotation_type: ROTATION_TYPES = "cartesian",
    ) -> Pose:
        pose_a = PrimUtils.get_prim_pose(
            prim_path=prim_path_a, coordinate_system="world"
        )
        pose_b = PrimUtils.get_prim_pose(
            prim_path=prim_path_b, coordinate_system="world"
        )
        return PrimUtils.get_relative_pose(pose_a, pose_b, mode, rotation_type)

    def get_relative_pose(
        pose_a: Pose,
        pose_b: Pose,
        mode: RelativePoseMode = RelativePoseMode.NORMAL,
        rotation_type: ROTATION_TYPES = "cartesian",
    ) -> Pose:
        matrix1 = PrimUtils.pose_to_matrix(pose_a.pose)
        matrix2 = PrimUtils.pose_to_matrix(pose_b.pose)

        if mode == RelativePoseMode.NORMAL:
            result_matrix = np.linalg.inv(matrix1) @ matrix2
        elif mode == RelativePoseMode.INVERSE_FIRST:
            result_matrix = matrix1 @ matrix2
        elif mode == RelativePoseMode.INVERSE_SECOND:
            result_matrix = np.linalg.inv(matrix1) @ np.linalg.inv(matrix2)
        elif mode == RelativePoseMode.INVERSE_BOTH:
            result_matrix = np.linalg.inv(matrix2) @ matrix1
        else:
            raise ValueError(f"Invalid mode: {mode}")

        result_pose = np.round(PrimUtils.matrix_to_pose(result_matrix), 3)
        if rotation_type == "cartesian":
            return WSPose(pose=result_pose.tolist())
        else:
            quat = rotvec_to_quat(*result_pose[3:])
            return QuatPose(pose=result_pose[:3].tolist() + quat)

    def set_relative_pose(
        prim_path: str,
        relative_pose: WSPose,
        object_first: bool = False,
        stage: Usd.Stage = None,
    ) -> None:
        current_pose = PrimUtils.get_prim_pose(
            prim_path, rotation_type="cartesian", stage=stage
        )
        # Compose as SE(3) transforms via homogeneous matrices so the relative
        # translation is rotated into the correct frame (rather than being added
        # in raw world coordinates), mirroring get_relative_pose.
        T_current = PrimUtils.pose_to_matrix(current_pose.pose)
        T_relative = PrimUtils.pose_to_matrix(relative_pose.pose)

        if object_first:
            # Relative transform expressed in the object's local frame: T_obj ∘ T_rel.
            T_new = T_current @ T_relative
        else:
            # Relative transform expressed in the world frame, applied first: T_rel ∘ T_obj.
            T_new = T_relative @ T_current

        new_pose = PrimUtils.matrix_to_pose(T_new).tolist()
        PrimUtils.set_prim_pose(prim_path, WSPose(pose=new_pose), stage)

    def reset_objects(prim_path: str) -> None:
        children_prims = prims_utils.get_all_matching_child_prims(
            prim_path, lambda _: True
        )
        if not children_prims:
            raise ValueError(f"No children found for {prim_path}")

        for child_prim in children_prims:
            child_prim_path = child_prim.GetPrimPath().pathString
            default_poses = host_database.get("default_poses", {})
            if child_prim_path in default_poses:
                default_pose = host_database[f"default_poses.{child_prim_path}"]
                PrimUtils.set_prim_pose(child_prim_path, default_pose)
            else:
                carb.log_warn(f"Default pose not set for prim: {child_prim_path}")

    def get_world_transform_xform(
        prim: Usd.Prim,
    ) -> tuple[Gf.Vec3d, Gf.Rotation, Gf.Vec3d]:
        """
        Get the world transform of a prim.
        Returns translation, rotation, and scale.
        """

        world_transform: Gf.Matrix4d = omni.usd.get_world_transform_matrix(prim)
        scale: Gf.Vec3d = Gf.Vec3d(
            *(
                cast(Gf.Vec3d, v).GetLength()
                for v in world_transform.ExtractRotationMatrix()
            )
        )

        if not world_transform.Orthonormalize():
            carb.log_warn(
                f"Warning: World transform for prim {prim.GetPath()} is not orthonormal."
            )

        translation: Gf.Vec3d = world_transform.ExtractTranslation()
        rotation: Gf.Rotation = world_transform.ExtractRotation()

        return translation, rotation, scale


class PrimPoseWatcher:
    def __init__(
        self,
        prim: Usd.Prim,
        pose_changed_fn: Callable[[Pose], None],
        relative_prim: Usd.Prim = None,
        max_rotation_dif_rad: float = 0.01,  # radians
        max_translation_dif_m: float = 0.001,  # meters
    ):
        self._stage: Usd.Stage = prim.GetStage()
        self._prim = prim
        self._pose_changed_fn = pose_changed_fn
        self._relative_prim = relative_prim
        self._change_subscriptions: list[carb.Subscription] = []
        self._last_pose: Pose | None = None
        self._timeline_sub: carb.Subscription | None = None
        self._stage_event_sub: carb.Subscription | None = None
        self._max_rotation_dif = max_rotation_dif_rad  # radians
        self._max_translation_dif = max_translation_dif_m * SceneUtils.get_stage_units(
            self._stage
        )

        self._timeline = omni.timeline.get_timeline_interface()
        self._timeline_stop_reset_applied = False
        carb.log_verbose(f"{self} listening to timeline events")
        weak_self = weakref.ref(self)

        def _on_stage_events(event: carb.events.IEvent):
            instance = weak_self()
            if not instance:
                return

            if event.type in (
                int(omni.usd.StageEventType.OPENED),
                int(omni.usd.StageEventType.CLOSED),
            ):
                instance._cleanup()

        self._stage_event_sub = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(_on_stage_events)
        )

        def _on_timeline_events(event: carb.events.IEvent):
            instance = weak_self()
            if not instance:
                return

            # Check if prims are still valid, cleanup if not
            if not instance._has_valid_prims():
                carb.log_verbose(
                    f"PrimPoseWatcher: Prims for {instance._prim.GetPath()} are no longer valid, cleaning up"
                )
                instance._cleanup()
                return

            is_play_or_stop = event.type in (
                omni.timeline.TimelineEventType.PLAY.value,
                omni.timeline.TimelineEventType.STOP.value,
            )
            needs_stop_reset = (
                instance._timeline.is_stopped()
                and not instance._timeline_stop_reset_applied
            )
            # The pose read is expensive and timeline ticks arrive every
            # frame while playing; only compute it for events that fire the
            # callback (PLAY/STOP, plus one tick after a stop).
            if not is_play_or_stop and not needs_stop_reset:
                return

            current_pose = instance.current_pose

            if is_play_or_stop:
                instance._pose_changed_fn(current_pose)
                instance._timeline_stop_reset_applied = False
            else:
                # The timeline stops, but the position reset happens one frame later so we wait for the next tick.
                instance._pose_changed_fn(current_pose)
                instance._timeline_stop_reset_applied = True

        self._timeline_sub = (
            self._timeline.get_timeline_event_stream().create_subscription_to_pop(
                _on_timeline_events
            )
        )

        carb.log_verbose(f"Subscribing to {prim} prim changes.")

        def _on_prim_changed(path: Sdf.Path = None):
            path_str: str = path.pathString
            if not (
                path_str.endswith(":translate")
                or path_str.endswith(":rotate")
                or path_str.endswith(":orient")
            ):
                return

            instance = weak_self()
            if not instance:
                return

            current_pose = instance.current_pose

            if instance._last_pose:
                translation_dif = np.linalg.norm(
                    np.array(current_pose.pose[:3])
                    - np.array(instance._last_pose.pose[:3])
                )
                no_translation_dif = translation_dif <= instance._max_translation_dif

                rotation_dif = np.linalg.norm(
                    np.array(current_pose.pose[3:])
                    - np.array(instance._last_pose.pose[3:])
                )
                no_rotation_dif = rotation_dif <= instance._max_rotation_dif

                if no_translation_dif and no_rotation_dif:
                    return

            instance._pose_changed_fn(current_pose)
            instance._last_pose = current_pose

        subscribe_prim = get_link_0_from_motion_group_prim(self._prim)

        while subscribe_prim:
            carb.log_verbose(f"Subscribing to prim changes for {subscribe_prim}.")
            self._change_subscriptions.append(
                get_watcher().subscribe_to_change_info_path(
                    subscribe_prim.GetPath(),
                    _on_prim_changed,
                )
            )
            subscribe_prim = subscribe_prim.GetParent()

        if self._relative_prim:
            carb.log_verbose(
                f"Subscribing to relative prim changes for {self._relative_prim}."
            )
            self._change_subscriptions.append(
                get_watcher().subscribe_to_change_info_path(
                    self._relative_prim.GetPath(),
                    _on_prim_changed,
                )
            )

    def _has_valid_prims(self) -> bool:
        return self._prim.IsValid() and (
            not self._relative_prim or self._relative_prim.IsValid()
        )

    def _cleanup(self):
        """Unsubscribe from all watcher events."""
        carb.log_verbose(f"Cleaning up PrimPoseWatcher for {self._prim.GetPath()}")

        # Unsubscribe from all prim change subscriptions
        for subscription in self._change_subscriptions:
            subscription.unsubscribe()
        self._change_subscriptions.clear()

        # Unsubscribe from timeline events
        if self._timeline_sub:
            self._timeline_sub.unsubscribe()
            self._timeline_sub = None

        # Unsubscribe from stage events
        if self._stage_event_sub:
            self._stage_event_sub.unsubscribe()
            self._stage_event_sub = None

    @property
    def current_pose(self) -> Pose:
        # Check if prims are still valid, cleanup if not
        if not self._has_valid_prims():
            self._cleanup()
            raise RuntimeError(
                f"Cannot get pose: prims for {self._prim.GetPath()} are no longer valid"
            )

        if self._relative_prim:
            return PrimUtils.get_relative_prim_pose(
                self._relative_prim.GetPrimPath().pathString,
                self._prim.GetPath().pathString,
            )
        return PrimUtils.get_prim_pose(
            self._prim.GetPrimPath().pathString,
            coordinate_system="world",
        )

    def __del__(self):
        self._cleanup()
