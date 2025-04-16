from typing import Literal

import numpy as np
import omni.isaac.core.utils.prims as prims_utils
import omni.usd
from wandelbots.omni.datatypes import (
    COORDINATE_SYSTEM,
    ROTATION_TYPES,
    CustomPrimData,
    Pose,
    QuatPose,
    WSPose,
)
from wandelbots.omni.environment import host_database
from omni.isaac.core.prims import RigidPrim
from omni.isaac.sensor import Camera
from pxr import Gf, Usd, UsdGeom, UsdPhysics
from scipy.spatial.transform import Rotation


class PrimUtils:
    @staticmethod
    def get_object(prim_path: str) -> Usd.Prim:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        if not prim:
            raise ValueError(f"Prim not found at path: {prim_path}")

        return prim

    @staticmethod
    def add_metadata(prim_path: str, metadata: CustomPrimData) -> None:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        if not prim:
            raise ValueError(f"Prim not found at path: {prim_path}")
        custom_data = prim.GetCustomData()
        custom_data["metadata"] = dict(metadata)
        prim.SetCustomData(custom_data)

    @staticmethod
    def remove_metadata(prim_path: str) -> None:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        if not prim:
            raise ValueError(f"Prim not found at path: {prim_path}")
        prim.SetCustomData({})

    @staticmethod
    def toggle_visibility(prim_path: str, visible: bool) -> None:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            raise ValueError(f"Prim not found at path: {prim_path}")
        attribute = prim.GetAttribute("visibility")
        attribute.Set("inherited" if visible else "invisible")

    @staticmethod
    def select_object(prim_path: str) -> None:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)

        if not prim.IsValid():
            raise ValueError(f"Prim not found at path: {prim_path}")

        # Get the selection context
        selection = omni.usd.get_context().get_selection()

        # Clear the current selection
        selection.clear_selected_prim_paths()

        # Add the prim to the selection
        selection.set_selected_prim_paths([prim_path], True)

    @staticmethod
    def toggle_collider(prim_path: str, enabled: bool) -> None:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            raise ValueError(f"Prim not found at path: {prim_path}")
        attribute = prim.GetAttribute("physics:collisionEnabled")
        attribute.Set(enabled)

    @staticmethod
    def toggle_joint(prim_path: str, enabled: bool) -> None:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            raise ValueError(f"Prim not found at {prim_path}.")
        attribute = prim.GetAttribute("physics:jointEnabled")
        attribute.Set(enabled)

    @staticmethod
    def get_pose(
        prim_path: str,
        coordinate_system: COORDINATE_SYSTEM = "local",
        rotation_type: ROTATION_TYPES = "cartesian",
    ) -> Pose:
        prim = PrimUtils.get_object(prim_path)
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            prim = RigidPrim(prim_path)
            position, quat = (
                prim.get_world_pose()
                if coordinate_system == "world"
                else prim.get_local_pose()
            )
        elif prim.GetTypeName() == "Camera":
            camera = Camera(prim.GetPrimPath().pathString)
            position, quat = (
                camera.get_world_pose()
                if coordinate_system == "world"
                else camera.get_local_pose()
            )
        else:
            xform = UsdGeom.Xformable(prim)
            time = Usd.TimeCode.Default()
            transformation = (
                xform.ComputeLocalToWorldTransform(time)
                if coordinate_system == "world"
                else xform.GetLocalTransformation()
            )
            position = np.array(transformation.ExtractTranslation())
            orientation = transformation.ExtractRotation().GetQuaternion()
            w, (x, y, z) = orientation.GetReal(), orientation.GetImaginary()
            quat = np.array([w, x, y, z])

        rotation = (
            Rotation.from_quat(quat[[1, 2, 3, 0]]).as_rotvec()
            if rotation_type == "cartesian"
            else quat
        )
        # stage_unit = get_stage_units()                               # disabled
        # pose = (position / stage_unit).tolist() + rotation.tolist()  # disabled
        pose = (position * 1000).tolist() + rotation.tolist()
        return (
            WSPose(pose=pose) if rotation_type == "cartesian" else QuatPose(pose=pose)
        )

    @staticmethod
    def set_pose(prim_path: str, input_pose: WSPose) -> None:
        position = tuple([each / 1000 for each in input_pose.pose[:3]])
        rot = tuple(input_pose.pose[3:])
        rotation = Rotation.from_rotvec(rot).as_quat().tolist()
        rotation.insert(0, rotation.pop())

        try:
            prim = PrimUtils.get_object(prim_path)
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                prim = RigidPrim(prim_path)
                prim.set_local_pose(position, rotation)
            elif prim.GetTypeName() == "Camera":
                camera = Camera(prim.GetPrimPath().pathString)
                camera.set_local_pose(position, rotation)
            else:
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

        except Exception as e:
            raise ValueError(
                f"Failed to set pose for prim at path: {prim_path}. Error: {e}"
            )

    @staticmethod
    def get_relative_pose(
        prim_path_1: str,
        prim_path_2: str,
        mode: Literal[
            "normal", "inverse_first", "inverse_second", "inverse_both"
        ] = "normal",
        rotation_type: ROTATION_TYPES = "cartesian",
    ) -> Pose:
        prim1_pose = PrimUtils.get_pose(prim_path_1, coordinate_system="world")
        prim2_pose = PrimUtils.get_pose(prim_path_2, coordinate_system="world")

        def pose_to_matrix(pose):
            trans = np.array(pose[:3])
            rot = Rotation.from_rotvec(pose[3:])
            mat = np.eye(4)
            mat[:3, :3] = rot.as_matrix()
            mat[:3, 3] = trans
            return mat

        def matrix_to_pose(mat):
            trans = mat[:3, 3]
            rot = Rotation.from_matrix(mat[:3, :3])
            return np.concatenate([trans, rot.as_rotvec()])

        prim1_matrix = pose_to_matrix(prim1_pose.pose)
        prim2_matrix = pose_to_matrix(prim2_pose.pose)

        if mode == "normal":  # prim1::prim2
            result_matrix = prim1_matrix @ prim2_matrix
        elif mode == "inverse_first":  # ~prim1::prim2
            result_matrix = np.linalg.inv(prim1_matrix) @ prim2_matrix
        elif mode == "inverse_second":  # prim1::~prim2
            result_matrix = prim1_matrix @ np.linalg.inv(prim2_matrix)
        elif mode == "inverse_both":  # ~prim1::~prim2
            result_matrix = np.linalg.inv(prim1_matrix) @ np.linalg.inv(prim2_matrix)
        else:
            raise ValueError(
                "Invalid mode. Choose from 'normal', 'inverse_first', 'inverse_second', or 'inverse_both'."
            )

        result_pose = matrix_to_pose(result_matrix)
        result_pose = np.round(result_pose, 3)

        if rotation_type == "cartesian":
            return WSPose(pose=result_pose.tolist())
        else:
            quat = Rotation.from_rotvec(result_pose[3:]).as_quat()
            return QuatPose(pose=result_pose[:3].tolist() + quat.tolist())

    @staticmethod
    def set_relative_pose(
        prim_path: str, relative_pose: WSPose, object_first: bool = False
    ) -> None:
        current_pose = PrimUtils.get_pose(prim_path, coordinate_system="cartesian")
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
        PrimUtils.set_pose(prim_path, WSPose(pose=new_pose))

    @staticmethod
    def reset_objects(prim_path: str) -> None:
        children_prims = prims_utils.get_all_matching_child_prims(
            prim_path, lambda _: True
        )
        if children_prims:
            for child_prim in children_prims:
                child_prim_path = child_prim.GetPrimPath().pathString
                if child_prim_path in host_database["default_poses"]:
                    default_pose = host_database[f"default_poses.{child_prim_path}"]
                    PrimUtils.set_pose(child_prim_path, default_pose)
                else:
                    raise ValueError(f"Default pose not set for {child_prim_path}")
        else:
            raise ValueError(f"No children found for {prim_path}")
