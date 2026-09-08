"""Shared helpers for the trajectory planner service layer.

Every request carries world-frame Cartesian poses plus ``mounting``, the
motion group's current link_0 world pose read from the stage. NOVA composes
that mounting for targets and static colliders alike, so stored world-frame
geometry passes through unchanged and planning stays valid wherever the base
currently stands.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp
import carb
import omni.kit.notification_manager as nm
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models
from wandelbots_api_client.v2.exceptions import OpenApiException

from wandelbots.omni.core.collision.utils import (
    merge_link_chain_extras,
    validate_setup_matches_motion_group,
)
from wandelbots.omni.manipulators import find_motion_group_prim
from wandelbots.omni.utils.prims import PrimUtils


REQUEST_TIMEOUT = 120.0  # Seconds, to stay under the nginx 504 gateway timeout.

# Everything the NOVA client raises for a failed call. Catching plain
# Exception here would swallow bugs in the request we build.
_API_ERRORS = (OpenApiException, aiohttp.ClientError, asyncio.TimeoutError)


def find_current_mounting(
    cell: str,
    controller: str,
    motion_group: str,
    host: str | None = None,
    prim_path: str | None = None,
) -> wb_v2_models.Pose | None:
    """Current world pose of the stage robot's kinematic base as a NOVA
    mounting pose, or None when the robot cannot be resolved.

    Reading it per request is what keeps planning correct for a base that
    moves, such as a gantry-mounted robot.
    """
    motion_group_prim = find_motion_group_prim(
        cell, controller, motion_group, host=host, prim_path=prim_path
    )
    if motion_group_prim is None:
        return None
    base_pose = PrimUtils.get_motion_group_base_world_pose(motion_group_prim)
    return base_pose.to_nova_pose() if base_pose else None


@dataclass
class MotionGroupContext:
    """Pre-fetched context for motion group operations."""

    description: wb_v2_models.MotionGroupDescription
    model_name: str
    tcp_offset: wb_v2_models.Pose | None
    collision_setups: dict | None
    joint_position_limits: list[wb_v2_models.LimitRange] | None
    mounting: wb_v2_models.Pose | None = None


def _find_tcp_offset(description, tcp_name: str | None) -> wb_v2_models.Pose | None:
    if not tcp_name:
        return None
    tcp_data = description.tcps.get(tcp_name)
    return tcp_data.pose if tcp_data else None


async def _fetch_collision_setups(
    api_client, cell: str, collision_setup_name: str
) -> dict | None:
    """The stored collision setup by name, or None when the fetch failed."""
    try:
        collision_setup = await wb_v2.StoreCollisionSetupsApi(
            api_client
        ).get_stored_collision_setup(cell=cell, setup=collision_setup_name)
    except _API_ERRORS as exc:
        carb.log_warn(
            f"Failed to fetch collision setup '{collision_setup_name}': {exc}"
        )
        return None
    return {collision_setup_name: collision_setup}


def _warn_on_foreign_collision_setup(
    collision_setups: dict,
    cell: str,
    controller: str,
    motion_group: str,
    host: str | None,
    motion_group_prim_path: str | None,
) -> None:
    """Warn when a setup's robot-mounted content does not come from the robot
    we are about to plan for. Must run before the canonical merge, which
    replaces the stage paths in the link chain with canonical model ids.
    """
    motion_group_prim = find_motion_group_prim(
        cell, controller, motion_group, host=host, prim_path=motion_group_prim_path
    )
    if motion_group_prim is None:
        return
    for name, setup in collision_setups.items():
        mismatches = validate_setup_matches_motion_group(setup, motion_group_prim)
        if not mismatches:
            continue
        text = (
            f"Collision setup '{name}' may not match motion group "
            f"{motion_group}:\n- " + "\n- ".join(mismatches)
        )
        carb.log_warn(text)
        nm.post_notification(text=text, status=nm.NotificationStatus.WARNING)


async def _merge_canonical_link_chain(
    api_client, collision_setups: dict, motion_group_model: str
) -> None:
    """Rebuild each setup's link chain from the canonical collision model with
    the stored link extras merged on top.

    The robot's own geometry is never stored, and the server has no fallback:
    without a link chain the robot is shapeless and nothing collides.
    """
    try:
        canonical = await wb_v2.MotionGroupModelsApi(
            api_client
        ).get_motion_group_collision_model(motion_group_model=motion_group_model)
    except _API_ERRORS as exc:
        carb.log_warn(
            f"Failed to fetch the collision model for '{motion_group_model}': "
            f"{exc} - collision checking would see no robot geometry."
        )
        return
    for setup in collision_setups.values():
        setup.link_chain = merge_link_chain_extras(canonical, setup.link_chain)


async def fetch_motion_group_context(
    api_client,
    cell: str,
    controller: str,
    motion_group: str,
    tcp_name: str | None = None,
    collision_setup_name: str | None = None,
    include_joint_limits: bool = False,
    motion_group_prim_path: str | None = None,
) -> MotionGroupContext:
    """Fetch the motion group description, TCP offset, collision setup, joint
    limits and current mounting in one place, for IK, planning, FK and
    execution alike.

    Pass ``motion_group_prim_path`` when the caller already knows the stage
    robot: it pins the mounting and provenance lookup to that prim instead of
    a registry match, which is ambiguous when two instances reuse the same
    identifiers. Without it, the API client's host picks the prim registered
    for the same instance.
    """
    client_host = api_client.configuration.host
    carb.log_verbose(
        f"fetch_motion_group_context: cell={cell}, controller={controller}, "
        f"motion_group={motion_group}, tcp={tcp_name}, "
        f"collision={collision_setup_name}"
    )
    description = await wb_v2.MotionGroupApi(api_client).get_motion_group_description(
        cell=cell,
        controller=controller,
        motion_group=motion_group,
    )
    carb.log_verbose(
        f"fetch_motion_group_context: model={description.motion_group_model}, "
        f"tcps={list(description.tcps.keys()) if description.tcps else []}"
    )

    collision_setups = None
    if collision_setup_name:
        collision_setups = await _fetch_collision_setups(
            api_client, cell, collision_setup_name
        )
    if collision_setups:
        _warn_on_foreign_collision_setup(
            collision_setups,
            cell,
            controller,
            motion_group,
            client_host,
            motion_group_prim_path,
        )
        await _merge_canonical_link_chain(
            api_client, collision_setups, description.motion_group_model
        )

    mounting = find_current_mounting(
        cell,
        controller,
        motion_group,
        host=client_host,
        prim_path=motion_group_prim_path,
    )
    if mounting is None:
        carb.log_warn(
            f"No stage mounting resolved for {cell}/{controller}/{motion_group}; "
            "sending mounting=None (base assumed at the stage world origin)."
        )

    return MotionGroupContext(
        description=description,
        model_name=description.motion_group_model,
        tcp_offset=_find_tcp_offset(description, tcp_name),
        collision_setups=collision_setups,
        joint_position_limits=(
            extract_joint_position_limits(description) if include_joint_limits else None
        ),
        mounting=mounting,
    )


def extract_joint_position_limits(
    description,
) -> list[wb_v2_models.LimitRange] | None:
    """Extract joint position limits from the motion group description's operation limits."""
    auto_limits = getattr(
        getattr(description, "operation_limits", None), "auto_limits", None
    )
    if auto_limits and auto_limits.joints:
        return [
            wb_v2_models.LimitRange(
                lower_limit=joint.position.lower_limit,
                upper_limit=joint.position.upper_limit,
            )
            for joint in auto_limits.joints
            if joint.position
        ]
    return None


def build_global_limits(
    description,
    tcp_velocity_limit: float | None,
    tcp_acceleration_limit: float | None,
) -> wb_v2_models.LimitSet | None:
    """Build a LimitSet for global_limits, starting from auto_limits and overriding TCP values."""
    base_limits = description.operation_limits.auto_limits
    has_override = (tcp_velocity_limit and tcp_velocity_limit > 0) or (
        tcp_acceleration_limit and tcp_acceleration_limit > 0
    )
    if not has_override:
        return base_limits

    tcp_kwargs = {}
    if base_limits and base_limits.tcp:
        tcp_kwargs["velocity"] = base_limits.tcp.velocity
        tcp_kwargs["acceleration"] = base_limits.tcp.acceleration
        tcp_kwargs["orientation_velocity"] = base_limits.tcp.orientation_velocity
        tcp_kwargs["orientation_acceleration"] = (
            base_limits.tcp.orientation_acceleration
        )
    if tcp_velocity_limit and tcp_velocity_limit > 0:
        tcp_kwargs["velocity"] = tcp_velocity_limit
    if tcp_acceleration_limit and tcp_acceleration_limit > 0:
        tcp_kwargs["acceleration"] = tcp_acceleration_limit

    tcp_limits = wb_v2_models.CartesianLimits(**tcp_kwargs)

    if base_limits:
        return wb_v2_models.LimitSet(
            joints=base_limits.joints,
            tcp=tcp_limits,
            elbow=base_limits.elbow,
            flange=base_limits.flange,
        )
    return wb_v2_models.LimitSet(tcp=tcp_limits)


def build_motion_group_setup(
    description,
    tcp_offset: wb_v2_models.Pose | None,
    tcp_velocity_limit: float | None = None,
    tcp_acceleration_limit: float | None = None,
    cycle_time: float | None = None,
    payload_name: str | None = None,
    payload_mass: float | None = None,
    mounting: wb_v2_models.Pose | None = None,
) -> wb_v2_models.MotionGroupSetup:
    """Build a MotionGroupSetup from the description and optional overrides."""
    global_limits = build_global_limits(
        description, tcp_velocity_limit, tcp_acceleration_limit
    )
    payload = None
    if payload_name and payload_mass is not None:
        payload = wb_v2_models.Payload(name=payload_name, payload=payload_mass)

    return wb_v2_models.MotionGroupSetup(
        motion_group_model=description.motion_group_model,
        cycle_time=cycle_time if cycle_time else description.cycle_time,
        # The stage-derived base pose, not description.mounting: the stored
        # value anchors the base plate and can be stale, which shifted
        # executed motion into the ground in the past.
        mounting=mounting,
        tcp_offset=tcp_offset,
        global_limits=global_limits,
        payload=payload,
    )
