"""Shared helpers for the trajectory planner service layer."""

from __future__ import annotations

from dataclasses import dataclass

import carb
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models


_REQUEST_TIMEOUT = 120.0  # seconds – avoid 504 gateway timeouts from nginx


@dataclass
class MotionGroupContext:
    """Pre-fetched context for motion group operations."""

    description: object  # MotionGroupDescription
    model_name: str
    tcp_offset: wb_v2_models.Pose | None
    collision_setups: dict | None
    joint_position_limits: list[wb_v2_models.LimitRange] | None


async def fetch_motion_group_context(
    api_client,
    cell: str,
    controller: str,
    motion_group: str,
    tcp_name: str | None = None,
    collision_setup_name: str | None = None,
    include_joint_limits: bool = False,
) -> MotionGroupContext:
    """Fetch the motion group description, TCP offset, collision setup, and joint limits.

    This consolidates the repeated pattern of fetching these values that was
    previously duplicated across IK, planning, FK, and execution methods.
    """
    mg_api = wb_v2.MotionGroupApi(api_client)
    carb.log_verbose(
        f"fetch_motion_group_context: cell={cell}, controller={controller}, "
        f"motion_group={motion_group}, tcp={tcp_name}, "
        f"collision={collision_setup_name}"
    )
    description = await mg_api.get_motion_group_description(
        cell=cell,
        controller=controller,
        motion_group=motion_group,
    )
    carb.log_verbose(
        f"fetch_motion_group_context: model={description.motion_group_model}, "
        f"tcps={list(description.tcps.keys()) if description.tcps else []}"
    )

    tcp_offset = None
    if tcp_name:
        tcp_data = description.tcps.get(tcp_name)
        if tcp_data:
            tcp_offset = tcp_data.pose

    collision_setups = None
    if collision_setup_name:
        try:
            collision_setup = await wb_v2.StoreCollisionSetupsApi(
                api_client
            ).get_stored_collision_setup(cell=cell, setup=collision_setup_name)
            collision_setups = {collision_setup_name: collision_setup}
        except Exception as exc:
            carb.log_warn(
                f"Failed to fetch collision setup '{collision_setup_name}': {exc}"
            )

    joint_position_limits = None
    if include_joint_limits:
        joint_position_limits = extract_joint_position_limits(description)

    return MotionGroupContext(
        description=description,
        model_name=description.motion_group_model,
        tcp_offset=tcp_offset,
        collision_setups=collision_setups,
        joint_position_limits=joint_position_limits,
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
                lower_limit=j.position.lower_limit,
                upper_limit=j.position.upper_limit,
            )
            for j in auto_limits.joints
            if j.position
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
        mounting=description.mounting,
        tcp_offset=tcp_offset,
        global_limits=global_limits,
        payload=payload,
    )
