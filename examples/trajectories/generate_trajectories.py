#!/usr/bin/env python3
"""Fetch Isaac Sim trajectory plans from the NOVA object store and generate
directly executable, fully editable Wandelbots NOVA programs.

The Isaac Sim trajectory planner exports each skill as an ``ExportedSkill`` JSON
payload and stores it, versioned, in the NOVA object store under the key prefix
``trajectory-plan/<name>``. This script:

  1. lists every ``trajectory-plan/*`` object (excluding the
     ``trajectory-plan-config/*`` companions),
  2. resolves the stored robot model to a canonical virtual-controller type via
     ``GET /api/v2/robot-configurations``,
  3. writes one ``<out>/skill_<name>.py`` per plan — a self-contained program
     whose poses, joint targets, TCP offset, mounting and limits are all inlined
     as plain Python literals so they can be edited by hand. No JSON files are
     written; the generated programs depend only on ``_trajectory_runtime.py``.

Usage::

    python generate_trajectories.py --host http://<nova-host> --cell cell

Connection settings fall back to the env vars ``NOVA_API`` / ``NOVA_HOST``,
``NOVA_ACCESS_TOKEN`` and ``CELL`` (loaded from a local ``.env`` if present).
The generated ``skill_<name>.py`` files are git-ignored (see ``skills/.gitignore``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

import wandelbots_api_client.v2 as wb_v2


logger = logging.getLogger("generate_trajectories")

TRAJECTORY_PLAN_PREFIX = "trajectory-plan/"
TRAJECTORY_PLAN_CONFIG_PREFIX = "trajectory-plan-config/"
RUNTIME_MODULE = "_trajectory_runtime.py"

# Normalized model prefix -> nova api.models.Manufacturer member name.
# Mirrors wandelbots.omni.ui.manufacturers.MANUFACTURER_PREFIXES (plus Staubli).
_MANUFACTURER_PREFIXES: dict[str, str] = {
    "universalrobots": "UNIVERSALROBOTS",
    "yaskawa": "YASKAWA",
    "staubli": "STAUBLI",
    "fanuc": "FANUC",
    "kuka": "KUKA",
    "abb": "ABB",
}


# -- naming / resolution helpers ----------------------------------------------


def _normalize(name: str) -> str:
    """Lower-case and strip separators (mirrors virtual_controller_service)."""
    return name.lower().replace("-", "").replace("_", "").replace(" ", "")


def resolve_controller_type(model_name: str, available_types: list[str]) -> Optional[str]:
    """Match a stored model to a canonical controller type, ignoring case/separators."""
    normalized = _normalize(model_name)
    for type_str in available_types:
        if _normalize(type_str) == normalized:
            return type_str
    return None


def infer_manufacturer_member(model_name: str) -> Optional[str]:
    """Infer the ``Manufacturer`` enum member name from a model string prefix."""
    normalized = _normalize(model_name)
    for prefix in sorted(_MANUFACTURER_PREFIXES, key=len, reverse=True):
        if normalized.startswith(prefix):
            return _MANUFACTURER_PREFIXES[prefix]
    return None


def _module_slug(value: str) -> str:
    """Safe python identifier fragment for a plan name."""
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_")
    if not slug:
        slug = "trajectory"
    if slug[0].isdigit():
        slug = f"_{slug}"
    return slug


def _module_basename(name: str) -> str:
    """Generated module name for a plan: ``skill_<name>`` (no extension)."""
    return f"skill_{_module_slug(name)}"


def _controller_name(value: str) -> str:
    """Sanitize into a valid NOVA controller name ``^[a-z][a-z0-9-]{0,61}[a-z0-9]$``."""
    name = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not name or not name[0].isalpha():
        name = f"c-{name}".strip("-")
    if len(name) < 2:
        name = f"{name}-mg"
    return name[:63].rstrip("-")


# -- literal formatting -------------------------------------------------------


def _num(value: Any) -> str:
    return repr(float(value))


def _vec(values: Any) -> str:
    return "[" + ", ".join(_num(v) for v in (values or [])) + "]"


def _tuple(values: Any) -> str:
    return "(" + ", ".join(_num(v) for v in (values or [])) + ")"


def _pose(target_pose: dict) -> str:
    coords = list(target_pose.get("position") or [0, 0, 0]) + list(
        target_pose.get("orientation") or [0, 0, 0]
    )
    return "Pose((" + ", ".join(_num(c) for c in coords) + "))"


def _action_line(cmd: dict) -> Optional[str]:
    """Render one motion command as an action constructor call, or None to skip."""
    path = (cmd or {}).get("path") or {}
    kind = path.get("path_definition_name")
    target_pose = path.get("target_pose")
    target_joints = path.get("target_joint_position")
    if kind in (None, "PathCartesianPTP") and isinstance(target_pose, dict):
        return f"cartesian_ptp({_pose(target_pose)}, settings=SETTINGS)"
    if kind == "PathLine" and isinstance(target_pose, dict):
        return f"linear({_pose(target_pose)}, settings=SETTINGS)"
    if kind == "PathJointPTP" and target_joints is not None:
        return f"joint_ptp({_tuple(target_joints)}, settings=SETTINGS)"
    logger.warning("Skipping unsupported motion command path %r", kind)
    return None


def _settings_line(setup: dict, limits_override: Optional[dict] = None) -> str:
    tcp = (limits_override or {}).get("tcp") if limits_override else None
    if not tcp:
        tcp = (setup.get("global_limits") or {}).get("tcp") or {}
    kwargs = []
    if tcp.get("velocity") is not None:
        kwargs.append(f"tcp_velocity_limit={_num(tcp['velocity'])}")
    if tcp.get("acceleration") is not None:
        kwargs.append(f"tcp_acceleration_limit={_num(tcp['acceleration'])}")
    return f"MotionSettings({', '.join(kwargs)})"


# -- skill introspection ------------------------------------------------------


def _representative_setup(skill: dict) -> dict:
    req = skill.get("plan_trajectory_request")
    if isinstance(req, dict):
        return req.get("motion_group_setup") or {}
    seg = skill.get("plan_segmented_trajectory")
    if isinstance(seg, dict):
        return seg.get("motion_group_setup") or {}
    cf = skill.get("plan_collision_free_requests")
    if isinstance(cf, list) and cf and isinstance(cf[0], dict):
        return cf[0].get("motion_group_setup") or {}
    return {}


# -- program rendering --------------------------------------------------------


def _header(name: str, version: str, model: Optional[str], controller_type: str, kind: str) -> str:
    return (
        f'"""Auto-generated from NOVA store key {TRAJECTORY_PLAN_PREFIX + name!r} '
        f"(version {version}).\n\n"
        f"Robot model {model!r} -> virtual controller type {controller_type!r}.\n"
        f"Payload type: {kind}.\n\n"
        "This program is fully self-contained: every pose / joint target, the TCP\n"
        "offset, the base mounting and the motion limits are spelled out as Python\n"
        "literals below — edit them freely. Regenerate with generate_trajectories.py\n"
        "to overwrite local edits.\n\n"
        "Run it directly:  python "
        f"{_module_basename(name)}.py\n"
        '"""'
    )


def _common_constants(controller_name: str, manufacturer_member: Optional[str], controller_type: str, model: Optional[str]) -> list[str]:
    if manufacturer_member:
        manufacturer_line = f"MANUFACTURER = api.models.Manufacturer.{manufacturer_member}"
    else:
        manufacturer_line = (
            "MANUFACTURER = None  # TODO: manufacturer could not be inferred from "
            f"model {model!r}; set api.models.Manufacturer.<NAME>."
        )
    return [
        f"CONTROLLER_NAME = {controller_name!r}",
        manufacturer_line,
        f"CONTROLLER_TYPE = {controller_type!r}",
    ]


def _mounting_constants(setup: dict) -> list[str]:
    mounting = setup.get("mounting") or {}
    return [
        "# Robot base mounting in the world frame (rotation-vector orientation).",
        f"MOUNTING_POSITION = {_vec(mounting.get('position'))}",
        f"MOUNTING_ORIENTATION = {_vec(mounting.get('orientation'))}",
    ]


def _tcp_offset_constants(setup: dict) -> list[str]:
    offset = setup.get("tcp_offset") or {}
    return [
        "# TCP offset registered on the virtual controller (rotation-vector orientation).",
        f"TCP_OFFSET_POSITION = {_vec(offset.get('position'))}",
        f"TCP_OFFSET_ORIENTATION = {_vec(offset.get('orientation'))}",
    ]


def _indent(lines: list[str], spaces: int) -> list[str]:
    pad = " " * spaces
    return [pad + line if line else line for line in lines]


def _wrap_program(name: str, header: str, constants: list[str], body: list[str]) -> str:
    const_block = "\n".join(constants)
    body_block = "\n".join(_indent(body, 8))
    return f'''{header}

from __future__ import annotations

import nova
from nova import api, run_program
from nova.actions import cartesian_ptp, collision_free, joint_ptp, linear
from nova.program import ProgramPreconditions
from nova.types import MotionSettings, Pose

from _trajectory_runtime import (
    ensure_tcp,
    fetch_collision_setup,
    make_algorithm,
    make_virtual_controller,
    move_to_start,
    set_mounting,
)


{const_block}


@nova.program(
    name={name!r},
    preconditions=ProgramPreconditions(
        controllers=[make_virtual_controller(CONTROLLER_NAME, MANUFACTURER, CONTROLLER_TYPE)],
        cleanup_controllers=False,
    ),
)
async def main(ctx: nova.ProgramContext) -> None:
    controller = await ctx.cell.controller(CONTROLLER_NAME)
    async with controller[0] as motion_group:
{body_block}


if __name__ == "__main__":
    run_program(main)
'''


def _render_single(skill: dict, request: dict) -> tuple[list[str], list[str]]:
    setup = request.get("motion_group_setup") or {}
    tcp = skill.get("tcp_name")
    action_lines = [line for cmd in (request.get("motion_commands") or []) if (line := _action_line(cmd))]
    constants = [
        f"TCP = {tcp!r}",
        "",
        *_tcp_offset_constants(setup),
        "",
        *_mounting_constants(setup),
        "",
        "# Joint position the robot moves to before replaying the taught trajectory.",
        f"START_JOINTS = {_tuple(request.get('start_joint_position'))}",
        "",
        "# TCP velocity / acceleration limits (mm/s, mm/s^2).",
        f"SETTINGS = {_settings_line(setup)}",
    ]
    body = [
        "await ensure_tcp(motion_group, TCP, TCP_OFFSET_POSITION, TCP_OFFSET_ORIENTATION)",
        "await set_mounting(ctx, controller.id, motion_group.id, MOUNTING_POSITION, MOUNTING_ORIENTATION)",
        "",
        "# The taught motion — edit the poses to change the trajectory.",
        "actions = [",
        *[f"    {line}," for line in action_lines],
        "]",
        "",
        "await move_to_start(motion_group, TCP, START_JOINTS, SETTINGS)",
        "await motion_group.plan_and_execute(actions, tcp=TCP, start_joint_position=START_JOINTS)",
    ]
    return constants, body


def _render_collision_free(skill: dict, requests: list[dict]) -> tuple[list[str], list[str]]:
    setup = requests[0].get("motion_group_setup") or {}
    tcp = skill.get("tcp_name")
    collision_setup = skill.get("collision_setup")
    algorithm = requests[0].get("algorithm")
    target_lines = [f"    {_tuple(req.get('target'))}," for req in requests if req.get("target")]
    constants = [
        f"TCP = {tcp!r}",
        "",
        *_tcp_offset_constants(setup),
        "",
        *_mounting_constants(setup),
        "",
        "# Joint position the robot moves to before replaying the targets.",
        f"START_JOINTS = {_tuple(requests[0].get('start_joint_position'))}",
        "",
        "# Collision scene (server-side setup) planned against. Fetched by name from",
        "# the NOVA store at runtime so collision avoidance is re-applied on replay.",
        f"COLLISION_SETUP = {collision_setup!r}",
        "",
        "# Collision-free planning algorithm captured at export; edit or set to None",
        "# to fall back to the SDK default (RRTConnect).",
        f"ALGORITHM = {algorithm!r}",
        "",
        "# Planned joint targets — each reached with collision-free planning.",
        "JOINT_TARGETS = [",
        *target_lines,
        "]",
        "",
        "# TCP velocity / acceleration limits (mm/s, mm/s^2).",
        f"SETTINGS = {_settings_line(setup)}",
    ]
    body = [
        "await ensure_tcp(motion_group, TCP, TCP_OFFSET_POSITION, TCP_OFFSET_ORIENTATION)",
        "await set_mounting(ctx, controller.id, motion_group.id, MOUNTING_POSITION, MOUNTING_ORIENTATION)",
        "",
        "collision_setup = await fetch_collision_setup(ctx, COLLISION_SETUP)",
        "actions = [",
        "    collision_free(",
        "        target,",
        "        settings=SETTINGS,",
        "        collision_setup=collision_setup,",
        "        algorithm=make_algorithm(ALGORITHM),",
        "    )",
        "    for target in JOINT_TARGETS",
        "]",
        "",
        "await move_to_start(motion_group, TCP, START_JOINTS, SETTINGS)",
        "await motion_group.plan_and_execute(actions, tcp=TCP, start_joint_position=START_JOINTS)",
    ]
    return constants, body


def _render_segmented(skill: dict, plan: dict) -> tuple[list[str], list[str]]:
    shared_setup = plan.get("motion_group_setup") or {}
    segments = plan.get("segments") or []
    constants = [
        *_mounting_constants(shared_setup),
        "",
        "# TCP velocity / acceleration limits (mm/s, mm/s^2).",
        f"SETTINGS = {_settings_line(shared_setup, plan.get('limits_override'))}",
    ]

    body: list[str] = [
        "await set_mounting(ctx, controller.id, motion_group.id, MOUNTING_POSITION, MOUNTING_ORIENTATION)",
        "",
    ]
    # Register every segment's TCP (deduplicated, order preserved).
    seen: set[str] = set()
    body.append("# Register the custom TCP(s) used across the segments.")
    for segment in segments:
        seg_setup = (segment.get("plan_trajectory_request") or {}).get("motion_group_setup") or {}
        tcp = segment.get("tcp_name") or skill.get("tcp_name")
        if not tcp or tcp in seen:
            continue
        seen.add(tcp)
        offset = seg_setup.get("tcp_offset") or {}
        body.append(
            f"await ensure_tcp(motion_group, {tcp!r}, "
            f"{_vec(offset.get('position'))}, {_vec(offset.get('orientation'))})"
        )
    body.append("")

    for index, segment in enumerate(segments):
        request = segment.get("plan_trajectory_request") or {}
        tcp = segment.get("tcp_name") or skill.get("tcp_name")
        action_lines = [line for cmd in (request.get("motion_commands") or []) if (line := _action_line(cmd))]
        start = _tuple(request.get("start_joint_position"))
        if segment.get("blending") is not None:
            body.append(f"# NOTE: inter-segment blending after segment {index} is dropped in this high-level replay.")
        body.append(f"# --- Segment {index} (TCP {tcp!r}) ---")
        body.append(f"segment_{index}_actions = [")
        body.extend(f"    {line}," for line in action_lines)
        body.append("]")
        if index == 0:
            body.append(f"await move_to_start(motion_group, {tcp!r}, {start}, SETTINGS)")
        body.append(
            f"await motion_group.plan_and_execute(segment_{index}_actions, "
            f"tcp={tcp!r}, start_joint_position={start})"
        )
        body.append("")
    return constants, body


def render_program(name: str, skill: dict, available_types: list[str]) -> str:
    """Render a complete, runnable program for one exported skill (pure / offline)."""
    setup = _representative_setup(skill)
    model = setup.get("motion_group_model")
    manufacturer_member = infer_manufacturer_member(model) if model else None
    controller_type = resolve_controller_type(model, available_types) if model else None
    if model and not controller_type:
        controller_type = model
        logger.warning("Model %r did not match any catalog type; baking the raw model name.", model)
    controller_name = _controller_name(model or name)

    seg = skill.get("plan_segmented_trajectory")
    cf = skill.get("plan_collision_free_requests")
    req = skill.get("plan_trajectory_request")
    if isinstance(seg, dict) and seg.get("segments"):
        kind = "segmented (multi-TCP)"
        variant_constants, body = _render_segmented(skill, seg)
    elif skill.get("type") == "plan_collision_free" and isinstance(cf, list) and cf:
        kind = "collision-free"
        variant_constants, body = _render_collision_free(skill, cf)
    elif isinstance(req, dict):
        kind = "single"
        variant_constants, body = _render_single(skill, req)
    else:
        raise ValueError(f"Skill {name!r} carries no executable trajectory.")

    header = _header(name, str(skill.get("version", "v1")), model, controller_type or "", kind)
    constants = [
        *_common_constants(controller_name, manufacturer_member, controller_type or "", model),
        "",
        *variant_constants,
    ]
    return _wrap_program(name, header, constants, body)


# -- fetch + write ------------------------------------------------------------


def _normalize_base_url(host: str) -> str:
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    if host.endswith("/api/v2"):
        return host
    return f"{host}/api/v2"


def _ensure_runtime(out_dir: Path) -> None:
    """Make sure ``_trajectory_runtime.py`` lives next to the generated programs."""
    src = Path(__file__).parent / "skills" / RUNTIME_MODULE
    dst = out_dir / RUNTIME_MODULE
    if src.resolve() != dst.resolve():
        if not src.exists():
            raise FileNotFoundError(
                f"Runtime module not found at {src}. It ships with this example and "
                "must be present to generate runnable programs."
            )
        shutil.copyfile(src, dst)


async def generate(host: str, cell: str, token: Optional[str], out_dir: Path) -> int:
    base_url = _normalize_base_url(host)
    logger.info("Connecting to %s (cell=%s)", base_url, cell)
    configuration = wb_v2.Configuration(host=base_url, access_token=token)

    async with wb_v2.ApiClient(configuration=configuration) as api_client:
        store_api = wb_v2.StoreObjectApi(api_client)
        configs_api = wb_v2.RobotConfigurationsApi(api_client)

        keys = await store_api.list_all_object_keys(cell=cell) or []
        plan_keys = sorted(
            key
            for key in keys
            if key.startswith(TRAJECTORY_PLAN_PREFIX)
            and not key.startswith(TRAJECTORY_PLAN_CONFIG_PREFIX)
        )
        if not plan_keys:
            logger.warning("No %s* objects found in cell %r.", TRAJECTORY_PLAN_PREFIX, cell)
            return 0
        logger.info("Found %d trajectory plan(s).", len(plan_keys))

        available_types = await configs_api.get_robot_configurations() or []
        logger.info("Loaded %d controller type(s) from the catalog.", len(available_types))

        out_dir.mkdir(parents=True, exist_ok=True)
        _ensure_runtime(out_dir)

        written = 0
        for key in plan_keys:
            name = key[len(TRAJECTORY_PLAN_PREFIX):]
            module = _module_basename(name)
            try:
                raw = await store_api.get_object(cell=cell, key=key)
                skill = json.loads(bytes(raw).decode("utf-8"))
                source = render_program(name, skill, available_types)
            except Exception as exc:
                logger.error("Failed to generate %r: %s", key, exc)
                continue
            (out_dir / f"{module}.py").write_text(source, encoding="utf-8")
            written += 1
            logger.info("Generated %s.py (%s)", module, skill.get("type"))

        logger.info("Done. Wrote %d program(s) to %s", written, out_dir)
        return written


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default=os.environ.get("NOVA_API") or os.environ.get("NOVA_HOST"),
        help="NOVA host or base URL (env: NOVA_API / NOVA_HOST).",
    )
    parser.add_argument(
        "--cell",
        default=os.environ.get("CELL", "cell"),
        help="NOVA cell id (env: CELL, default 'cell').",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("NOVA_ACCESS_TOKEN"),
        help="Access token (env: NOVA_ACCESS_TOKEN). Optional for open instances.",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "skills"),
        help="Output directory (default: ./skills).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    if not args.host:
        parser.error("No host given. Pass --host or set NOVA_API / NOVA_HOST.")

    try:
        count = asyncio.run(generate(args.host, args.cell, args.token, Path(args.out)))
    except (OSError, asyncio.TimeoutError) as exc:
        # aiohttp wraps connection failures in ClientConnectorError (an OSError).
        base = _normalize_base_url(args.host)
        parser.exit(
            2,
            f"ERROR: cannot reach NOVA at {base} ({exc}).\n"
            "Check that the host is correct and reachable (VPN / network), e.g.:\n"
            f"  curl -m 5 {base}/cells/{args.cell}/store/objects\n",
        )
    if count == 0:
        logger.warning("No programs were generated.")


if __name__ == "__main__":
    main()
