"""Service logic for converting prims into ghost objects.

Holds the non-UI work behind the ``Convert Poses to Ghost Objects`` window:
resolving the convertible selection, enumerating motion groups and their TCP
sources, matching the NOVA-configured TCP, and creating the ghost objects. Kept
separate from ``convert_pose_window.py`` so the window only deals with widgets.
"""

from __future__ import annotations

import carb
import isaacsim.core.utils.prims as prims_utils
import isaacsim.core.utils.stage as stage_utils
import omni.usd
from pxr import Sdf, Usd, UsdGeom

import wandelbots_api_client.v2 as wb_v2

from wandelbots.omni.datatypes import TCPSource
from wandelbots.omni.manipulators import get_motion_group_configuration_from_prim
from wandelbots.omni.manipulators.utils import get_scene_motion_group_prim_paths
from wandelbots.omni.usd import SchemaUtils, TcpUtils
from wandelbots.omni.utils.api import get_api_client_from_config
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.teaching import GhostObjectUtils


def is_nova_managed_prim(prim: Usd.Prim) -> bool:
    """True when *prim* is (or is nested inside) a robot/tool schema hierarchy.

    Covers motion-group roots and their links, tools, and TCPs — none of these
    should be eligible for a generic scene-marker conversion, since overwriting
    them in place would destroy part of the robot/tool setup.
    """
    return (
        SchemaUtils.find_parent_motion_group(prim) is not None
        or SchemaUtils.find_parent_tool(prim) is not None
        or TcpUtils.is_tcp(prim)
    )


def is_pose_prim(prim: Usd.Prim) -> bool:
    """True when *prim* is a Wandelbots POSE prim (custom data ``type == 'POSE'``).

    Deliberately narrower than ``PoseListManager.is_pose_prim`` (which also matches
    ghost objects): only genuine POSE prims can be converted.
    """
    if not prim or not prim.IsValid():
        return False
    custom_data = prim.GetCustomDataByKey("wandelbots")
    return bool(custom_data) and custom_data.get("type") == "POSE"


def is_convertible_prim(prim: Usd.Prim) -> bool:
    """True when *prim* can be converted to a ghost object.

    Any transformable prim (Xform, mesh, POSE prim, …) qualifies, since the ghost
    is created at the prim's transform. Ghost objects themselves are excluded — they
    are already ghosts and would otherwise be re-cloned.
    """
    if not prim or not prim.IsValid():
        return False
    if GhostObjectUtils.is_ghost_object(prim):
        return False
    return bool(UsdGeom.Xformable(prim))


def is_pose_convertible_prim(prim: Usd.Prim) -> bool:
    """True when *prim* can be tagged as a POSE.

    Any transformable prim that is neither a ghost object, already a POSE, nor
    part of a robot/tool schema hierarchy (motion groups, links, tools, TCPs) —
    the in-place conversion overwrites the prim's spec, which would destroy
    those NOVA-managed prims instead of just tagging a generic scene marker.
    """
    if not prim or not prim.IsValid():
        return False
    if (
        GhostObjectUtils.is_ghost_object(prim)
        or is_pose_prim(prim)
        or is_nova_managed_prim(prim)
    ):
        return False
    return bool(UsdGeom.Xformable(prim))


def convert_prims_to_poses(prim_paths: list[str]) -> int:
    """Convert each prim into a Wandelbots POSE gizmo. Returns the count converted.

    Mirrors the "Add Pose" flow (``pose_utils.create_pose_prim``): it embeds the
    gizmo so the prim is overridden to look like a normal pose, ensures the
    translate/orient/scale xformOps exist (the scale op keeps the transform and
    material intact when reparented into a scaled xform) and sets
    ``customData["wandelbots"]["type"] = "POSE"``. The prim's current local pose is
    preserved so the gizmo stays where the prim was. Ghost objects are excluded.
    """
    from wandelbots.omni.ui.tool.trajectory_planner.pose_utils import embed_gizmo

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return 0
    converted = 0
    for path in prim_paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid() or not is_pose_convertible_prim(prim):
            continue
        try:
            # Keep the prim where it is: capture its local pose before the gizmo
            # spec replaces the prim's content.
            local_pose = PrimUtils.get_prim_pose(
                path, coordinate_system="local", stage=stage
            )
            # Override the prim with the gizmo (Sdf.CopySpec replaces the spec, so
            # it must run before the xformOps are (re)added below).
            embed_gizmo(stage, path)
            xform = UsdGeom.Xform.Get(stage, path) or UsdGeom.Xform.Define(stage, path)
            prim = xform.GetPrim()
            if not prim.HasAttribute("xformOp:translate"):
                xform.AddTranslateOp()
            if not prim.HasAttribute("xformOp:orient"):
                xform.AddOrientOp()
            if not prim.HasAttribute("xformOp:scale"):
                xform.AddScaleOp()
            PrimUtils.set_prim_pose(path, local_pose, stage=stage)
            prim.SetCustomDataByKey("wandelbots", {"type": "POSE"})
            prim.SetMetadata("kind", "assembly")
            converted += 1
        except Exception as exc:
            carb.log_error(f"Failed to convert prim '{path}' to pose: {exc}")
    return converted


class ConvertPoseService:
    """Stateless helpers used by ``ConvertPoseWindow``."""

    @staticmethod
    def resolve_convertible_prim_paths(payload: dict | None) -> list[str]:
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return []

        candidate_paths: list[str] = []
        if payload:
            for prim in payload.get("prim_list", []):
                candidate_paths.append(prim.GetPath().pathString)
        if not candidate_paths:
            candidate_paths = (
                omni.usd.get_context().get_selection().get_selected_prim_paths()
            )

        pose_paths: list[str] = []
        for path in candidate_paths:
            prim = stage.GetPrimAtPath(path)
            if prim and prim.IsValid() and is_convertible_prim(prim):
                pose_paths.append(path)
        return pose_paths

    @staticmethod
    def list_motion_group_paths() -> list[str]:
        return get_scene_motion_group_prim_paths(include_prims_without_api=False)

    @staticmethod
    def list_tcp_sources(mg_path: str) -> list[TCPSource]:
        """All TCP sources of the motion group's tools, de-duplicated and sorted.

        ``get_all_tcp_sources`` iterates a set (non-deterministic order); sorting by
        name keeps the default selection stable.
        """
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return []
        mg_prim = stage.GetPrimAtPath(mg_path)
        if not mg_prim or not mg_prim.IsValid():
            return []

        seen: set[str] = set()
        sources: list[TCPSource] = []
        for tool in SchemaUtils.list_motion_group_tools(mg_prim):
            for source in GhostObjectUtils.get_all_tcp_sources(tool):
                if source.prim_path not in seen:
                    seen.add(source.prim_path)
                    sources.append(source)
        sources.sort(key=lambda s: s.name)
        return sources

    @staticmethod
    async def match_nova_tcp_index(
        mg_prim: Usd.Prim | None, tcp_sources: list[TCPSource]
    ) -> int | None:
        """Index of the TCP source matching one configured for the motion group in
        NOVA (by name), mirroring the ghost-teaching toolbar's TCP matching. Returns
        ``None`` when nothing matches or the description can't be fetched."""
        if not mg_prim or not tcp_sources:
            return None
        mg_config = get_motion_group_configuration_from_prim(mg_prim)
        if not mg_config:
            return None
        msc = mg_config.motion_stream_configuration
        try:
            async with get_api_client_from_config(msc.get_api_configuration()) as api:
                desc = await wb_v2.MotionGroupApi(api).get_motion_group_description(
                    cell=msc.cell,
                    controller=msc.controller,
                    motion_group=msc.motion_group,
                )
        except Exception as exc:
            carb.log_warn(f"Convert: could not fetch motion group TCPs: {exc}")
            return None
        nova_tcps = getattr(desc, "tcps", None) or {}
        if not nova_tcps:
            return None

        def _nova_name(name: str) -> str:
            return name[4:] if name.lower().startswith("tcp_") else name

        return next(
            (
                i
                for i, src in enumerate(tcp_sources)
                if src.name in nova_tcps or _nova_name(src.name) in nova_tcps
            ),
            None,
        )

    @staticmethod
    def create_ghost_override(
        stage: Usd.Stage,
        pose_path: str,
        tcp_prim: Usd.Prim,
        tool_prim: Usd.Prim,
    ) -> str | None:
        """Convert the pose at *pose_path* into a ghost object **in place**.

        The pose prim is replaced by the ghost at the same path/name (no ``_go``
        sibling). Returns the ghost prim path (== ``pose_path``) on success, or
        ``None`` on failure. The returned ghost serves as the reusable template for
        :meth:`copy_ghost_to_pose` (the merged mesh/material/TCP link are identical
        for every pose in the batch, so only this first one is built from scratch).
        """
        tmp_path = stage_utils.get_next_free_path(f"{pose_path}_convert_tmp")
        try:
            pose_prim = stage.GetPrimAtPath(pose_path)
            if not pose_prim or not pose_prim.IsValid():
                carb.log_warn(f"Pose prim '{pose_path}' is no longer valid.")
                return None
            pose_local = PrimUtils.get_prim_pose(
                pose_path, coordinate_system="local", stage=stage
            )
            world_pose = PrimUtils.get_prim_pose(
                pose_path, coordinate_system="world", stage=stage
            )
            preferred_joint_values = GhostObjectUtils.get_preferred_joint_values(
                pose_prim
            )

            # Build the ghost at a scratch path first — a failure here (e.g. no
            # TCP source found) must not delete the pose it would have replaced.
            GhostObjectUtils.add_ghost_object(
                source_prim=tool_prim,
                tcp_world_pose=world_pose,
                target_path=tmp_path,
                tcp_prim=tcp_prim,
            )
            tmp_prim = stage.GetPrimAtPath(tmp_path)
            if not tmp_prim or not GhostObjectUtils.is_ghost_object(tmp_prim):
                carb.log_error(f"Failed to build ghost object for pose '{pose_path}'.")
                return None

            # Only now overwrite the pose: CopySpec replaces the destination
            # spec (including stale children/customData) in one step, so the
            # original is never left deleted without a replacement.
            layer = stage.GetEditTarget().GetLayer()
            if not Sdf.CopySpec(layer, Sdf.Path(tmp_path), layer, Sdf.Path(pose_path)):
                carb.log_error(
                    f"Failed to move ghost object into place for pose '{pose_path}'."
                )
                return None
            # The ghost origin is the TCP; reuse the pose's local transform so the
            # TCP coincides with where the pose was.
            PrimUtils.set_prim_pose(pose_path, pose_local, stage=stage)
            if preferred_joint_values is not None:
                GhostObjectUtils.set_preferred_joint_values(
                    stage.GetPrimAtPath(pose_path), preferred_joint_values
                )
            return pose_path
        except Exception as exc:
            carb.log_error(
                f"Failed to convert pose '{pose_path}' to ghost object: {exc}"
            )
            return None
        finally:
            if stage.GetPrimAtPath(tmp_path).IsValid():
                prims_utils.delete_prim(tmp_path)

    @staticmethod
    def copy_ghost_to_pose(
        stage: Usd.Stage, template_path: str, pose_path: str
    ) -> bool:
        """Override the pose at *pose_path* with a copy of the *template_path* ghost.

        Reuses the already-merged ghost (mesh + ``Looks`` material +
        ``GhostObjectAPI``/``SourceTcpRel``) via ``Sdf.CopySpec`` instead of
        re-merging the tool meshes, then re-applies the pose's local transform.
        """
        try:
            pose_prim = stage.GetPrimAtPath(pose_path)
            if not pose_prim or not pose_prim.IsValid():
                carb.log_warn(f"Pose prim '{pose_path}' is no longer valid.")
                return False
            pose_local = PrimUtils.get_prim_pose(
                pose_path, coordinate_system="local", stage=stage
            )
            preferred_joint_values = GhostObjectUtils.get_preferred_joint_values(
                pose_prim
            )

            # CopySpec replaces the destination spec in place (including stale
            # children/customData), so the pose is only ever overwritten once the
            # copy has actually succeeded — it is never deleted up front, which
            # would otherwise lose the pose if the copy failed.
            layer = stage.GetEditTarget().GetLayer()
            if not Sdf.CopySpec(
                layer, Sdf.Path(template_path), layer, Sdf.Path(pose_path)
            ):
                carb.log_warn(
                    f"Failed to copy ghost template '{template_path}' to '{pose_path}'."
                )
                return False
            PrimUtils.set_prim_pose(pose_path, pose_local, stage=stage)
            # CopySpec above also copied the template's own preferredJointValues
            # (if any). Restore this pose's own preference, or clear the
            # inherited template value if it didn't have one — otherwise every
            # pose without a manual selection would silently inherit the first
            # pose's preferred config.
            if preferred_joint_values is not None:
                GhostObjectUtils.set_preferred_joint_values(
                    stage.GetPrimAtPath(pose_path), preferred_joint_values
                )
            else:
                GhostObjectUtils.clear_preferred_joint_values(
                    stage.GetPrimAtPath(pose_path)
                )
            return True
        except Exception as exc:
            carb.log_error(f"Failed to copy ghost to pose '{pose_path}': {exc}")
            return False
