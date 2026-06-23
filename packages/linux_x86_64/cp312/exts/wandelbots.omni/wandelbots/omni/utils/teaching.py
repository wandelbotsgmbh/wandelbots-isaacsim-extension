import weakref
from typing import Callable, cast

import carb
import numpy as np
import isaacsim.core.utils.prims as prims_utils
import isaacsim.core.utils.stage as stage_utils
import omni.client
import omni.kit.commands
import omni.usd
import omni.usd.commands
from omni.kit.property.usd import prim_selection_payload
from omni.usd.commands import DeletePrimsCommand
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

import wandelbots.usd as wb_schema  # type: ignore
from wandelbots.omni.datatypes import (
    GHOST_MATERIAL_MDL_EXT_FILE,
    GHOST_MATERIAL_MDL_PROJECT_FILE,
    SHADER_IDENTIFIER,
    GhostObject,
    GhostObjectSource,
    Pose,
    TCPSource,
    WSPose,
)
from wandelbots.omni.manipulators.utils import get_link_0_from_motion_group_prim
from wandelbots.omni.usd import SchemaUtils, TcpUtils
from wandelbots.omni.utils.mesh import MeshUtils
from wandelbots.omni.utils.prims import PrimPoseWatcher, PrimUtils, RelativePoseMode


CARB_SETTINGS_PREFIX = "/persistent/exts/wandelbots.omni/ghost_teaching"

PREFERRED_JOINT_VALUES_ATTR = "preferredJointValues"


class GhostObjectUtils:
    @staticmethod
    def refresh_all_ghost_objects_material():
        ghost_object: GhostObject
        for ghost_object in GhostObjectUtils.get_ghost_objects():
            prim = stage_utils.get_current_stage().GetPrimAtPath(ghost_object.prim_path)
            if prim:
                GhostObjectUtils.refresh_ghost_material(prim)

    @staticmethod
    def refresh_ghost_material(prim: Usd.Prim):
        omni.usd.commands.DeletePrimsCommand(
            [prim.GetPath().pathString + "/Looks"], destructive=False
        ).do()
        GhostObjectUtils.add_material_to_prim(prim)

    @staticmethod
    def add_material_to_prim(prim: Usd.Prim):
        """
        Applies ghost material and shader to the prim given its path
        """

        stage_url = omni.usd.get_context().get_stage_url()
        copy_destination = omni.client.combine_urls(
            stage_url,
            GHOST_MATERIAL_MDL_PROJECT_FILE.as_posix(),
        )
        copy_result = omni.client.copy_file(
            GHOST_MATERIAL_MDL_EXT_FILE.as_posix(),
            copy_destination,
            behavior=omni.client.CopyBehavior.ERROR_IF_EXISTS,
        )
        relative_url = omni.client.make_relative_url(stage_url, copy_destination)
        carb.log_verbose(f"Copy ghost material result: {copy_result} at {relative_url}")

        stage = prim.GetStage()
        looks_path = prim.GetPath().AppendChild("Looks")
        if not stage.GetPrimAtPath(looks_path):
            stage.DefinePrim(looks_path, "Scope")

        material_path = looks_path.AppendChild("GhostMaterial")
        material = UsdShade.Material.Define(stage, material_path)

        shader_path = material_path.AppendChild("Shader")
        shader = UsdShade.Shader.Define(stage, shader_path)
        shader.SetSourceAsset(relative_url, "mdl")
        shader.SetSourceAssetSubIdentifier(SHADER_IDENTIFIER)

        material.CreateSurfaceOutput().ConnectToSource(
            shader.ConnectableAPI(), "surface"
        )
        material.CreateDisplacementOutput().ConnectToSource(
            shader.ConnectableAPI(), "displacement"
        )
        material.CreateVolumeOutput().ConnectToSource(shader.ConnectableAPI(), "volume")
        UsdShade.MaterialBindingAPI(prim).Bind(
            material, UsdShade.Tokens.strongerThanDescendants
        )

    @staticmethod
    def is_ghost_object(prim: Usd.Prim) -> bool:
        return prim.HasAPI(wb_schema.GhostObjectAPI)

    @staticmethod
    def register_ghost_object(prim: Usd.Prim, tcp_prim: Usd.Prim):
        """
        Registers custom data to ghost object to make them discoverable
        """
        if prim.HasAPI(wb_schema.ToolAPI):
            if not prim.RemoveAPI(wb_schema.ToolAPI):
                raise RuntimeError(f"Failed to remove ToolAPI from {prim.GetPath()}")
        if not prim.ApplyAPI(wb_schema.GhostObjectAPI):
            raise RuntimeError(f"Failed to apply GhostObjectAPI to {prim.GetPath()}")
        ghost_object_api = wb_schema.GhostObjectAPI.Get(prim.GetStage(), prim.GetPath())
        ghost_object_api.GetSourceTcpRel().AddTarget(tcp_prim.GetPath())

    @staticmethod
    def get_ghost_object_sources() -> list[GhostObjectSource]:
        return [
            GhostObjectSource(
                prim_path=prim.GetPrimPath().pathString, name=prim.GetName()
            )
            for prim in stage_utils.traverse_stage()
            if prim.HasAPI(wb_schema.ToolAPI)
        ]

    def add_ghost_object(
        source_prim: Usd.Prim,
        tcp_world_pose: WSPose,
        target_path: str = None,
        tcp_prim: Usd.Prim = None,
    ):
        source_parent_prim = source_prim.GetParent()

        carb.log_info(
            f"Add ghost object {source_prim.GetPath()} ref_world_pose={tcp_world_pose}"
        )

        clone_prim_path = f"{source_parent_prim.GetPath().pathString}/poses/{source_prim.GetName()}_Pose"
        if target_path is None:
            target_path = stage_utils.get_next_free_path(clone_prim_path)

        tcp_sources: list[TCPSource] = GhostObjectUtils.get_all_tcp_sources(source_prim)
        if len(tcp_sources) == 0:
            carb.log_error(
                f"Failed to add ghost object {source_prim.GetPath()} because no TCP source was found."
            )
            return

        if tcp_prim:
            tcp_sources_filtered = [
                source
                for source in tcp_sources
                if source.prim_path == tcp_prim.GetPath().pathString
            ]
            if len(tcp_sources_filtered) == 0:
                carb.log_error(
                    f"Failed to add ghost object {source_prim.GetPath()} because TCP prim {tcp_prim.GetPath()} was not found in available TCP sources."
                )
                return
            if len(tcp_sources_filtered) > 1:
                carb.log_warn(
                    f"Found multiple TCP sources {[tcp.prim_path for tcp in tcp_sources_filtered]} for ghost object {source_prim.GetPath()} with TCP prim {tcp_prim.GetPath()}. Using the first one."
                )
            tcp_source: TCPSource = tcp_sources_filtered[0]
        else:
            if len(tcp_sources) > 1:
                carb.log_warn(
                    f"Found multiple TCP sources {[tcp.prim_path for tcp in tcp_sources]} for ghost object {source_prim.GetPath()}. Using the first one."
                )
            tcp_source = tcp_sources[0]

        ghost_prim: Usd.Prim = GhostObjectUtils._convert_prim_to_ghost_prim(
            source_prim=source_prim,
            tcp_prim_path=tcp_source.prim_path,
            target_path=target_path,
        )

        # set ghost object to active TCP pose

        if tcp_world_pose:
            parent = ghost_prim.GetParent()
            local_pose = PrimUtils.get_relative_pose(
                PrimUtils.get_prim_pose(
                    parent.GetPrimPath().pathString, coordinate_system="world"
                ),
                tcp_world_pose,
                mode=RelativePoseMode.NORMAL,
            )
            carb.log_verbose(
                f"Setting ghost object {ghost_prim.GetPath()} local_pose={local_pose} world_pose={tcp_world_pose} parent_pose={PrimUtils.get_prim_pose(parent.GetPrimPath().pathString)}"
            )

            PrimUtils.set_prim_pose(ghost_prim.GetPrimPath().pathString, local_pose)

    def get_linked_motion_group_to_ghost_object_prim(
        ghost_prim: Usd.Prim,
    ) -> Usd.Prim | None:
        """
        Get the motion group linked to the ghost object prim.
        """
        stage: Usd.Stage = ghost_prim.GetStage()
        if not GhostObjectUtils.is_ghost_object(ghost_prim):
            carb.log_error(
                f"Ghost prim {ghost_prim.GetPath()} does not have GhostObjectAPI."
            )
            return None

        ghost_object_api = wb_schema.GhostObjectAPI.Get(stage, ghost_prim.GetPath())
        tcp_prim_paths: list[Sdf.Path] = (
            ghost_object_api.GetSourceTcpRel().GetForwardedTargets()
        )
        if len(tcp_prim_paths) == 0:
            carb.log_error(f"Ghost prim {ghost_prim.GetPath()} has no linked TCP.")
            return None
        if len(tcp_prim_paths) > 1:
            carb.log_warn(
                f"Ghost prim {ghost_prim.GetPath()} has multiple linked TCPs: {tcp_prim_paths}. Using the first one."
            )
        tcp_prim: Usd.Prim = stage.GetPrimAtPath(tcp_prim_paths[0])
        flange_tcp = SchemaUtils.get_flange_tcp_from_tool_tcp(tcp_prim)
        if not flange_tcp:
            carb.log_error(
                f"Failed to find flange TCP for ghost object {ghost_prim.GetPath()} with TCP {tcp_prim.GetPath()}. Cannot find motion group."
            )
            return None
        return SchemaUtils.find_parent_motion_group(flange_tcp)

    def get_ghost_object_from_prim(
        ghost_prim: Usd.Prim, relative_to_prim: str = None
    ) -> GhostObject | None:
        if not GhostObjectUtils.is_ghost_object(ghost_prim):
            return None

        path = ghost_prim.GetPrimPath().pathString
        robot_prim = GhostObjectUtils.get_linked_motion_group_to_ghost_object_prim(
            ghost_prim
        )
        if not robot_prim:
            carb.log_verbose(f"Robot prim for ghost object {path} not found.")
            return None
        robot_prim_path = robot_prim.GetPath().pathString
        ws_pose = PrimUtils.get_relative_prim_pose(
            relative_to_prim if relative_to_prim else robot_prim_path, path
        )

        return GhostObject(
            name=ghost_prim.GetName(),
            prim_path=ghost_prim.GetPath().pathString,
            robot_prim_path=robot_prim_path,
            pose=ws_pose,
            preferred_joint_values=GhostObjectUtils.get_preferred_joint_values(
                ghost_prim
            ),
        )

    def get_ghost_objects(relative_to_prim: str = None) -> list[GhostObject]:
        ghost_objects: list[GhostObject] = []
        prim: Usd.Prim

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return []

        for prim in stage_utils.traverse_stage():
            ghost_prim = GhostObjectUtils.get_ghost_object_from_prim(
                prim, relative_to_prim=relative_to_prim
            )

            if not ghost_prim:
                continue
            ghost_objects.append(ghost_prim)
        return ghost_objects

    def delete_ghost_objects(prim_paths: list[str]) -> None:
        ghost_paths = {
            ghost.prim_path for ghost in GhostObjectUtils.get_ghost_objects()
        }
        valid_paths = ghost_paths.intersection(prim_paths)
        for path in valid_paths:
            prims_utils.delete_prim(path)

    def remove_physics_attributes(prim):
        # If this is a joint prim, remove it and stop further recursion
        if prim.GetTypeName() in ["PhysicsPrismaticJoint", "PhysicsFixedJoint"]:
            DeletePrimsCommand([prim.GetPath()], destructive=False).do()
            return

        # Delete all configured physics APIs
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            prim.RemoveAPI(UsdPhysics.RigidBodyAPI)
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            prim.RemoveAPI(UsdPhysics.CollisionAPI)

        # Recurse into child prims
        for child in prim.GetChildren():
            GhostObjectUtils.remove_physics_attributes(child)

    def get_all_tcp_sources(tool_prim: Usd.Prim = None) -> list[TCPSource]:
        """
        Return the prim paths of all tcps that are defined in the scene which follows a strict predicate `tcp_` or 'TCP_'
        """

        source_tcp_prims: list[Usd.Prim] = []

        if tool_prim:
            source_tcp_prims += prims_utils.get_all_matching_child_prims(
                tool_prim.GetPath().pathString,
                lambda prim_path: TcpUtils.is_tcp(
                    tool_prim.GetStage().GetPrimAtPath(prim_path)
                ),
            )
        else:
            stage: Usd.Stage = omni.usd.get_context().get_stage()
            for source_prim in GhostObjectUtils.get_ghost_object_sources():
                source_tcp_prims += prims_utils.get_all_matching_child_prims(
                    source_prim.prim_path,
                    lambda prim_path: TcpUtils.is_tcp(stage.GetPrimAtPath(prim_path)),
                )

        tcp_sources = []
        for prim in set(source_tcp_prims):
            name = str(prim.GetPath()).rsplit("/", 1)[-1]
            prim_path = str(prim.GetPath())

            flange_prim: Usd.Prim = SchemaUtils.get_flange_tcp_from_tool_tcp(prim)
            if not flange_prim:
                carb.log_warn(f"Failed to find flange TCP for {prim_path}. Skipping.")
                continue
            rel_pose = PrimUtils.get_relative_prim_pose(
                prim_path, flange_prim.GetPrimPath().pathString
            )
            tcp_sources.append(
                TCPSource(name=name, prim_path=prim_path, value=rel_pose, mode="normal")
            )
        return tcp_sources

    def _convert_prim_to_ghost_prim(
        source_prim: Usd.Prim, tcp_prim_path: str, target_path: str
    ) -> Usd.Prim:
        stage: Usd.Stage = source_prim.GetStage()

        tcp_transform: Gf.Matrix4d = omni.usd.get_world_transform_matrix(
            stage.GetPrimAtPath(tcp_prim_path)
        ).GetOrthonormalized()
        source_transform = omni.usd.get_world_transform_matrix(
            source_prim
        ).GetOrthonormalized()
        relative_tcp_transform: Gf.Matrix4d = (
            source_transform * tcp_transform.GetInverse()
        )
        relative_transform: Gf.Matrix4d = (
            relative_tcp_transform.GetInverse() * source_transform
        )

        ghost_mesh_prim: UsdGeom.Mesh = MeshUtils.merge_prim_meshes(
            source_prim=source_prim,
            target_path=target_path,
            mesh_offset_transform=relative_transform.GetInverse(),
        )

        omni.kit.commands.execute(
            "AddXformOp",
            payload=prim_selection_payload.PrimSelectionPayload(
                weakref.ref(stage), paths=[ghost_mesh_prim.GetPath()]
            ),
            precision=UsdGeom.XformOp.PrecisionDouble,
            rotation_order="ZYX",
            add_translate_op=True,
            add_rotate_xyz_op=False,  # we are using orient_op since this is what set_prim_pose uses
            add_orient_op=True,
            add_scale_op=True,
            add_transform_op=False,
            add_pivot_op=False,
        )

        # add ghost material to object
        GhostObjectUtils.add_material_to_prim(ghost_mesh_prim.GetPrim())

        # register prim
        tcp_prim = stage.GetPrimAtPath(tcp_prim_path)
        GhostObjectUtils.register_ghost_object(ghost_mesh_prim.GetPrim(), tcp_prim)

        return ghost_mesh_prim.GetPrim()

    def get_ghost_object_tcp_offset(ghost_prim: Usd.Prim) -> WSPose | None:
        stage: Usd.Stage = ghost_prim.GetStage()
        if not GhostObjectUtils.is_ghost_object(ghost_prim):
            carb.log_error(
                f"Ghost prim {ghost_prim.GetPath()} does not have GhostObjectAPI."
            )
            return None

        ghost_object_api = wb_schema.GhostObjectAPI.Get(stage, ghost_prim.GetPath())
        tcp_prim_paths: list[Sdf.Path] = (
            ghost_object_api.GetSourceTcpRel().GetForwardedTargets()
        )
        if len(tcp_prim_paths) == 0:
            carb.log_error(f"Ghost prim {ghost_prim.GetPath()} has no linked TCP.")
            return None
        if len(tcp_prim_paths) > 1:
            carb.log_warn(
                f"Ghost prim {ghost_prim.GetPath()} has multiple linked TCPs: {tcp_prim_paths}. Using the first one."
            )
        tcp_prim: Usd.Prim = stage.GetPrimAtPath(tcp_prim_paths[0])
        flange_tcp = SchemaUtils.get_flange_tcp_from_tool_tcp(tcp_prim)
        if not flange_tcp:
            carb.log_error(
                f"Failed to find flange TCP for ghost object {ghost_prim.GetPath()} with TCP {tcp_prim.GetPath()}."
            )
            return None
        return PrimUtils.get_relative_prim_pose(
            flange_tcp.GetPrimPath().pathString,
            tcp_prim.GetPrimPath().pathString,
        )

    def get_ghost_object_flange_pose(
        ghost_prim: Usd.Prim, tcp_world_pose: WSPose
    ) -> WSPose | None:
        """Return the flange world pose for a ghost object positioned at tcp_world_pose.

        Ghost object prims are placed at the TCP target pose. This computes the
        corresponding flange pose: flange_target = tcp_world @ inv(flange_to_tcp_offset).
        Returns None if the TCP/flange chain cannot be resolved.
        """
        tcp_offset = GhostObjectUtils.get_ghost_object_tcp_offset(ghost_prim)
        if tcp_offset is None:
            return None
        # get_ghost_object_tcp_offset returns get_relative_prim_pose(flange_path, tcp_path)
        # = inv(flange_world) @ tcp_world. We need inv(tcp_world) @ flange_world, the matrix inverse.
        flange_to_tcp_mat = PrimUtils.pose_to_matrix(tcp_offset.pose)
        tcp_to_flange_mat = np.linalg.inv(flange_to_tcp_mat)
        ghost_mat = PrimUtils.pose_to_matrix(tcp_world_pose.pose)
        return WSPose(
            pose=PrimUtils.matrix_to_pose(ghost_mat @ tcp_to_flange_mat).tolist()
        )

    def create_ghost_object_pose_watcher(
        ghost_object_prim: Usd.Prim, pose_changed_fn: Callable[[Pose], None]
    ):
        motion_group_prim = (
            GhostObjectUtils.get_linked_motion_group_to_ghost_object_prim(
                ghost_object_prim
            )
        )

        if not motion_group_prim:
            carb.log_verbose(
                f"Could not find motion group prim linked to ghost object at {ghost_object_prim.GetPath()}"
            )
            return None

        relative_prim = get_link_0_from_motion_group_prim(motion_group_prim)

        return PrimPoseWatcher(
            prim=ghost_object_prim,
            pose_changed_fn=pose_changed_fn,
            relative_prim=relative_prim,
        )

    @staticmethod
    def get_selected_ghost_object_from_scene(
        ghost_objects: dict[str, GhostObject],
    ) -> GhostObject | None:
        selection = cast(omni.usd.Selection, omni.usd.get_context().get_selection())
        stage: Usd.Stage = omni.usd.get_context().get_stage()
        selected_prim_paths = selection.get_selected_prim_paths()
        if len(selected_prim_paths) == 1 and stage:
            selected_prim = stage.GetPrimAtPath(selected_prim_paths[0])
            if selected_prim and GhostObjectUtils.is_ghost_object(selected_prim):
                prim_path = selected_prim.GetPath().pathString
                return ghost_objects.get(prim_path, None)
        return None

    @staticmethod
    def get_preferred_joint_values(ghost_object_prim: Usd.Prim) -> list[float] | None:
        """Read ``preferredJointValues`` from a ghost-object prim."""
        if not ghost_object_prim or not ghost_object_prim.IsValid():
            return None
        attr = ghost_object_prim.GetAttribute(PREFERRED_JOINT_VALUES_ATTR)
        if not attr or not attr.HasValue():
            return None
        return list(attr.Get())

    @staticmethod
    def find_preferred_config_index(
        joint_configs: list[list[float]],
        preferred: list[float],
        tolerance: float = 1e-4,
    ) -> int | None:
        """Return the index in *joint_configs* whose values match *preferred*
        within *tolerance* (per-joint absolute tolerance), or ``None`` if no match.
        """
        for i, cfg in enumerate(joint_configs):
            if len(cfg) == len(preferred) and all(
                abs(a - b) < tolerance for a, b in zip(cfg, preferred)
            ):
                return i
        return None

    @staticmethod
    def get_nova_tcp_name(ghost_prim: Usd.Prim) -> str | None:
        """Derive the candidate NOVA TCP name from a ghost object's source TCP prim.

        Strips the ``tcp_`` prefix from the linked TCP prim name to recover the
        NOVA TCP key (e.g. prim ``tcp_schunk`` → NOVA name ``"schunk"``).  Used
        as a tiebreaker when multiple NOVA TCPs share the same offset.
        """
        try:
            ghost_api = wb_schema.GhostObjectAPI.Get(
                ghost_prim.GetStage(), ghost_prim.GetPath()
            )
            targets = ghost_api.GetSourceTcpRel().GetForwardedTargets()
            if not targets:
                return None
            prim_name = targets[0].name
            if prim_name.startswith("tcp_"):
                return prim_name[4:]
        except Exception:
            pass
        return None


def make_ghost_tcp_matcher(ghost_prim: Usd.Prim) -> Callable[[dict], str | None]:
    """Return a TCP matcher callable for use with TcpSelector.

    Prefers the TCP explicitly linked on the ghost object (its source TCP prim
    name) whenever that name is a valid TCP of the motion group — that is the
    user-defined TCP and must be shown.  Falls back to matching by flange-relative
    TCP offset (pose) when no such name is available, using the source prim name to
    disambiguate ties.  Returns ``None`` when nothing matches, allowing the caller's
    fallback chain to proceed.
    """
    ghost_offset = GhostObjectUtils.get_ghost_object_tcp_offset(ghost_prim)
    candidate_name = GhostObjectUtils.get_nova_tcp_name(ghost_prim)

    _TOLERANCE = 1e-4

    def _pose_matches(nova_pose, ws_pose: WSPose) -> bool:
        pos = getattr(nova_pose, "position", None) or []
        ori = getattr(nova_pose, "orientation", None) or []
        if len(pos) != 3 or len(ori) != 3:
            return False
        return all(abs(a - b) < _TOLERANCE for a, b in zip(pos + ori, ws_pose.pose))

    def matcher(nova_tcps: dict) -> str | None:
        # The TCP explicitly linked on the ghost object is the user-defined TCP;
        # use it whenever it is a valid TCP of this motion group.
        if candidate_name is not None and candidate_name in nova_tcps:
            return candidate_name
        if ghost_offset is None:
            return candidate_name
        matches = [
            name
            for name, tcp_offset in nova_tcps.items()
            if hasattr(tcp_offset, "pose")
            and tcp_offset.pose is not None
            and _pose_matches(tcp_offset.pose, ghost_offset)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return candidate_name if candidate_name in matches else matches[0]
        return None

    return matcher


class RefreshGhostMaterialsCommand(omni.kit.commands.Command):
    def __init__(self) -> None:
        super().__init__()

    def do(self) -> None:
        GhostObjectUtils.refresh_all_ghost_objects_material()
        return

    def undo(self) -> None:
        return
