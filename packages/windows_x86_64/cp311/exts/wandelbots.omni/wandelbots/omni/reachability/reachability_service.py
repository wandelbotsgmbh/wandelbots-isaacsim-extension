"""Reachability analysis service for testing robot model reach to TCP poses."""

import asyncio
import json
from dataclasses import dataclass
from typing import Optional

import aiohttp
import carb
import omni.kit.notification_manager as nm
import omni.usd
import pydantic
from pxr import Usd

import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models
from wandelbots_api_client.v2.exceptions import NotFoundException, OpenApiException
import wandelbots.omni.core.collision.shapes as collision_shapes
from wandelbots.omni.core.collision.utils import to_nova_collider
from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVAInstance
from wandelbots.omni.manipulators.motion_group import (
    get_motion_group_configuration_from_prim,
)
from wandelbots.omni.reachability.model_base_offsets import MODEL_BASE_OFFSETS
from wandelbots.omni.utils.math import m_to_mm
from wandelbots.omni.utils.prims import PrimUtils

# The NOVA client wraps HTTP-level failures in OpenApiException, but transport
# failures (connection refused, dropped mid-request) surface as raw
# aiohttp.ClientError; a timeout comes from asyncio.wait_for instead.
_REQUEST_ERRORS = (OpenApiException, aiohttp.ClientError, asyncio.TimeoutError)


# Mechanical joint position limits per motion group model, parsed from the
# model's configuration transcript. Only successful lookups are cached so a
# transient fetch failure is retried on the next analysis run.
_model_joint_limits_cache: dict[str, list] = {}


def _extract_transcript_limit_values(
    transcript: list, motion_group_uid: int, service_description: str
) -> Optional[list[float]]:
    """Pull a per-joint values array out of a GCI transcript exchange."""
    for entry in transcript:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request") or {}
        service = request.get("service")
        if not isinstance(service, dict):
            continue
        if service.get("description") != service_description:
            continue
        if request.get("uid") != motion_group_uid:
            continue
        response = entry.get("response") or {}
        if (response.get("status") or {}).get("code") != "CODE_OK":
            continue
        values = ((response.get("data") or {}).get("joints") or {}).get("values")
        if isinstance(values, list):
            return [float(value) for value in values]
    return None


@dataclass
class ReachabilityResult:
    """Result of reachability analysis for a single robot model."""

    model_name: str
    reachable: bool
    reachable_count: int
    total_poses: int
    error: Optional[str] = None
    joint_solutions: Optional[list[list[float]]] = None
    all_joint_solutions: Optional[list[list[list[float]]]] = None
    # Vertical distance from the base plate to the first joint, in meters.
    base_height_meters: float = 0.0
    # Mounting pose of the base that reached each target, parallel to
    # joint_solutions. None for a single-base run.
    per_pose_mounting_poses: Optional[list[Optional[list[float]]]] = None
    # Attached tool mesh: triangle vertices in meters relative to the tool
    # root, for preview rendering only. Never sent to NOVA.
    tool_mesh_vertices: Optional[list[tuple[float, float, float]]] = None


@dataclass(frozen=True)
class TargetPose:
    """One analysis target: the TCP pose to reach and the name of the
    NOVA-stored collision setup it is checked against. None means the
    session's own static colliders."""

    pose: WSPose
    collision_setup_name: Optional[str] = None


@dataclass
class ReachabilitySession:
    """Holds the API context for an ongoing reachability analysis."""

    api_client: wb_v2.ApiClient
    kinematics_api: wb_v2.KinematicsApi
    models_api: wb_v2.MotionGroupModelsApi
    cell_id: str
    target_poses: list[TargetPose]
    nova_mounting_pose: object
    nova_tcp_offset: object
    # World-frame colliders for every pose without its own stored setup.
    static_colliders: Optional[dict] = None
    joint_position_limits: Optional[list] = None
    # Preview-only tool mesh, copied into every result of this session.
    tool_mesh_vertices: Optional[list[tuple[float, float, float]]] = None
    # Flange-frame colliders of the attached tool, applied to every pose. A
    # stored setup's own tool colliders are ignored, so what is checked
    # always matches the tool the user attached.
    tool_colliders: Optional[dict[str, wb_v2_models.Collider]] = None
    # Every setup name referenced by a target pose, resolved once.
    stored_collision_setups: Optional[dict[str, wb_v2_models.CollisionSetup]] = None

    def colliders_for_setup(self, setup_name: Optional[str]) -> Optional[dict]:
        """Static colliders a pose is checked against. An unresolved setup
        name yields none, never the default colliders."""
        if setup_name is None:
            return self.static_colliders
        stored = (self.stored_collision_setups or {}).get(setup_name)
        return stored.colliders if stored is not None else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await self.api_client.close()
        except _REQUEST_ERRORS as exc:
            carb.log_warn(f"Error closing API client: {exc}")
        return False


# Model name patterns that are positioners / turntables, not robot arms.
# The NOVA IK API does not support these and returns 500.
_NON_ARM_SUFFIXES = ("_IRT", "_TURN", "_POSITIONER")


def _is_robot_arm(model_name: str) -> bool:
    """Return True if the model is a robot arm (not a positioner/turntable)."""
    upper = model_name.upper()
    return not any(suffix in upper for suffix in _NON_ARM_SUFFIXES)


def _join_errors(errors: list[str]) -> Optional[str]:
    """One message from the collected errors, duplicates removed."""
    return "; ".join(dict.fromkeys(errors)) or None


def _robot_arm_models(raw_models: list[str]) -> list[str]:
    """Deduplicate the fetched model names and drop non-arm models."""
    seen: set[str] = set()
    arm_models: list[str] = []
    for model_name in raw_models:
        if model_name not in seen and _is_robot_arm(model_name):
            seen.add(model_name)
            arm_models.append(model_name)
    return arm_models


def _flange_frame_vertices(
    hull_meters: list[tuple[float, float, float]],
) -> tuple[list[list[float]], bool]:
    """Hull vertices in millimeters, with everything behind the flange plane
    projected onto it. Also reports whether anything was projected."""
    vertices: list[list[float]] = []
    clamped = False
    for point in hull_meters:
        vertex = [m_to_mm(value) for value in point]
        if vertex[2] < 0.0:
            vertex[2] = 0.0
            clamped = True
        vertices.append(vertex)
    return vertices, clamped


def attached_tool_colliders(
    tool_hulls_meters: Optional[list[list[tuple[float, float, float]]]],
) -> Optional[dict[str, wb_v2_models.Collider]]:
    """Convert the attached tool's convex hulls (meters, relative to the tool
    root, which mounts at the flange) into flange-frame NOVA colliders in
    millimeters. A hull of fewer than 4 points spans no volume and is skipped.

    Vertices behind the flange plane are projected onto it, because tool
    geometry overlapping the wrist counts as a self-collision in every
    configuration and would make every pose unreachable. A hull lying
    entirely behind the plane would flatten to a zero-volume plate and is
    dropped instead; the robot's own link model already covers that volume.
    """
    if not tool_hulls_meters:
        return None
    colliders: dict[str, wb_v2_models.Collider] = {}
    clamped_hulls = 0
    dropped_hulls = 0
    for index, hull in enumerate(tool_hulls_meters):
        if len(hull) < 4:
            continue
        vertices, clamped = _flange_frame_vertices(hull)
        clamped_hulls += 1 if clamped else 0
        if all(vertex[2] <= 0.0 for vertex in vertices):
            dropped_hulls += 1
            continue
        collider_id = f"attached_tool/{index}"
        colliders[collider_id] = to_nova_collider(
            collision_shapes.Collider(
                shape=collision_shapes.ConvexHull(vertices=vertices),
                pose=wb_v2_models.Pose(
                    position=[0.0, 0.0, 0.0], orientation=[0.0, 0.0, 0.0]
                ),
                prim_path=collider_id,
            )
        )
    if clamped_hulls or dropped_hulls:
        carb.log_warn(
            "Attached tool: geometry behind the flange plane was projected "
            f"onto it ({dropped_hulls} hull(s) entirely behind were dropped) "
            "- tool geometry overlapping the robot wrist would otherwise "
            "count as a permanent self-collision and make every pose "
            "unreachable."
        )
    return colliders or None


class ReachabilityService:
    """Service for analyzing which robot models can reach specified TCP poses."""

    @property
    def _stage(self) -> Usd.Stage:
        """Get the current USD stage."""
        return omni.usd.get_context().get_stage()

    def extract_mounting_pose_from_prim(self, prim_path: str) -> WSPose:
        """
        Extract world pose from a single prim to use as robot mounting position.

        Args:
            prim_path: Path to the prim representing the mounting position.

        Returns:
            WSPose object representing the mounting position.

        Raises:
            ValueError: If the prim is invalid or pose cannot be extracted.
        """
        try:
            pose = PrimUtils.get_prim_pose(
                prim_path=prim_path,
                coordinate_system="world",
                rotation_type="cartesian",
                stage=self._stage,
            )
            carb.log_info(f"Extracted mounting pose from {prim_path}: {pose}")
            return pose
        except Exception as exc:
            raise ValueError(
                f"Could not extract mounting pose from prim {prim_path}: {exc}"
            )

    def extract_target_poses_from_prims(self, prim_paths: list[str]) -> list[WSPose]:
        """
        Extract world poses from multiple prims to use as target TCP poses.

        Every prim's world pose is used verbatim - ghost objects included:
        the frame the user sees in the viewport IS the target. Any tool
        geometry enters solely via the request's ``tcp_offset`` (the
        window's TCP fields); without one, NOVA places the flange at the
        target frame.

        Args:
            prim_paths: List of prim paths representing target TCP positions.

        Returns:
            List of WSPose objects extracted from target prims.

        Raises:
            ValueError: If no valid poses can be extracted.
        """
        if not prim_paths:
            raise ValueError("No target prims provided")

        poses = []
        for prim_path in prim_paths:
            try:
                pose = PrimUtils.get_prim_pose(
                    prim_path=prim_path,
                    coordinate_system="world",
                    rotation_type="cartesian",
                    stage=self._stage,
                )
                poses.append(pose)
                carb.log_verbose(f"Extracted target pose from {prim_path}: {pose}")
            except Exception as exc:
                carb.log_warn(
                    f"Could not extract pose from target prim {prim_path}: {exc}"
                )
                continue

        if not poses:
            raise ValueError(
                "No valid poses could be extracted from target prims. "
                "Ensure selected prims are Xformable."
            )

        carb.log_info(f"Extracted {len(poses)} target poses")
        return poses

    @staticmethod
    async def _fetch_arm_model_names(
        models_api: wb_v2.MotionGroupModelsApi,
    ) -> list[str]:
        """Robot-arm motion group model names the instance offers."""
        raw_models: list[str] = await asyncio.wait_for(
            models_api.get_motion_group_models(), timeout=5.0
        )
        arm_models = _robot_arm_models(raw_models)
        carb.log_info(
            f"Found {len(raw_models)} motion group models, "
            f"using {len(arm_models)} robot arms"
        )
        return arm_models

    async def fetch_model_names(self, instance: NOVAInstance) -> list[str]:
        """All robot-arm motion group model names the instance offers, or []
        when the instance can't be reached - callers use this to seed
        manufacturer choices before any analysis has run."""
        api_client = get_instances_api().create_api_client_for_instance(instance)
        if api_client is None:
            return []
        try:
            async with api_client:
                return await self._fetch_arm_model_names(
                    wb_v2.MotionGroupModelsApi(api_client)
                )
        except _REQUEST_ERRORS as exc:
            carb.log_warn(f"Could not fetch motion group models: {exc}")
            return []

    async def prepare_session(
        self,
        instance: NOVAInstance,
        targets: list[TargetPose],
        mounting_pose: Optional[WSPose] = None,
        tcp_offset: Optional[WSPose] = None,
        static_colliders: Optional[dict] = None,
        tool_mesh_vertices: Optional[list[tuple[float, float, float]]] = None,
        tool_collider_hulls: Optional[list[list[tuple[float, float, float]]]] = None,
    ) -> tuple[ReachabilitySession, list[str]]:
        """Prepare an analysis session and return the session context + list of model names."""
        if not targets:
            raise ValueError("No target poses provided for reachability check")

        cell_id = await asyncio.wait_for(
            get_instances_api().fetch_primary_cell_id(instance), timeout=5.0
        )
        if not cell_id:
            raise ValueError(
                f"Instance '{instance.display_name}' has no cells available"
            )

        api_client = get_instances_api().create_api_client_for_instance(instance)
        if api_client is None:
            raise ValueError(f"Cannot connect to instance '{instance.display_name}'")

        models_api = wb_v2.MotionGroupModelsApi(api_client)
        all_models = await self._fetch_arm_model_names(models_api)

        if static_colliders:
            carb.log_info(
                f"Using {len(static_colliders)} scene colliders for collision checking"
            )
        if tool_mesh_vertices:
            carb.log_info(
                f"Using {len(tool_mesh_vertices) // 3} tool mesh triangle(s) for preview"
            )

        session = ReachabilitySession(
            api_client=api_client,
            kinematics_api=wb_v2.KinematicsApi(api_client),
            models_api=models_api,
            cell_id=cell_id,
            target_poses=targets,
            nova_mounting_pose=mounting_pose.to_nova_pose() if mounting_pose else None,
            nova_tcp_offset=tcp_offset.to_nova_pose() if tcp_offset else None,
            static_colliders=static_colliders,
            tool_mesh_vertices=tool_mesh_vertices,
            tool_colliders=attached_tool_colliders(tool_collider_hulls),
            stored_collision_setups=await self._resolve_stored_collision_setups(
                api_client, cell_id, targets
            ),
        )
        return session, all_models

    async def _resolve_stored_collision_setups(
        self,
        api_client: wb_v2.ApiClient,
        cell_id: str,
        targets: list[TargetPose],
    ) -> Optional[dict[str, wb_v2_models.CollisionSetup]]:
        """Fetch every distinct NOVA-stored collision setup the targets
        reference, once each. A setup that fails to fetch is left out, so its
        poses are checked without collision awareness rather than failing the
        whole analysis - but never silently: a notification names the dropped
        setups (a stale name from a previously selected instance is the
        typical cause).
        """
        names = {
            target.collision_setup_name
            for target in targets
            if target.collision_setup_name
        }
        if not names:
            return None

        store_api = wb_v2.StoreCollisionSetupsApi(api_client)
        resolved: dict[str, wb_v2_models.CollisionSetup] = {}
        for name in names:
            try:
                resolved[name] = await asyncio.wait_for(
                    store_api.get_stored_collision_setup(cell=cell_id, setup=name),
                    timeout=5.0,
                )
            except _REQUEST_ERRORS as exc:
                carb.log_warn(f"Could not fetch stored collision setup '{name}': {exc}")
        unresolved = sorted(names - set(resolved))
        if unresolved:
            nm.post_notification(
                "Collision setup(s) could not be fetched from this instance: "
                f"{', '.join(unresolved)}. The poses using them are checked "
                "WITHOUT collision awareness.",
                duration=8.0,
                status=nm.NotificationStatus.WARNING,
            )
        return resolved or None

    async def list_collision_setup_names(
        self, instance: NOVAInstance
    ) -> Optional[list[str]]:
        """List the collision setup names stored on NOVA for this instance's
        cell. Used by the UI to populate the per-pose override dropdown.

        Returns None when the list could NOT be determined (fetch failure,
        no reachable cell, no API client) so callers can keep their previous
        state instead of treating a transient outage like a genuinely empty
        store; an actual empty store returns []."""
        try:
            cell_id = await asyncio.wait_for(
                get_instances_api().fetch_primary_cell_id(instance), timeout=5.0
            )
            if not cell_id:
                return None

            api_client = get_instances_api().create_api_client_for_instance(instance)
            if api_client is None:
                return None
            try:
                return await asyncio.wait_for(
                    wb_v2.StoreCollisionSetupsApi(
                        api_client
                    ).list_stored_collision_setups_keys(cell=cell_id),
                    timeout=5.0,
                )
            finally:
                await api_client.close()
        except _REQUEST_ERRORS as exc:
            carb.log_warn(f"Could not list stored collision setups: {exc}")
            return None

    @staticmethod
    def _group_pose_indices(
        session: ReachabilitySession,
    ) -> list[tuple[list[int], Optional[dict]]]:
        """Bucket target-pose indices by the static colliders they are
        checked against, in first-seen order. Yields ``(indices,
        static_colliders)`` per group, one IK call each.

        Only the statics differ per group: the attached tool's colliders ride
        the flange for every pose alike, and a stored setup's own tool
        colliders are ignored.
        """
        buckets: dict[Optional[str], list[int]] = {}
        for index, target in enumerate(session.target_poses):
            buckets.setdefault(target.collision_setup_name, []).append(index)
        return [
            (indices, session.colliders_for_setup(setup_name))
            for setup_name, indices in buckets.items()
        ]

    @staticmethod
    def _mounting_for_model(
        session: ReachabilitySession,
        mounting_pose: Optional[WSPose],
        base_height_meters: float,
    ) -> Optional[wb_v2_models.Pose]:
        """The mounting pose sent to IK, raised by the model's own base
        height so every model stands on the same mounting surface."""
        if mounting_pose is not None:
            mounting = mounting_pose.to_nova_pose()
        else:
            mounting = session.nova_mounting_pose
        if mounting is None or base_height_meters == 0.0:
            return mounting
        position = list(mounting.position)
        position[2] += m_to_mm(base_height_meters)
        return wb_v2_models.Pose(position=position, orientation=mounting.orientation)

    @staticmethod
    def _collision_setups(
        session: ReachabilitySession,
        model_name: str,
        colliders: Optional[dict],
        link_chain: Optional[list],
    ) -> Optional[dict[str, wb_v2_models.CollisionSetup]]:
        """The request's collision setups, keyed by the model under test, or
        None when there is nothing to check against."""
        if link_chain is None or not (colliders or session.tool_colliders):
            return None
        return {
            model_name: ReachabilityService._make_collision_setup(
                link_chain, colliders, session.tool_colliders
            )
        }

    async def _run_group_ik(
        self,
        session: ReachabilitySession,
        model_name: str,
        request: wb_v2_models.InverseKinematicsRequest,
    ) -> tuple[Optional[list], Optional[str]]:
        """Solve IK for one pose group. Returns the per-pose solutions, or
        None plus the error that the caller reports on the result."""
        try:
            response = await asyncio.wait_for(
                session.kinematics_api.inverse_kinematics(
                    cell=session.cell_id,
                    inverse_kinematics_request=request,
                ),
                timeout=2.0,
            )
        except _REQUEST_ERRORS as exc:
            carb.log_warn(
                f"IK call failed for model {model_name} "
                f"({len(request.tcp_poses)} pose(s)): {exc}"
            )
            return None, str(exc)
        return response.joints, None

    async def check_single_model(
        self,
        session: ReachabilitySession,
        model_name: str,
        mounting_pose: Optional[WSPose] = None,
    ) -> ReachabilityResult:
        """Check reachability for a single model using an existing session.

        Poses are checked in groups by collider source (see
        ``_group_pose_indices``), one IK call per group, and the solutions
        are scattered back into the original pose order. Without an explicit
        ``mounting_pose`` the session's own mounting pose is used.
        """
        total = len(session.target_poses)
        base_height_meters = MODEL_BASE_OFFSETS.get(model_name, 0.0)
        try:
            # Explicit session limits (mounting assistant) take precedence;
            # otherwise use the model's own mechanical limits. The IK endpoint
            # applies no limits at all when the request carries none and then
            # returns physically impossible configurations, so without this a
            # pose could count as reachable that the real robot cannot reach.
            joint_position_limits = session.joint_position_limits
            if joint_position_limits is None:
                joint_position_limits = await self._get_model_joint_limits(
                    session, model_name
                )

            mounting = self._mounting_for_model(
                session, mounting_pose, base_height_meters
            )
            groups = self._group_pose_indices(session)
            link_chain = None
            if session.tool_colliders or any(colliders for _, colliders in groups):
                link_chain = await self._fetch_link_chain_for_model(session, model_name)

            (
                joint_solutions,
                all_joint_solutions,
                errors,
            ) = await self._solve_pose_groups(
                session=session,
                model_name=model_name,
                groups=groups,
                mounting=mounting,
                joint_position_limits=joint_position_limits,
                link_chain=link_chain,
            )

            reachable_count = sum(1 for solution in joint_solutions if solution)
            reachable = reachable_count == total
            return ReachabilityResult(
                model_name=model_name,
                reachable=reachable,
                reachable_count=reachable_count,
                total_poses=total,
                joint_solutions=joint_solutions,
                all_joint_solutions=all_joint_solutions,
                base_height_meters=base_height_meters,
                error=None if reachable else _join_errors(errors),
                tool_mesh_vertices=session.tool_mesh_vertices,
            )
        except (pydantic.ValidationError, *_REQUEST_ERRORS) as exc:
            carb.log_warn(f"Error testing model {model_name}: {exc}")
            return ReachabilityResult(
                model_name=model_name,
                reachable=False,
                reachable_count=0,
                total_poses=total,
                error=str(exc),
                tool_mesh_vertices=session.tool_mesh_vertices,
            )

    async def _solve_pose_groups(
        self,
        session: ReachabilitySession,
        model_name: str,
        groups: list[tuple[list[int], Optional[dict]]],
        mounting: Optional[wb_v2_models.Pose],
        joint_position_limits: Optional[list],
        link_chain: Optional[list],
    ) -> tuple[list[list[float]], list[list[list[float]]], list[str]]:
        """Run one IK call per collider group and scatter the solutions back
        into the original pose order. A group whose call fails contributes its
        error and leaves its poses unsolved."""
        total = len(session.target_poses)
        joint_solutions: list[list[float]] = [[] for _ in range(total)]
        all_joint_solutions: list[list[list[float]]] = [[] for _ in range(total)]
        errors: list[str] = []

        for indices, colliders in groups:
            request = wb_v2_models.InverseKinematicsRequest(
                motion_group_model=model_name,
                tcp_poses=[
                    session.target_poses[index].pose.to_nova_pose() for index in indices
                ],
                mounting=mounting,
                tcp_offset=session.nova_tcp_offset,
                joint_position_limits=joint_position_limits,
                collision_setups=self._collision_setups(
                    session, model_name, colliders, link_chain
                ),
            )
            group_solutions, error = await self._run_group_ik(
                session, model_name, request
            )
            if group_solutions is None:
                errors.append(error)
                continue
            for pose_index, pose_solutions in zip(indices, group_solutions):
                if pose_solutions:
                    joint_solutions[pose_index] = pose_solutions[0]
                    all_joint_solutions[pose_index] = list(pose_solutions)
        return joint_solutions, all_joint_solutions, errors

    async def check_single_model_multi_base(
        self,
        session: ReachabilitySession,
        model_name: str,
        mounting_poses: list[Optional[WSPose]],
    ) -> ReachabilityResult:
        """Check a model against the targets from several robot bases and
        union the reachable poses.

        A target counts as reachable if at least one base reaches it, so a
        robot reaching pose 1 from base A and pose 2 from base B scores 2/2.
        Each reached pose keeps the joint solution and the mounting pose of
        the first base that reached it, so the preview can render every pose
        at its winning base.
        """
        if len(mounting_poses) <= 1:
            single = mounting_poses[0] if mounting_poses else None
            return await self.check_single_model(session, model_name, single)

        total = len(session.target_poses)
        merged_joints: list[list[float]] = [[] for _ in range(total)]
        merged_all: list[list[list[float]]] = [[] for _ in range(total)]
        per_pose_mounting: list[Optional[list[float]]] = [None] * total
        reached: list[bool] = [False] * total
        base_height_meters = 0.0
        errors: list[str] = []

        for mounting_pose in mounting_poses:
            result = await self.check_single_model(session, model_name, mounting_pose)
            base_height_meters = result.base_height_meters
            if result.error:
                errors.append(result.error)
                continue
            solutions = result.joint_solutions or []
            all_solutions = result.all_joint_solutions or []
            for pose_index in range(total):
                if reached[pose_index]:
                    continue  # already reached by an earlier base
                solution = solutions[pose_index] if pose_index < len(solutions) else []
                if not solution:
                    continue
                reached[pose_index] = True
                merged_joints[pose_index] = solution
                merged_all[pose_index] = (
                    all_solutions[pose_index] if pose_index < len(all_solutions) else []
                )
                per_pose_mounting[pose_index] = (
                    list(mounting_pose.pose) if mounting_pose is not None else None
                )

        reachable_count = sum(reached)
        reachable = reachable_count == total
        # Surface base errors whenever the union could be incomplete because
        # of them: if every base failed there's no result at all; if only
        # some failed and coverage is still short, the failed base(s) may
        # have been the ones that could have reached the remaining targets,
        # so the result must not read as a confident "Not Reachable" without
        # that caveat. A fully-covered union is unambiguous regardless of
        # errors, so it's left alone.
        error = None
        if errors:
            joined = _join_errors(errors)
            if len(errors) == len(mounting_poses):
                error = joined
            elif not reachable:
                error = (
                    f"{len(errors)}/{len(mounting_poses)} base(s) failed "
                    f"({joined}); reachability union may be incomplete"
                )
        return ReachabilityResult(
            model_name=model_name,
            reachable=reachable,
            reachable_count=reachable_count,
            total_poses=total,
            joint_solutions=merged_joints,
            all_joint_solutions=merged_all,
            base_height_meters=base_height_meters,
            per_pose_mounting_poses=per_pose_mounting,
            error=error,
            tool_mesh_vertices=session.tool_mesh_vertices,
        )

    async def _get_model_joint_limits(
        self, session: ReachabilitySession, model_name: str
    ) -> Optional[list[wb_v2_models.LimitRange]]:
        """Fetch a model's mechanical joint position limits, cached per model.

        Bare models expose their limits only inside the GCI transcript of
        getConfigurationForMotionGroup (the kinematic model carries DH
        parameters but no limits), so parse them from the
        MECHANICALLIMIT_GET_MIN/MAX_JOINT_POSITION_LIMITS exchanges.
        Returns None when the transcript is unavailable or has no limits.
        """
        if model_name in _model_joint_limits_cache:
            return _model_joint_limits_cache[model_name]
        try:
            config = await asyncio.wait_for(
                session.models_api.get_configuration_for_motion_group(
                    motion_group_model=model_name
                ),
                timeout=5.0,
            )
            transcript = json.loads(config.content)
            motion_group_uid = config.motion_group_uid
            minimums = _extract_transcript_limit_values(
                transcript,
                motion_group_uid,
                "MECHANICALLIMIT_GET_MIN_JOINT_POSITION_LIMITS",
            )
            maximums = _extract_transcript_limit_values(
                transcript,
                motion_group_uid,
                "MECHANICALLIMIT_GET_MAX_JOINT_POSITION_LIMITS",
            )
            if not minimums or not maximums or len(minimums) != len(maximums):
                carb.log_warn(
                    f"No joint limits in configuration transcript of {model_name}"
                )
                return None
            # The transcript arrays are padded with zeros beyond the real
            # joint count; a joint with lower == upper == 0 cannot move, so
            # trailing zero pairs are padding.
            while minimums and minimums[-1] == 0.0 and maximums[-1] == 0.0:
                minimums.pop()
                maximums.pop()
            limits = [
                wb_v2_models.LimitRange(lower_limit=low, upper_limit=high)
                for low, high in zip(minimums, maximums)
            ]
            _model_joint_limits_cache[model_name] = limits
            return limits
        except NotFoundException:
            carb.log_info(f"No configuration transcript for {model_name}")
            return None
        except (
            *_REQUEST_ERRORS,
            json.JSONDecodeError,
            pydantic.ValidationError,
        ) as exc:
            carb.log_warn(f"Could not fetch joint limits for {model_name}: {exc}")
            return None

    async def prepare_mounting_session_from_prim(
        self,
        motion_group_prim: Usd.Prim,
        target_poses: list[WSPose],
    ) -> tuple[ReachabilitySession, str, Optional[wb_v2_models.Pose]]:
        """Prepare a mounting session from the motion group prim's API config.

        Returns (session, model_name, tcp_offset_pose).
        """
        if not target_poses:
            raise ValueError("No target poses provided for mounting check")

        config = get_motion_group_configuration_from_prim(motion_group_prim)
        if config is None:
            raise ValueError(
                f"Prim {motion_group_prim.GetPath()} is not a motion group"
            )
        stream_config = config.motion_stream_configuration
        api_client = stream_config.get_api_client()

        description = await asyncio.wait_for(
            wb_v2.MotionGroupApi(api_client).get_motion_group_description(
                cell=stream_config.cell,
                controller=stream_config.controller,
                motion_group=stream_config.motion_group,
            ),
            timeout=5.0,
        )
        model_name = description.motion_group_model

        joint_position_limits = None
        try:
            auto_limits = description.operation_limits.auto_limits
            if auto_limits and auto_limits.joints:
                joint_position_limits = [
                    j.position for j in auto_limits.joints if j.position is not None
                ] or None
                carb.log_info(
                    f"Extracted {len(joint_position_limits or [])} joint position limits"
                )
        except AttributeError as exc:
            carb.log_warn(f"Could not extract joint position limits: {exc}")

        tcp_offset_pose = None
        if description.tcps:
            first_tcp_name = next(iter(description.tcps))
            tcp_offset_pose = description.tcps[first_tcp_name].pose
            carb.log_info(f"Using TCP '{first_tcp_name}': {tcp_offset_pose}")

        session = ReachabilitySession(
            api_client=api_client,
            kinematics_api=wb_v2.KinematicsApi(api_client),
            models_api=wb_v2.MotionGroupModelsApi(api_client),
            cell_id=stream_config.cell,
            target_poses=[TargetPose(pose=pose) for pose in target_poses],
            nova_mounting_pose=None,
            nova_tcp_offset=tcp_offset_pose,
            joint_position_limits=joint_position_limits,
        )
        return session, model_name, tcp_offset_pose

    async def _fetch_link_chain_for_model(
        self, session: ReachabilitySession, model_name: str
    ) -> Optional[list]:
        """Fetch a model's own kinematic-chain link shapes for collision
        checking. Shared across every collider-source group for this model
        in check_single_model - a model's link chain does not depend on
        which colliders it's being checked against."""
        try:
            return await asyncio.wait_for(
                session.models_api.get_motion_group_collision_model(
                    motion_group_model=model_name
                ),
                timeout=3.0,
            )
        except _REQUEST_ERRORS as exc:
            carb.log_verbose(
                f"Could not fetch collision model for {model_name}, "
                f"running IK without collision: {exc}"
            )
            return None

    @staticmethod
    def _make_collision_setup(
        link_chain: list, colliders: Optional[dict], tool: Optional[dict] = None
    ) -> wb_v2_models.CollisionSetup:
        """Build a CollisionSetup for one pose group.

        Static `colliders` are world frame and the server places the robot
        via the request's mounting, so they pass through untouched. `tool`
        colliders are flange frame and come only from the attached tool. The
        link chain always comes from the freshly fetched canonical model,
        never from a stored setup. Self collision detection stays on; the
        false positive it would cause for tool geometry over the flange is
        prevented in attached_tool_colliders instead.
        """
        return wb_v2_models.CollisionSetup(
            colliders=colliders,
            link_chain=link_chain,
            tool=tool if tool else None,
            self_collision_detection=True,
        )


# Singleton instance
_reachability_service = ReachabilityService()


def get_reachability_service() -> ReachabilityService:
    """Get the singleton reachability service instance."""
    return _reachability_service
