from typing import Callable, cast
import weakref
import carb.events
import numpy as np
import isaacsim.core.utils.prims as prims_utils
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
    compose_rotvecs,
    pose_to_matrix as math_pose_to_matrix,
    matrix_to_pose as math_matrix_to_pose,
)
from omni.usd import get_watcher
import omni.timeline


class PrimUtils:
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
            prim = RigidPrim(prim_path)
            poses = (
                prim.get_world_poses()
                if coordinate_system == "world"
                else prim.get_local_poses()
            )
            position = poses[0][0]
            quat = poses[1][0]

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
            prim = RigidPrim(prim_path)
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
        prim_path: str, relative_pose: WSPose, object_first: bool = False
    ) -> None:
        current_pose = PrimUtils.get_prim_pose(prim_path, rotation_type="cartesian")
        current_translation = np.array(current_pose.pose[:3])
        relative_translation = np.array(relative_pose.pose[:3])

        current_rotvec = current_pose.pose[3:]
        relative_rotvec = relative_pose.pose[3:]

        if object_first:
            new_translation = (current_translation - relative_translation).tolist()
            new_rotvec = compose_rotvecs(relative_rotvec, current_rotvec)
        else:
            new_translation = (current_translation + relative_translation).tolist()
            new_rotvec = compose_rotvecs(current_rotvec, relative_rotvec)

        new_pose = new_translation + new_rotvec
        PrimUtils.set_prim_pose(prim_path, WSPose(pose=new_pose))

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
        self._max_rotation_dif = max_rotation_dif_rad  # radians
        self._max_translation_dif = max_translation_dif_m * SceneUtils.get_stage_units(
            self._stage
        )

        self._timeline = omni.timeline.get_timeline_interface()
        self._timeline_stop_reset_applied = False
        carb.log_verbose(f"{self} listening to timeline events")

        def _on_timeline_events(event: carb.events.IEvent, weak_self=weakref.ref(self)):
            weak_self_instance = weak_self()
            if not weak_self_instance:
                return
            if (
                event.type == omni.timeline.TimelineEventType.PLAY.value
                or event.type == omni.timeline.TimelineEventType.STOP.value
            ):
                weak_self_instance._pose_changed_fn(weak_self_instance.current_pose)
                weak_self_instance._timeline_stop_reset_applied = False
            elif (
                weak_self_instance._timeline.is_stopped()
                and not weak_self_instance._timeline_stop_reset_applied
            ):
                # The timeline stops, but the position reset happens one frame later so we wait for the next tick.
                weak_self_instance._pose_changed_fn(weak_self_instance.current_pose)
                weak_self_instance._timeline_stop_reset_applied = True

        self._timeline_sub = (
            self._timeline.get_timeline_event_stream().create_subscription_to_pop(
                lambda event, weak_self=weakref.ref(self): (
                    _on_timeline_events(event=event) if weak_self() else None
                )
            )
        )

        carb.log_verbose(f"Subscribing to {prim} prim changes.")

        def _on_prim_changed(path: Sdf.Path = None, weak_self=weakref.ref(self)):
            path_str: str = path.pathString
            if not (
                path_str.endswith(":translate")
                or path_str.endswith(":rotate")
                or path_str.endswith(":orient")
            ):
                return

            weak_self_instance = weak_self()
            if not weak_self_instance:
                return

            current_pose = weak_self_instance.current_pose

            if weak_self_instance._last_pose:
                translation_dif = np.linalg.norm(
                    np.array(current_pose.pose[:3])
                    - np.array(weak_self_instance._last_pose.pose[:3])
                )
                no_translation_dif = (
                    translation_dif <= weak_self_instance._max_translation_dif
                )

                rotation_dif = np.linalg.norm(
                    np.array(current_pose.pose[3:])
                    - np.array(weak_self_instance._last_pose.pose[3:])
                )
                no_rotation_dif = rotation_dif <= weak_self_instance._max_rotation_dif

                if no_translation_dif and no_rotation_dif:
                    return

            weak_self_instance._pose_changed_fn(current_pose)
            weak_self_instance._last_pose = current_pose

        subscribe_prim = self._prim
        while subscribe_prim:
            carb.log_verbose(f"Subscribing to prim changes for {subscribe_prim}.")
            self._change_subscriptions.append(
                get_watcher().subscribe_to_change_info_path(
                    subscribe_prim.GetPath(),
                    _on_prim_changed,
                )
            )
            subscribe_prim = subscribe_prim.GetParent()

    @property
    def current_pose(self) -> Pose:
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
        carb.log_verbose(f"Unsubscribing from {self._prim} prim changes.")
        for subscription in self._change_subscriptions:
            subscription.unsubscribe()
        self._change_subscriptions.clear()
        self._timeline_sub.unsubscribe()
