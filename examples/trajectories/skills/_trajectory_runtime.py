"""Shared helpers for the generated trajectory programs.

Each ``<name>.py`` next to this module is a self-contained, directly executable
Wandelbots NOVA program generated from an Isaac Sim trajectory plan. The poses,
joint targets, TCP offsets, mounting and limits are all spelled out as plain
Python literals in the generated file so they can be edited by hand. This module
only holds the small, repetitive setup/teardown helpers shared by every program.

Run a generated program with:

    python skill_<name>.py
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

import nova
from nova import api
from nova.actions import joint_ptp
from nova.cell import virtual_controller
from nova.types import MotionSettings


logger = logging.getLogger(__name__)


def make_algorithm(algorithm: Optional[dict]) -> "api.models.CollisionFreeAlgorithm":
    """Rebuild the collision-free planning algorithm from its stored dict.

    Falls back to the SDK default (RRTConnect) when the skill carried no algorithm.
    """
    if algorithm:
        return api.models.CollisionFreeAlgorithm.model_validate(algorithm)
    return api.models.CollisionFreeAlgorithm(api.models.RRTConnectAlgorithm())


async def fetch_collision_setup(
    ctx: "nova.ProgramContext",
    name: Optional[str],
) -> "Optional[api.models.CollisionSetup]":
    """Fetch a collision setup (scene) from the NOVA store by name.

    Collision-free planning needs the full ``CollisionSetup`` geometry, but the
    skill only stores its name — the scene lives server-side in the cell's store.
    Returns ``None`` when no name is set; on a fetch failure it warns and returns
    ``None`` so the caller can surface a clear message instead of planning against
    a missing scene.
    """
    if not name:
        return None
    try:
        setup = await ctx.nova.api.store_collision_setups_api.get_stored_collision_setup(
            cell=ctx.cell.cell_id, setup=name
        )
        logger.info("Fetched collision setup %r from the store", name)
        return setup
    except Exception as exc:
        logger.warning(
            "Could not fetch collision setup %r (collision avoidance will be "
            "skipped): %s",
            name,
            exc,
        )
        return None

# Standard TCPs that always exist on a virtual controller; never recreated.
_BUILTIN_TCPS = {"Flange"}


def make_virtual_controller(
    name: str,
    manufacturer: "api.models.Manufacturer",
    controller_type: str,
):
    """Build the ``virtual_controller`` config used in the program preconditions."""
    return virtual_controller(name=name, manufacturer=manufacturer, type=controller_type)


async def ensure_tcp(
    motion_group,
    tcp_name: Optional[str],
    position: Sequence[float],
    orientation: Sequence[float],
) -> None:
    """(Re)create a custom TCP on the virtual controller from its offset.

    Custom TCPs only exist on the source robot, so the trajectory's TCP must be
    registered before planning. ``Flange`` and unnamed TCPs are left untouched.
    """
    if not tcp_name or tcp_name in _BUILTIN_TCPS:
        return
    try:
        await motion_group.ensure_virtual_tcp(
            api.models.RobotTcp(
                id=tcp_name,
                name=tcp_name,
                position=list(position),
                orientation=list(orientation),
                orientation_type=api.models.OrientationType.ROTATION_VECTOR,
            )
        )
        logger.info("Ensured TCP %r on motion group", tcp_name)
    except Exception as exc:  # best-effort; planning will surface a clearer error
        logger.warning("Could not ensure TCP %r: %s", tcp_name, exc)


async def set_mounting(
    ctx: "nova.ProgramContext",
    controller_id: str,
    motion_group_id: str,
    position: Sequence[float],
    orientation: Sequence[float],
) -> None:
    """Apply a non-identity base mounting (rotation-vector orientation)."""
    if not any(position) and not any(orientation):
        return  # identity mounting → leave the controller at its default origin
    try:
        await ctx.nova.api.virtual_controller_api.set_virtual_controller_mounting(
            cell=ctx.cell.cell_id,
            controller=controller_id,
            motion_group=motion_group_id,
            coordinate_system=api.models.CoordinateSystem(
                coordinate_system="mounting",
                reference_coordinate_system="world",
                position=list(position),
                orientation=list(orientation),
                orientation_type=api.models.OrientationType.ROTATION_VECTOR,
            ),
        )
        logger.info("Applied mounting position=%s orientation=%s", list(position), list(orientation))
    except Exception as exc:
        logger.warning("Could not set mounting (continuing at origin): %s", exc)


async def move_to_start(
    motion_group,
    tcp: Optional[str],
    start_joints: Optional[Sequence[float]],
    settings: MotionSettings,
) -> None:
    """Move the (virtual) robot to the trajectory's taught start joints.

    This first ``joint_ptp`` is planned from the current pose; everything after
    is replayed exactly as taught (anchored at ``start_joints``).
    """
    if not start_joints:
        return
    logger.info("Moving to trajectory start %s", list(start_joints))
    await motion_group.plan_and_execute([joint_ptp(tuple(start_joints), settings=settings)], tcp=tcp)
