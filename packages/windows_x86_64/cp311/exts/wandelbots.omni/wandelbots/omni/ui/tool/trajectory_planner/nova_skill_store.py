"""Load trajectory-planner skills back from the Nova backend by name.

Every successful plan stores a lossless ``TrajectoryPlannerConfig`` to the Nova
object store under the ``trajectory-plan-config/`` key prefix (see
``PlanningOrchestrator._store_to_nova``). These helpers list and fetch those
configs so a skill can be loaded into the planner after a restart or on another
machine.
"""

from __future__ import annotations

import json

import carb

import wandelbots_api_client.v2 as wb_v2

from wandelbots.omni.ui.tool.trajectory_planner.planning_orchestrator import (
    TRAJECTORY_PLAN_PREFIX,
    TRAJECTORY_PLAN_CONFIG_PREFIX,
)
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
    PoseConfig,
    TrajectoryPlannerConfig,
)


def _config_from_payload(raw: bytes) -> TrajectoryPlannerConfig | None:
    """Parse a stored config payload into a TrajectoryPlannerConfig.

    The payload is the JSON ``{"version": ..., "config": {...}}`` written by
    ``_store_to_nova``. Older payloads that are just the bare config dict are also
    tolerated.
    """
    data = json.loads(bytes(raw).decode("utf-8"))
    config_dict = data.get("config", data) if isinstance(data, dict) else data
    return TrajectoryPlannerConfig.model_validate(config_dict)


# -- Import-from-plan helpers ------------------------------------------------
# The import modal lists the exported skills (``trajectory-plan/*``) and prefers
# the lossless companion config (``trajectory-plan-config/*``) when present;
# otherwise it reconstructs a reduced-fidelity config from the exported skill.


async def list_nova_plan_names(api_client, cell: str) -> list[str]:
    """Return the names of all exported plans (``trajectory-plan/*``), sorted.

    ``api_client`` is an open ``wandelbots_api_client.v2.ApiClient`` (the caller
    owns its lifecycle).
    """
    store_api = wb_v2.StoreObjectApi(api_client)
    keys = await store_api.list_all_object_keys(cell=cell)
    names = [
        key[len(TRAJECTORY_PLAN_PREFIX) :]
        for key in (keys or [])
        # Exclude the companion config keys, which share the trajectory-plan
        # stem but live under their own (longer) prefix.
        if key.startswith(TRAJECTORY_PLAN_PREFIX)
        and not key.startswith(TRAJECTORY_PLAN_CONFIG_PREFIX)
    ]
    return sorted(names)


async def load_nova_plan(
    api_client, cell: str, name: str
) -> tuple[dict | None, TrajectoryPlannerConfig | None]:
    """Fetch an exported plan and its companion config (either may be None).

    ``api_client`` is an open ``wandelbots_api_client.v2.ApiClient``.
    """
    skill_dict: dict | None = None
    config: TrajectoryPlannerConfig | None = None
    store_api = wb_v2.StoreObjectApi(api_client)
    try:
        raw = await store_api.get_object(
            cell=cell, key=f"{TRAJECTORY_PLAN_PREFIX}{name}"
        )
        skill_dict = json.loads(bytes(raw).decode("utf-8"))
    except Exception as exc:
        carb.log_warn(f"NOVA import: failed to load plan '{name}': {exc}")
    try:
        raw_cfg = await store_api.get_object(
            cell=cell, key=f"{TRAJECTORY_PLAN_CONFIG_PREFIX}{name}"
        )
        config = _config_from_payload(raw_cfg)
    except Exception as exc:
        carb.log_verbose(f"NOVA import: no companion config for '{name}' ({exc}).")
    return skill_dict, config


def _representative_setup(skill_dict: dict) -> dict | None:
    """The motion_group_setup dict from whichever request the skill carries."""
    if not isinstance(skill_dict, dict):
        return None
    req = skill_dict.get("plan_trajectory_request")
    if isinstance(req, dict):
        return req.get("motion_group_setup")
    seg = skill_dict.get("plan_segmented_trajectory")
    if isinstance(seg, dict):
        return seg.get("motion_group_setup")
    cf = skill_dict.get("plan_collision_free_requests")
    if isinstance(cf, list) and cf and isinstance(cf[0], dict):
        return cf[0].get("motion_group_setup")
    return None


def _representative_request(skill_dict: dict) -> dict | None:
    """A representative PlanTrajectoryRequest dict (for start_joint_position)."""
    if not isinstance(skill_dict, dict):
        return None
    req = skill_dict.get("plan_trajectory_request")
    if isinstance(req, dict):
        return req
    seg = skill_dict.get("plan_segmented_trajectory")
    if isinstance(seg, dict):
        segments = seg.get("segments") or []
        if segments and isinstance(segments[0], dict):
            return segments[0].get("plan_trajectory_request")
    cf = skill_dict.get("plan_collision_free_requests")
    if isinstance(cf, list) and cf and isinstance(cf[0], dict):
        return cf[0]
    return None


def exported_skill_model(skill_dict: dict) -> str | None:
    """The stored motion group model name, or None if unavailable."""
    setup = _representative_setup(skill_dict)
    model = setup.get("motion_group_model") if isinstance(setup, dict) else None
    return model or None


def exported_skill_joint_count(skill_dict: dict) -> int | None:
    """Stored DOF, derived from a request's start_joint_position."""
    req = _representative_request(skill_dict)
    start = req.get("start_joint_position") if isinstance(req, dict) else None
    if isinstance(start, (list, tuple)) and start:
        return len(start)
    return None


def _all_target_commands(skill_dict: dict) -> list:
    """Motion-command dicts aligned to the target poses (poses[1:]).

    Empty for collision-free skills (different request shape) or unknown formats.
    """
    if not isinstance(skill_dict, dict):
        return []
    req = skill_dict.get("plan_trajectory_request")
    if isinstance(req, dict):
        return req.get("motion_commands") or []
    seg = skill_dict.get("plan_segmented_trajectory")
    if isinstance(seg, dict):
        cmds: list = []
        for s in seg.get("segments") or []:
            r = s.get("plan_trajectory_request") if isinstance(s, dict) else None
            if isinstance(r, dict):
                cmds.extend(r.get("motion_commands") or [])
        return cmds
    return []


def _find_in_path(path, key):
    """Read ``key`` from a (possibly oneOf-wrapped) MotionCommandPath dict."""
    if not isinstance(path, dict):
        return None
    if key in path:
        return path[key]
    for value in path.values():
        if isinstance(value, dict) and key in value:
            return value[key]
    return None


def _parse_command(cmd: dict) -> tuple[list[float] | None, list[float] | None]:
    """Return (tcp_pose[6], target_joint_position) for one motion command."""
    path = cmd.get("path") if isinstance(cmd, dict) else None
    tcp_pose = None
    target = _find_in_path(path, "target_pose")
    if isinstance(target, dict):
        pose = list(target.get("position") or []) + list(
            target.get("orientation") or []
        )
        tcp_pose = pose if len(pose) >= 6 else None
    joint = _find_in_path(path, "target_joint_position")
    joint = list(joint) if isinstance(joint, (list, tuple)) else None
    return tcp_pose, joint


def extract_pose_rows(skill_dict: dict | None, config) -> list[dict]:
    """Per-pose info for the import preview.

    Returns dicts with ``prim_path``, ``tcp_pose`` (6 floats or None),
    ``joint`` (joint config or None) and ``motion_type``. Geometry comes from the
    exported requests; joint configs / motion types fall back to the companion
    config when the request doesn't carry them.
    """
    metadata = (skill_dict or {}).get("metadata") or {}
    prim_paths = [
        p.get("prim_path")
        for p in (metadata.get("poses") or [])
        if isinstance(p, dict) and p.get("prim_path")
    ]
    if not prim_paths and config is not None:
        prim_paths = [p.prim_path for p in config.poses]

    commands = _all_target_commands(skill_dict)
    # Collision-free plans carry joint targets (no cartesian TCP) in their own
    # request list: request k goes from pose k to pose k+1.
    cf_requests = (
        (skill_dict or {}).get("plan_collision_free_requests")
        if isinstance(skill_dict, dict)
        else None
    )
    if not isinstance(cf_requests, list):
        cf_requests = None
    req = _representative_request(skill_dict)
    start_joint = req.get("start_joint_position") if isinstance(req, dict) else None
    cfg_poses = config.poses if config is not None else []

    def _as_list(value):
        return list(value) if isinstance(value, (list, tuple)) else None

    rows: list[dict] = []
    for i, prim_path in enumerate(prim_paths):
        tcp_pose = None
        joint = None
        motion_type = None
        if cf_requests:
            if i == 0:
                joint = _as_list(cf_requests[0].get("start_joint_position"))
            elif i - 1 < len(cf_requests):
                joint = _as_list(cf_requests[i - 1].get("target"))
        elif i == 0:
            joint = _as_list(start_joint)
        elif i - 1 < len(commands):
            tcp_pose, joint = _parse_command(commands[i - 1])
        if i < len(cfg_poses):
            pc = cfg_poses[i]
            motion_type = getattr(pc, "motion_type", None)
            if joint is None:
                joint = pc.selected_joint_config or (
                    pc.joint_configs[pc.selected_config_idx]
                    if pc.joint_configs
                    and 0 <= pc.selected_config_idx < len(pc.joint_configs)
                    else None
                )
        rows.append(
            {
                "prim_path": prim_path,
                "tcp_pose": tcp_pose,
                "joint": joint,
                "motion_type": motion_type,
            }
        )
    return rows


def config_from_exported_skill(skill_dict: dict) -> TrajectoryPlannerConfig:
    """Reduced-fidelity TrajectoryPlannerConfig from an exported skill.

    Used only when no companion ``trajectory-plan-config`` exists. Recovers the
    skill name, motion group, TCP, collision scene and per-pose prim paths from
    the export metadata; per-pose motion types default to ``PathCartesianPTP``
    (the exported NOVA request format does not preserve them losslessly).
    """
    metadata = skill_dict.get("metadata") or {}
    mg_path = skill_dict.get("robot_prim_path") or metadata.get(
        "motion_group_prim_path"
    )
    pose_paths = [
        p.get("prim_path")
        for p in (metadata.get("poses") or [])
        if isinstance(p, dict) and p.get("prim_path")
    ]
    poses = [PoseConfig(prim_path=path) for path in pose_paths]

    setup = _representative_setup(skill_dict) or {}
    global_limits = setup.get("global_limits")

    return TrajectoryPlannerConfig(
        name=skill_dict.get("name") or "Imported plan",
        robot_prim_path=mg_path,
        tcp_name=skill_dict.get("tcp_name"),
        collision_setup=skill_dict.get("collision_setup"),
        poses=poses,
        plan_collision_free=skill_dict.get("type") == "plan_collision_free",
        global_limits_override=global_limits
        if isinstance(global_limits, dict)
        else None,
    )
