"""Reachability analysis service for testing robot model reach to TCP poses."""

import asyncio
from dataclasses import dataclass
from typing import Optional

import carb
import omni.timeline
import omni.usd
from pxr import Usd

import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models
from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVACloudInstance, NOVAInstance
from wandelbots.omni.reachability.model_base_offsets import MODEL_BASE_OFFSETS
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.core.collision.collision_export_service import (
    SphereSweepParameters,
    get_collision_export_service,
)
from wandelbots.omni.core.collision.utils import to_nova_collider


@dataclass
class ReachabilityResult:
    """Result of reachability analysis for a single robot model."""

    model_name: str
    reachable: bool
    reachable_count: int
    total_poses: int
    error: Optional[str] = None
    joint_solutions: Optional[list[list[float]]] = None
    base_height_mm: float = 0.0


@dataclass
class ReachabilitySession:
    """Holds the API context for an ongoing reachability analysis."""

    api_client: wb_v2.ApiClient
    kinematics_api: wb_v2.KinematicsApi
    models_api: wb_v2.MotionGroupModelsApi
    cell_id: str
    nova_target_poses: list
    nova_mounting_pose: object
    nova_tcp_offset: object
    total_poses: int
    static_colliders: Optional[dict] = None


# Model name patterns that are positioners / turntables, not robot arms.
# The NOVA IK API does not support these and returns 500.
_NON_ARM_SUFFIXES = ("_IRT", "_TURN", "_POSITIONER")


def _is_robot_arm(model_name: str) -> bool:
    """Return True if the model is a robot arm (not a positioner/turntable)."""
    upper = model_name.upper()
    return not any(suffix in upper for suffix in _NON_ARM_SUFFIXES)


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

    def _make_api_client(self, instance: NOVAInstance) -> Optional[wb_v2.ApiClient]:
        """
        Create an authenticated API client for the given instance.

        Args:
            instance: NOVA instance to connect to.

        Returns:
            API client or None if creation failed.
        """
        if isinstance(instance, NOVACloudInstance):
            token = get_instances_api().get_auth_token_from_host(instance.host)
            return instance.create_api_client(token=token)
        return instance.create_api_client()

    async def prepare_session(
        self,
        instance: NOVAInstance,
        target_poses: list[WSPose],
        mounting_pose: Optional[WSPose] = None,
        tcp_offset: Optional[WSPose] = None,
        static_colliders: Optional[dict] = None,
    ) -> tuple[ReachabilitySession, list[str]]:
        """Prepare an analysis session and return the session context + list of model names."""
        if not target_poses:
            raise ValueError("No target poses provided for reachability check")

        cells = await asyncio.wait_for(
            get_instances_api().fetch_cells_for_instance(instance), timeout=5.0
        )
        if not cells or len(cells) == 0:
            raise ValueError(
                f"Instance '{instance.display_name}' has no cells available"
            )
        cell_id = cells[0].name

        api_client = self._make_api_client(instance)
        if api_client is None:
            raise Exception(f"Cannot connect to instance '{instance.display_name}'")

        nova_target_poses = [pose.to_nova_pose() for pose in target_poses]
        nova_mounting_pose = mounting_pose.to_nova_pose() if mounting_pose else None
        nova_tcp_offset = tcp_offset.to_nova_pose() if tcp_offset else None

        models_api = wb_v2.MotionGroupModelsApi(api_client)
        raw_models: list[str] = await asyncio.wait_for(
            models_api.get_motion_group_models(), timeout=5.0
        )
        seen: set[str] = set()
        all_models: list[str] = []
        for m in raw_models:
            if m not in seen and _is_robot_arm(m):
                seen.add(m)
                all_models.append(m)
        carb.log_info(
            f"Found {len(raw_models)} motion group models, "
            f"using {len(all_models)} robot arms"
        )

        if static_colliders:
            carb.log_info(
                f"Using {len(static_colliders)} scene colliders for collision checking"
            )

        session = ReachabilitySession(
            api_client=api_client,
            kinematics_api=wb_v2.KinematicsApi(api_client),
            models_api=models_api,
            cell_id=cell_id,
            nova_target_poses=nova_target_poses,
            nova_mounting_pose=nova_mounting_pose,
            nova_tcp_offset=nova_tcp_offset,
            total_poses=len(target_poses),
            static_colliders=static_colliders,
        )
        return session, all_models

    async def check_single_model(
        self, session: ReachabilitySession, model_name: str
    ) -> ReachabilityResult:
        """Check reachability for a single model using an existing session."""
        try:
            base_height_mm = MODEL_BASE_OFFSETS.get(model_name, 0.0)

            mounting = session.nova_mounting_pose
            if base_height_mm != 0.0 and mounting is not None:
                adjusted_pos = list(mounting.position)
                adjusted_pos[2] += base_height_mm * 1000.0
                mounting = wb_v2_models.Pose(
                    position=adjusted_pos,
                    orientation=mounting.orientation,
                )

            collision_setups = None
            if session.static_colliders:
                collision_setups = await self._build_collision_setups_for_model(
                    session, model_name
                )

            ik_request = wb_v2_models.InverseKinematicsRequest(
                motion_group_model=model_name,
                tcp_poses=session.nova_target_poses,
                mounting=mounting,
                tcp_offset=session.nova_tcp_offset,
                collision_setups=collision_setups,
            )
            ik_response = await asyncio.wait_for(
                session.kinematics_api.inverse_kinematics(
                    cell=session.cell_id,
                    inverse_kinematics_request=ik_request,
                ),
                timeout=2.0,
            )
            reachable_count = sum(
                1
                for pose_solutions in ik_response.joints
                if pose_solutions and len(pose_solutions) > 0
            )
            joint_solutions = [
                pose_solutions[0] if pose_solutions else []
                for pose_solutions in ik_response.joints
            ]
            return ReachabilityResult(
                model_name=model_name,
                reachable=reachable_count == session.total_poses,
                reachable_count=reachable_count,
                total_poses=session.total_poses,
                joint_solutions=joint_solutions,
                base_height_mm=base_height_mm,
            )
        except Exception as exc:
            carb.log_warn(f"Error testing model {model_name}: {exc}")
            return ReachabilityResult(
                model_name=model_name,
                reachable=False,
                reachable_count=0,
                total_poses=session.total_poses,
                error=str(exc),
            )

    async def close_session(self, session: ReachabilitySession) -> None:
        """Close the API client for a session."""
        try:
            await session.api_client.close()
        except Exception as exc:
            carb.log_warn(f"Error closing API client: {exc}")

    async def sweep_colliders_around_prim(
        self,
        prim_path: str,
        radius: float,
        stabilization_wait: float = 0.5,
    ) -> dict[str, wb_v2_models.Collider]:
        """Perform a sphere sweep around a prim and return NOVA-format colliders.

        Automatically starts the timeline if it is not already playing and waits
        for physics to stabilize before sweeping.

        Args:
            prim_path: USD prim path to use as the sweep center.
            radius: Sphere sweep radius in stage units (meters).
            stabilization_wait: Seconds to wait for physics after starting the timeline.

        Returns:
            Dict mapping collider name to NOVA Collider, or empty dict on failure.
        """
        try:
            timeline = omni.timeline.get_timeline_interface()
            was_playing = timeline.is_playing()
            if not was_playing:
                timeline.play()
                while timeline.is_stopped():
                    await asyncio.sleep(0.1)
                await asyncio.sleep(stabilization_wait)

            pose = PrimUtils.get_prim_pose(
                prim_path=prim_path,
                coordinate_system="world",
                rotation_type="cartesian",
                stage=self._stage,
            )
            position = pose.pose[:3]
            position_m = [p / 1000.0 for p in position]

            sweep_params = SphereSweepParameters(
                sweep_type="sphere",
                radius=radius,
                position=position_m,
                direction=[0.0, 0.0, -1.0],
                max_distance=0.0,
            )

            export_service = get_collision_export_service()
            colliders = export_service.collision_sweep(sweep_params)

            nova_colliders: dict[str, wb_v2_models.Collider] = {}
            for name, collider in colliders.items():
                nova_collider = to_nova_collider(collider)
                if nova_collider is not None:
                    nova_colliders[name] = nova_collider

            carb.log_info(
                f"Sphere sweep around '{prim_path}' (r={radius}m): "
                f"found {len(nova_colliders)} colliders"
            )
            return nova_colliders
        except Exception as exc:
            carb.log_warn(f"Sphere sweep failed: {exc}")
            return {}

    async def _build_collision_setups_for_model(
        self, session: ReachabilitySession, model_name: str
    ) -> Optional[dict]:
        """Build a per-model collision setup from static colliders + model's own link chain."""
        try:
            link_chain = await asyncio.wait_for(
                session.models_api.get_motion_group_collision_model(
                    motion_group_model=model_name
                ),
                timeout=3.0,
            )
            collision_setup = wb_v2_models.CollisionSetup(
                colliders=session.static_colliders,
                link_chain=link_chain,
                tool=None,
                self_collision_detection=True,
            )
            return {model_name: collision_setup}
        except Exception as exc:
            carb.log_verbose(
                f"Could not fetch collision model for {model_name}, "
                f"running IK without collision: {exc}"
            )
            return None


# Singleton instance
_reachability_service = ReachabilityService()


def get_reachability_service() -> ReachabilityService:
    """Get the singleton reachability service instance."""
    return _reachability_service
