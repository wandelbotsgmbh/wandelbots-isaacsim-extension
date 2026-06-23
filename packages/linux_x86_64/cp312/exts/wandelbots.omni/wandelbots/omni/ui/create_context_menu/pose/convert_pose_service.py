"""Service logic for converting prims into ghost objects.

Holds the non-UI work behind the ``Convert Poses to Ghost Objects`` window:
resolving the convertible selection, enumerating motion groups and their TCP
sources, matching the NOVA-configured TCP, and creating the ghost objects. Kept
separate from ``convert_pose_window.py`` so the window only deals with widgets.
"""

from __future__ import annotations

import carb
import isaacsim.core.utils.stage as stage_utils
import omni.usd
from pxr import Usd, UsdGeom

import wandelbots_api_client.v2 as wb_v2

from wandelbots.omni.datatypes import TCPSource
from wandelbots.omni.manipulators import get_motion_group_configuration_from_prim
from wandelbots.omni.manipulators.utils import get_scene_motion_group_prim_paths
from wandelbots.omni.usd import SchemaUtils
from wandelbots.omni.utils.api import get_api_client_from_config
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.teaching import GhostObjectUtils


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
    def create_ghost_for_pose(
        stage: Usd.Stage,
        pose_path: str,
        tcp_prim: Usd.Prim,
        tool_prim: Usd.Prim,
    ) -> bool:
        """Create one ghost object aligned to *pose_path*. Returns True on success."""
        try:
            pose_prim = stage.GetPrimAtPath(pose_path)
            if not pose_prim or not pose_prim.IsValid():
                carb.log_warn(f"Pose prim '{pose_path}' is no longer valid.")
                return False
            pose_parent_path = pose_prim.GetParent().GetPath().pathString
            # Name the ghost after the pose it was created from ("<pose>_go") and
            # place it as a sibling right next to that pose prim, instead of the
            # default location/name under the tool's hierarchy.
            pose_name = pose_prim.GetName()
            target_path = stage_utils.get_next_free_path(
                f"{pose_parent_path}/{pose_name}_go"
            )

            world_pose = PrimUtils.get_prim_pose(
                pose_path, coordinate_system="world", stage=stage
            )
            GhostObjectUtils.add_ghost_object(
                source_prim=tool_prim,
                tcp_world_pose=world_pose,
                target_path=target_path,
                tcp_prim=tcp_prim,
            )
            # The ghost prim's origin is the TCP, so giving it the same local
            # transform as the pose prim (its sibling) makes the TCP coincide with
            # the pose and keeps both relative transforms identical.
            pose_local = PrimUtils.get_prim_pose(
                pose_path, coordinate_system="local", stage=stage
            )
            PrimUtils.set_prim_pose(target_path, pose_local, stage=stage)
            return True
        except Exception as exc:
            carb.log_error(
                f"Failed to convert pose '{pose_path}' to ghost object: {exc}"
            )
            return False
