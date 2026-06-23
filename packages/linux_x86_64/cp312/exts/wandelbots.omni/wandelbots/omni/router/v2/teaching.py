import asyncio
from typing import Literal, Optional

import carb
import omni.usd

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    status,
    Query,
)
from fastapi.websockets import WebSocketState

import isaacsim.core.utils.prims as prims_utils
import wandelbots_api_client.v2.models as wb_v2_models
from pydantic import BaseModel, Field
from wandelbots.omni.datatypes import (
    GhostObjectSource,
    GhostObject,
    WSPose,
    TCPSource,
)
from wandelbots.omni.manipulators.utils import get_link_0_from_motion_group_prim
from wandelbots.omni.utils.prims import PrimUtils


from wandelbots.omni.utils.teaching import GhostObjectUtils

teaching_router = APIRouter(prefix="/teaching", tags=["Teaching"])
trajectory_planner_router = APIRouter(
    prefix="/trajectory-planner", tags=["Trajectory Planner"]
)


@teaching_router.get(
    path="/ghost-objects/sources",
    operation_id="list_ghost_object_sources",
    response_model=list[GhostObjectSource],
    responses={
        200: {"description": "Successfully retrieved the ghost objects"},
        500: {
            "description": "Internal server error: Unable to fetch ghost object sources from the scene"
        },
    },
)
def list_ghost_object_sources() -> list[GhostObjectSource]:
    """
    Return the prim paths of all prims that are sources for ghost objects i.e. tools.
    These ghost object sources must follow a strict predicate `tool_` and the source ghost must be created in the scene.
    Source ghost is created by default during robot creation
    """
    try:
        return GhostObjectUtils.get_ghost_object_sources()
    except Exception as e:
        raise HTTPException(
            500, f"Unable to fetch ghost object sources from the scene: {e}"
        )


@teaching_router.get(
    path="/tcps/sources",
    operation_id="list_tcp_sources",
    response_model=list[TCPSource],
    responses={
        200: {"description": "Successfully retrieved the tcp sources"},
        500: {
            "description": "Internal server error: Unable to fetch tcp sources from the scene"
        },
    },
)
def list_tcp_sources() -> list[TCPSource]:
    """
    Return the prim paths of all tcps that are defined in the scene which follows a strict predicate `tcp_` or 'TCP_'
    """
    try:
        return GhostObjectUtils.get_all_tcp_sources()
    except Exception as e:
        raise HTTPException(500, f"Unable to fetch tcp sources from the scene: {e}")


class CreateGhostObject(BaseModel):
    prim_path: str = Field(description="prim path of the object to clone")
    ref_pose: Optional[WSPose] = Field(
        None,
        description="The TCP pose to which the ghost object has to be attached",
    )
    tcp_prim_path: Optional[str] = Field(
        None,
        description="Prim path of the TCP which is used as ghost object transform origin",
    )


@teaching_router.post(
    path="/ghost-objects",
    operation_id="create_ghost_object",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses={
        204: {"description": "Successfully added ghost object to the scene"},
        404: {"description": "Invalid prim path"},
        422: {
            "description": "Source ghost object is not created. Make sure that the tool is in robot workspace and the robot is created"
        },
        500: {
            "description": "Internal server error: Unable to add ghost objects to the scene"
        },
    },
)
async def create_ghost_object(ghost_object_data: CreateGhostObject) -> None:
    """
    Create a ghost object from the prim under the specified path.
    This will clone the prim, apply the specified material and shift the origin of the prim.
    """
    if not prims_utils.is_prim_path_valid(ghost_object_data.prim_path):
        raise HTTPException(
            404, detail=f"Invalid prim path: {ghost_object_data.prim_path}"
        )

    tcp_prim_path = ghost_object_data.tcp_prim_path
    if tcp_prim_path and not prims_utils.is_prim_path_valid(tcp_prim_path):
        raise HTTPException(404, detail=f"Invalid TCP prim path: {tcp_prim_path}")

    try:
        GhostObjectUtils.add_ghost_object(
            prims_utils.get_prim_at_path(ghost_object_data.prim_path),
            ghost_object_data.ref_pose,
            tcp_prim=prims_utils.get_prim_at_path(tcp_prim_path)
            if tcp_prim_path
            else None,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        import traceback

        carb.log_error(traceback.format_exc())
        raise HTTPException(500, f"Unable to add ghost objects to the scene: {e}")


@teaching_router.delete(
    path="/ghost-objects",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="clear_ghost_objects",
    response_model=None,
    responses={204: {"description": "Successfully deleted specified ghost objects"}},
)
async def clear_ghost_objects(prim_path: str = None) -> None:
    """
    Remove all ghost objects
    """
    existing_ghost_paths: set[str] = {
        g.prim_path for g in GhostObjectUtils.get_ghost_objects()
    }

    if prim_path and prim_path not in existing_ghost_paths:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ghost object {prim_path} not found",
        )

    if prim_path:
        existing_ghost_paths = {prim_path}

    try:
        GhostObjectUtils.delete_ghost_objects(list(existing_ghost_paths))
    except Exception as e:
        raise HTTPException(500, detail=f"Failed to delete ghost objects: {e}")


@teaching_router.get(
    path="/ghost-objects",
    operation_id="list_ghost_objects",
    response_model=list[GhostObject],
    responses={
        200: {"description": "Successfully fetched all the ghost objects in the scene"},
        500: {"description": "Unable to fetch all the ghost objects in the scene"},
    },
)
def list_ghost_objects(
    relative_to_prim: str = Query(
        None, description="Prim path to which the ghost object poses are relative"
    ),
) -> list[GhostObject]:
    """
    Fetches all ghost objects defined in the scene
    """
    try:
        return GhostObjectUtils.get_ghost_objects(relative_to_prim)
    except Exception as e:
        raise HTTPException(500, f"Unable to fetch ghost objects from the scene: {e}")


class GhostObjectsMessage(BaseModel):
    ghost_objects: list[GhostObject]


@teaching_router.websocket("/ghost-objects/track")
async def pose_tracker_websocket(
    websocket: WebSocket,
    interval: float = Query(
        1.0, ge=0.05, le=5.0, description="Time delay in seconds between updates"
    ),
    relative_to_prim: str = Query(
        None, description="Prim path to which the ghost object poses are relative"
    ),
) -> GhostObjectsMessage:
    """
    WebSocket endpoint that streams ghost object poses to connected clients.
    """
    await websocket.accept()
    carb.log_info("WebSocket connection accepted for ghost objects tracking")
    try:
        while True:
            ghost_objects_data = GhostObjectsMessage(
                ghost_objects=GhostObjectUtils.get_ghost_objects(relative_to_prim)
            )
            if websocket.client_state != WebSocketState.CONNECTED:
                carb.log_info("WebSocket connection closed")
                break
            await websocket.send_text(ghost_objects_data.model_dump_json())
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        carb.log_info("WebSocket connection closed by client.")
    except Exception as e:
        carb.log_error(f"Unexpected error in websocket connection: {e}")


# -- Ghost object export ---------------------------------------------------


class ExportedGhostObject(BaseModel):
    name: str
    prim_path: str
    robot_prim_path: str | None = None
    # Resolved NOVA TCP name (derived from the ghost's linked source TCP prim).
    tcp_name: str | None = None
    # [x, y, z, rx, ry, rz], relative to ``robot_prim_path`` (the linked motion group).
    pose: list[float] = Field(default_factory=list)
    preferred_joint_values: list[float] | None = None


class ExportedGhostObjects(BaseModel):
    version: str = "v1"
    ghost_objects: list[ExportedGhostObject] = Field(default_factory=list)


def build_ghost_objects_export() -> ExportedGhostObjects:
    """Flatten all scene ghost objects into a NOVA-storable structure.

    Poses are relative to each ghost object's linked motion group (the default of
    ``get_ghost_objects``), so the export is independent of the world transform.
    """
    stage = omni.usd.get_context().get_stage()
    items: list[ExportedGhostObject] = []
    for ghost in GhostObjectUtils.get_ghost_objects():
        prim = stage.GetPrimAtPath(ghost.prim_path) if stage else None
        items.append(
            ExportedGhostObject(
                name=ghost.name,
                prim_path=ghost.prim_path,
                robot_prim_path=ghost.robot_prim_path,
                tcp_name=GhostObjectUtils.get_nova_tcp_name(prim)
                if prim and prim.IsValid()
                else None,
                pose=list(ghost.pose.pose),
                preferred_joint_values=ghost.preferred_joint_values,
            )
        )
    return ExportedGhostObjects(ghost_objects=items)


@teaching_router.get(
    path="/ghost-objects/export",
    operation_id="export_ghost_objects",
    status_code=status.HTTP_200_OK,
    response_model=ExportedGhostObjects,
    responses={
        200: {"description": "Ghost objects exported as a flattened NOVA structure."},
        500: {"description": "Unable to export ghost objects from the scene."},
    },
)
def export_ghost_objects() -> ExportedGhostObjects:
    """Export all scene ghost objects as a flattened NOVA-compatible structure."""
    try:
        return build_ghost_objects_export()
    except Exception as e:
        raise HTTPException(500, f"Unable to export ghost objects from the scene: {e}")


# -- Trajectory plan export ------------------------------------------------


class PoseMetadata(BaseModel):
    prim_path: str


class SkillMetadata(BaseModel):
    motion_group_prim_path: str | None = None
    # USD stage (scene) the plan was exported from, for traceability on import.
    scene_path: str | None = None
    poses: list[PoseMetadata] = Field(default_factory=list)


class PlanTrajectorySegment(BaseModel):
    """One contiguous run of motion commands that share a single TCP.

    A ``PlanTrajectoryRequest`` can only carry one TCP (via
    ``motion_group_setup.tcp_offset``), so a skill whose poses use different TCPs
    is exported as several segments. A consumer plans each segment individually and
    then merges the resulting joint trajectories via the ``mergeTrajectories``
    endpoint, using ``blending`` as the inter-segment blend.
    """

    tcp_name: str | None = None
    plan_trajectory_request: dict  # PlanTrajectoryRequest.to_dict()
    # BlendingPosition.to_dict(): inter-segment blend at this segment's END.
    # Ignored for the last segment. None means a hard (non-blended) transition.
    blending: dict | None = None


class SegmentedTrajectoryPlan(BaseModel):
    """A segmented trajectory plan plus the shared settings to merge the segments.

    Because a ``PlanTrajectoryRequest`` carries a single TCP, a multi-TCP skill is
    exported as one segment per contiguous same-TCP run. To reconstruct the motion,
    a consumer plans each segment's ``plan_trajectory_request`` into a
    ``JointTrajectory`` and then calls the ``mergeTrajectories`` endpoint::

        MergeTrajectoriesRequest(
            motion_group_setup=motion_group_setup,
            trajectory_segments=[
                MergeTrajectoriesSegment(
                    trajectory=<planned jt for segment i>,
                    blending=segments[i].blending,
                    limits_override=limits_override,
                    collision_setups=collision_setups,
                )
                for i in range(len(segments))
            ],
        )

    ``limits_override`` and ``collision_setups`` are shared across all segments.
    """

    motion_group_setup: dict  # MotionGroupSetup.to_dict() for the merge request
    limits_override: dict | None = (
        None  # LimitsOverride.to_dict(), applied to all segments
    )
    collision_setups: dict | None = (
        None  # {name: CollisionSetup.to_dict()}, all segments
    )
    segments: list[PlanTrajectorySegment] = Field(default_factory=list)


class ExportedSkill(BaseModel):
    name: str
    # Export version tag ("v1", "v2", ...); incremented on every plan/store.
    version: str = "v1"
    robot_prim_path: str | None = None
    tcp_name: str | None = None
    collision_setup: str | None = None
    type: Literal["plan_trajectory", "plan_collision_free"] = "plan_trajectory"
    # Single-segment (single-TCP) skills use the flat request; multi-TCP skills use
    # plan_segmented_trajectory (segments + shared merge settings).
    plan_trajectory_request: dict | None = None
    plan_segmented_trajectory: SegmentedTrajectoryPlan | None = None
    plan_collision_free_requests: list[dict] | None = None
    metadata: SkillMetadata | None = None


class ExportedTrajectoryPlan(BaseModel):
    skills: list[ExportedSkill] = Field(default_factory=list)


def _resolve_pose_for_prim(
    stage, prim_path: str, mg_prim_path: str | None
) -> wb_v2_models.Pose | None:
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return None
    if mg_prim_path:
        mg_prim = stage.GetPrimAtPath(mg_prim_path)
        if mg_prim and mg_prim.IsValid():
            link_0 = get_link_0_from_motion_group_prim(mg_prim)
            ref = str(link_0.GetPath()) if link_0 else mg_prim_path
            ws_pose = PrimUtils.get_relative_prim_pose(
                prim_path_a=ref,
                prim_path_b=prim_path,
                rotation_type="cartesian",
            )
            return ws_pose.to_nova_pose()
    ws_pose = PrimUtils.get_prim_pose(
        prim_path=prim_path,
        coordinate_system="world",
        rotation_type="cartesian",
        stage=stage,
    )
    return ws_pose.to_nova_pose()


def _get_joint_config(pose_cfg) -> list[float] | None:
    jc = pose_cfg.selected_joint_config
    if (
        jc is None
        and pose_cfg.joint_configs
        and pose_cfg.selected_config_idx < len(pose_cfg.joint_configs)
    ):
        jc = pose_cfg.joint_configs[pose_cfg.selected_config_idx]
    return jc


async def _fetch_motion_group_setups(
    config, stage, tcp_names: set[str | None]
) -> dict[str | None, wb_v2_models.MotionGroupSetup]:
    """Build one MotionGroupSetup per requested TCP from a single description fetch.

    A ``PlanTrajectoryRequest`` carries the TCP only in
    ``motion_group_setup.tcp_offset``, so segments with different TCPs each need
    their own setup. The motion group description (mounting, model, limits, all
    TCP offsets) is fetched once and reused; only ``tcp_offset`` differs per TCP.
    """
    from wandelbots.omni.manipulators.motion_group import (
        get_motion_group_configuration_from_prim,
    )
    from wandelbots.omni.ui.tool.trajectory_planner.service.helpers import (
        build_motion_group_setup,
        fetch_motion_group_context,
    )
    from wandelbots.omni.utils.api import get_api_client_from_config

    def _empty() -> dict[str | None, wb_v2_models.MotionGroupSetup]:
        empty = wb_v2_models.MotionGroupSetup(motion_group_model="", cycle_time=4)
        return {tcp: empty for tcp in tcp_names}

    if not config.robot_prim_path:
        return _empty()

    prim = stage.GetPrimAtPath(config.robot_prim_path)
    if not prim or not prim.IsValid():
        return _empty()

    mg_config = get_motion_group_configuration_from_prim(prim)
    if not mg_config:
        return _empty()

    stream_config = mg_config.motion_stream_configuration
    api_config = stream_config.get_api_configuration()

    async with get_api_client_from_config(api_config) as api_client:
        ctx = await fetch_motion_group_context(
            api_client,
            cell=stream_config.cell,
            controller=stream_config.controller,
            motion_group=stream_config.motion_group,
            tcp_name=config.tcp_name,
            collision_setup_name=config.collision_setup,
        )
        description = ctx.description
        setups: dict[str | None, wb_v2_models.MotionGroupSetup] = {}
        for tcp in tcp_names:
            if tcp == config.tcp_name:
                tcp_offset = ctx.tcp_offset
            elif tcp and getattr(description, "tcps", None):
                tcp_data = description.tcps.get(tcp)
                tcp_offset = tcp_data.pose if tcp_data else None
            else:
                tcp_offset = ctx.tcp_offset
            setups[tcp] = build_motion_group_setup(
                description,
                tcp_offset,
                tcp_velocity_limit=getattr(config, "tcp_velocity", None),
                tcp_acceleration_limit=getattr(config, "tcp_acceleration", None),
                payload_name=getattr(config, "payload_name", None),
                payload_mass=getattr(config, "payload_mass", None),
            )
        return setups


def _build_metadata(config, stage=None) -> SkillMetadata:
    """Build metadata section with motion group, scene and per-pose prim paths."""
    scene_path: str | None = None
    if stage is not None:
        try:
            scene_path = stage.GetRootLayer().identifier
        except Exception:
            scene_path = None
    return SkillMetadata(
        motion_group_prim_path=config.robot_prim_path,
        scene_path=scene_path,
        poses=[PoseMetadata(prim_path=pose.prim_path) for pose in config.poses],
    )


def _effective_tcp(pose_cfg, config) -> str | None:
    """Per-pose TCP override, falling back to the skill's default TCP."""
    return getattr(pose_cfg, "tcp_name", None) or config.tcp_name


def _group_poses_by_tcp(poses, config) -> list[list[int]]:
    """Group the target poses (poses[1:]) into contiguous runs sharing one TCP.

    Returns a list of runs, each a list of indices into ``poses``. The first pose
    is the trajectory start and is never part of a run. Each run becomes its own
    ``PlanTrajectoryRequest`` because a request carries a single TCP via
    ``motion_group_setup.tcp_offset``.
    """
    runs: list[list[int]] = []
    for idx in range(1, len(poses)):
        tcp = _effective_tcp(poses[idx], config)
        if runs and _effective_tcp(poses[runs[-1][-1]], config) == tcp:
            runs[-1].append(idx)
        else:
            runs.append([idx])
    return runs


def _build_motion_command(pose_cfg, stage, config, global_blending, global_limits):
    """Build a single MotionCommand from a pose config, or None if unresolvable."""
    from wandelbots.omni.ui.tool.trajectory_planner.widgets.motion_settings_dialog import (
        blending_from_dict,
        limits_from_dict,
    )

    nova_pose = _resolve_pose_for_prim(
        stage, pose_cfg.prim_path, config.robot_prim_path
    )
    if nova_pose is None:
        return None
    mt = pose_cfg.motion_type or "PathCartesianPTP"
    if mt == "PathJointPTP":
        joint_pos = _get_joint_config(pose_cfg)
        if joint_pos:
            path = wb_v2_models.MotionCommandPath(
                wb_v2_models.PathJointPTP(target_joint_position=joint_pos)
            )
        else:
            path = wb_v2_models.MotionCommandPath(
                wb_v2_models.PathCartesianPTP(target_pose=nova_pose)
            )
    elif mt == "PathLine":
        path = wb_v2_models.MotionCommandPath(
            wb_v2_models.PathLine(target_pose=nova_pose)
        )
    else:
        path = wb_v2_models.MotionCommandPath(
            wb_v2_models.PathCartesianPTP(target_pose=nova_pose)
        )

    # Per-pose overrides take precedence over global
    pose_blending = blending_from_dict(getattr(pose_cfg, "blending", None))
    pose_limits = limits_from_dict(getattr(pose_cfg, "limits_override", None))
    return wb_v2_models.MotionCommand(
        path=path,
        blending=pose_blending or global_blending,
        limits_override=pose_limits or global_limits,
    )


def _segment_blending(pose_cfg, config, global_blending) -> dict | None:
    """Inter-segment blend for a TCP boundary pose, as a BlendingPosition dict.

    The mergeTrajectories segment blending accepts only a ``BlendingPosition``.
    If the boundary pose's blend is a ``BlendingAuto`` (or absent), fall back to a
    hard transition (None) and log it.
    """
    from wandelbots.omni.ui.tool.trajectory_planner.widgets.motion_settings_dialog import (
        blending_from_dict,
    )

    blend = blending_from_dict(getattr(pose_cfg, "blending", None)) or global_blending
    if blend is None:
        return None
    inner = getattr(blend, "actual_instance", None)
    if isinstance(inner, wb_v2_models.BlendingPosition):
        return inner.to_dict()
    carb.log_info(
        f"Skill '{config.name}': inter-segment blend at pose "
        f"'{pose_cfg.prim_path}' is not a position blend; using a hard transition."
    )
    return None


async def _build_normal_skill(config, stage) -> ExportedSkill:
    from wandelbots.omni.ui.tool.trajectory_planner.widgets.motion_settings_dialog import (
        blending_from_dict,
        limits_from_dict,
    )

    poses = config.poses or []

    # Resolve global blending/limits override
    global_blending = blending_from_dict(getattr(config, "global_blending", None))
    global_limits = limits_from_dict(getattr(config, "global_limits_override", None))

    # Legacy: auto_blending field for backward compat
    if not global_blending and getattr(config, "auto_blending", False):
        global_blending = wb_v2_models.MotionCommandBlending(
            wb_v2_models.BlendingAuto(
                min_velocity_in_percent=getattr(
                    config, "blending_min_velocity_percent", 50
                )
            )
        )

    # A PlanTrajectoryRequest can only carry a single TCP
    # (motion_group_setup.tcp_offset), so split into contiguous same-TCP runs. A
    # consumer plans each segment and merges them via the mergeTrajectories endpoint.
    runs = _group_poses_by_tcp(poses, config)

    tcp_names: set[str | None] = (
        {_effective_tcp(poses[run[0]], config) for run in runs}
        if runs
        else {config.tcp_name}
    )
    setups = await _fetch_motion_group_setups(config, stage, tcp_names)

    segments: list[PlanTrajectorySegment] = []
    if not runs:
        # No target poses: keep a single empty segment with the default TCP so the
        # exported structure stays consistent (matches prior behaviour).
        start = _get_joint_config(poses[0]) if poses else []
        request = wb_v2_models.PlanTrajectoryRequest(
            motion_group_setup=setups.get(config.tcp_name),
            start_joint_position=start or [],
            motion_commands=[],
        )
        segments.append(
            PlanTrajectorySegment(
                tcp_name=config.tcp_name,
                plan_trajectory_request=request.to_dict(),
            )
        )
    else:
        for run_pos, run in enumerate(runs):
            seg_tcp = _effective_tcp(poses[run[0]], config)
            # Segment starts at the pose right before its first target pose
            # (= the previous segment's last pose, chaining the trajectory).
            seg_start = _get_joint_config(poses[run[0] - 1])
            seg_commands = [
                cmd
                for idx in run
                if (
                    cmd := _build_motion_command(
                        poses[idx], stage, config, global_blending, global_limits
                    )
                )
                is not None
            ]
            request = wb_v2_models.PlanTrajectoryRequest(
                motion_group_setup=setups.get(seg_tcp),
                start_joint_position=seg_start or [],
                motion_commands=seg_commands,
            )
            # Inter-segment blend lives on the boundary (last) pose of the run;
            # ignored for the final segment.
            blending = (
                _segment_blending(poses[run[-1]], config, global_blending)
                if run_pos < len(runs) - 1
                else None
            )
            segments.append(
                PlanTrajectorySegment(
                    tcp_name=seg_tcp,
                    plan_trajectory_request=request.to_dict(),
                    blending=blending,
                )
            )

    # Single-TCP skills export a flat request; multi-TCP skills export the segmented
    # plan plus the shared settings needed to rebuild a MergeTrajectoriesRequest.
    single_request: dict | None = None
    segmented: SegmentedTrajectoryPlan | None = None
    if len(segments) <= 1:
        single_request = segments[0].plan_trajectory_request if segments else None
    else:
        segmented = SegmentedTrajectoryPlan(
            motion_group_setup=segments[0].plan_trajectory_request.get(
                "motion_group_setup", {}
            ),
            limits_override=getattr(config, "global_limits_override", None),
            collision_setups=None,
            segments=segments,
        )

    return ExportedSkill(
        name=config.name,
        robot_prim_path=config.robot_prim_path,
        tcp_name=config.tcp_name,
        collision_setup=config.collision_setup,
        type="plan_trajectory",
        plan_trajectory_request=single_request,
        plan_segmented_trajectory=segmented,
        metadata=_build_metadata(config, stage),
    )


async def _build_collision_free_skill(config, stage) -> ExportedSkill:
    cf_algorithm = getattr(config, "cf_algorithm", "RRTConnectAlgorithm")
    cf_max_iterations = getattr(config, "cf_max_iterations", 10000)

    if cf_algorithm == "MidpointInsertionAlgorithm":
        algorithm = wb_v2_models.CollisionFreeAlgorithm(
            wb_v2_models.MidpointInsertionAlgorithm(max_iterations=cf_max_iterations)
        )
    else:
        algorithm = wb_v2_models.CollisionFreeAlgorithm(
            wb_v2_models.RRTConnectAlgorithm(max_iterations=cf_max_iterations)
        )

    # Collision-free planning operates in joint space and uses the skill's single
    # default TCP for the whole motion.
    setups = await _fetch_motion_group_setups(config, stage, {config.tcp_name})
    mg_setup = setups.get(config.tcp_name)

    requests = []
    for i in range(len(config.poses) - 1):
        start_jc = _get_joint_config(config.poses[i])
        target_jc = _get_joint_config(config.poses[i + 1])
        if not start_jc or not target_jc:
            continue
        req = wb_v2_models.PlanCollisionFreeRequest(
            motion_group_setup=mg_setup,
            start_joint_position=start_jc,
            target=target_jc,
            algorithm=algorithm,
        )
        requests.append(req.to_dict())

    return ExportedSkill(
        name=config.name,
        robot_prim_path=config.robot_prim_path,
        tcp_name=config.tcp_name,
        collision_setup=config.collision_setup,
        type="plan_collision_free",
        plan_collision_free_requests=requests,
        metadata=_build_metadata(config, stage),
    )


async def build_skill(config, stage) -> ExportedSkill:
    if config.collision_setup:
        return await _build_collision_free_skill(config, stage)
    return await _build_normal_skill(config, stage)


@trajectory_planner_router.get(
    path="/export",
    operation_id="export_trajectory_plans",
    status_code=status.HTTP_200_OK,
    response_model=ExportedTrajectoryPlan,
    responses={
        200: {
            "description": "Trajectory plans exported with NOVA-compatible requests grouped by skill."
        },
    },
)
async def export_trajectory_plans():
    """Export all trajectory planner skills as NOVA-compatible planning requests."""
    from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_window import (
        TrajectoryPlannerWindow,
    )
    from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
        get_trajectory_planner_store,
    )

    configs = TrajectoryPlannerWindow.get_live_configs()
    if configs is None:
        store = get_trajectory_planner_store()
        configs = store.load_configs()

    stage = omni.usd.get_context().get_stage()
    if not stage:
        raise HTTPException(status_code=500, detail="No USD stage available.")

    skills = [await build_skill(config, stage) for config in configs]
    return ExportedTrajectoryPlan(skills=skills)


@trajectory_planner_router.get(
    path="/{skill_name}/export",
    operation_id="export_trajectory_plan_skill",
    status_code=status.HTTP_200_OK,
    response_model=ExportedSkill,
    responses={
        200: {
            "description": "Single trajectory plan skill exported with NOVA-compatible requests."
        },
        404: {"description": "Skill not found."},
    },
)
async def export_trajectory_plan_skill(skill_name: str):
    """Export a single trajectory planner skill by name."""
    from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_window import (
        TrajectoryPlannerWindow,
    )
    from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
        get_trajectory_planner_store,
    )

    configs = TrajectoryPlannerWindow.get_live_configs()
    if configs is None:
        store = get_trajectory_planner_store()
        configs = store.load_configs()

    stage = omni.usd.get_context().get_stage()
    if not stage:
        raise HTTPException(status_code=500, detail="No USD stage available.")

    for config in configs:
        if config.name == skill_name:
            return await build_skill(config, stage)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Skill '{skill_name}' not found.",
    )
