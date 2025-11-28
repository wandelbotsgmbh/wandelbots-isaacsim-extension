from typing import cast
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
from pxr import Gf, Usd, UsdGeom, UsdPhysics
from scipy.spatial.transform import Rotation
import carb
import omni.usd
from wandelbots.omni.utils.scene import SceneUtils


class PrimUtils:
    @staticmethod
    def get_prim(prim_path: str) -> Usd.Prim:
        return prims_utils.get_prim_at_path(prim_path)

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

    def get_prim_pose(
        prim_path: str,
        coordinate_system: COORDINATE_SYSTEM = "local",
        rotation_type: ROTATION_TYPES = "cartesian",
    ) -> Pose:
        prim = PrimUtils.get_prim(prim_path)
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
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
            xform = UsdGeom.Xformable(prim)
            time = Usd.TimeCode.Default()
            transformation: Gf.Matrix4d = (
                xform.ComputeLocalToWorldTransform(time)
                if coordinate_system == "world"
                else xform.GetLocalTransformation()
            )

            # Orthnormalize the transformation matrix to avoid scaling issues
            if not transformation.Orthonormalize():
                carb.log_warn(
                    f"Transform for prim {prim.GetPath()} orthonormalize failed."
                )
            position = np.array(transformation.ExtractTranslation())
            orientation = transformation.ExtractRotation().GetQuaternion()
            w, (x, y, z) = orientation.GetReal(), orientation.GetImaginary()
            quat = np.array([w, x, y, z])
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
            )

        rotation = (
            Rotation.from_quat(quat[[1, 2, 3, 0]]).as_rotvec()
            if rotation_type == "cartesian"
            else quat
        )
        pose = (
            (position / SceneUtils.get_stage_units()) * 1000
        ).tolist() + rotation.tolist()

        return (
            WSPose(pose=pose) if rotation_type == "cartesian" else QuatPose(pose=pose)
        )

    def set_prim_pose(prim_path: str, input_pose: WSPose) -> None:
        position = tuple(each / 1000 for each in input_pose.pose[:3])
        rot = tuple(input_pose.pose[3:])
        rotation = Rotation.from_rotvec(rot).as_quat().tolist()
        rotation.insert(0, rotation.pop())

        prim = PrimUtils.get_prim(prim_path)
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            prim = RigidPrim(prim_path)
            prim.set_local_pose(position, rotation)
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
        trans = np.array(pose[:3])
        rot = Rotation.from_rotvec(pose[3:])
        mat = np.eye(4)
        mat[:3, :3] = rot.as_matrix()
        mat[:3, 3] = trans
        return mat

    @staticmethod
    def matrix_to_pose(mat: np.ndarray) -> np.ndarray:
        trans = mat[:3, 3]
        rot = Rotation.from_matrix(mat[:3, :3])
        return np.concatenate([trans, rot.as_rotvec()])

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
            quat = Rotation.from_rotvec(result_pose[3:]).as_quat()
            return QuatPose(pose=result_pose[:3].tolist() + quat.tolist())

    def set_relative_pose(
        prim_path: str, relative_pose: WSPose, object_first: bool = False
    ) -> None:
        current_pose = PrimUtils.get_prim_pose(prim_path, rotation_type="cartesian")
        current_translation = np.array(current_pose.pose[:3])
        relative_translation = np.array(relative_pose.pose[:3])

        current_rotation = Rotation.from_rotvec(current_pose.pose[3:])
        relative_rotation = Rotation.from_rotvec(relative_pose.pose[3:])

        if object_first:
            new_translation = (current_translation - relative_translation).tolist()
            new_rotation = relative_rotation * current_rotation
        else:
            new_translation = (current_translation + relative_translation).tolist()
            new_rotation = current_rotation * relative_rotation

        new_rotation_vec = new_rotation.as_rotvec().tolist()
        new_pose = new_translation + new_rotation_vec
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
