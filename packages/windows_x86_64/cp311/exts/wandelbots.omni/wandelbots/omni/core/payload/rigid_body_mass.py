"""Mass properties of rigid bodies as PhysX computes them.

PhysX derives a body's mass, centre of mass and inertia from its colliders
and the authored MassAPI attributes; this is what the Physics toolbar's mass
distribution manipulator shows. The property query interface hands out the
same numbers, asynchronously, because mesh colliders have to be cooked first.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import omni.physx
from omni.physx.bindings._physx import (
    PhysxPropertyQueryMode,
    PhysxPropertyQueryResult,
    PhysxPropertyQueryRigidBodyResponse,
)
from pxr import Gf, PhysicsSchemaTools, Usd, UsdPhysics, UsdUtils

QUERY_TIMEOUT_MILLISECONDS = 60000


class RigidBodyQueryError(RuntimeError):
    """PhysX could not deliver the mass properties of a rigid body."""


@dataclass(frozen=True)
class RigidBodyMassProperties:
    """What PhysX reports for one rigid body, in the stage's units.

    The centre of mass is in the body's rigid frame with the body's scale
    baked in; the inertia is diagonal along the principal axes, whose
    orientation in the body frame is the quaternion.
    """

    prim_path: str
    mass: float
    center_of_mass: Gf.Vec3d
    diagonal_inertia: Gf.Vec3d
    principal_axes: Gf.Quatd


def find_rigid_bodies(stage: Usd.Stage) -> list[Usd.Prim]:
    return [prim for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.RigidBodyAPI)]


async def query_rigid_body_mass(
    prim: Usd.Prim, timeout_milliseconds: int = QUERY_TIMEOUT_MILLISECONDS
) -> RigidBodyMassProperties:
    prim_path = prim.GetPath().pathString
    finished = asyncio.get_running_loop().create_future()
    responses: list[PhysxPropertyQueryRigidBodyResponse] = []

    def on_rigid_body(response: PhysxPropertyQueryRigidBodyResponse) -> None:
        responses.append(response)
        # An error ends the query without a finished callback.
        if response.result != PhysxPropertyQueryResult.VALID and not finished.done():
            finished.set_result(None)

    def on_finished() -> None:
        if not finished.done():
            finished.set_result(None)

    omni.physx.get_physx_property_query_interface().query_prim(
        stage_id=UsdUtils.StageCache.Get().Insert(prim.GetStage()).ToLongInt(),
        prim_id=PhysicsSchemaTools.sdfPathToInt(prim.GetPath()),
        query_mode=PhysxPropertyQueryMode.QUERY_RIGID_BODY_WITH_COLLIDERS,
        timeout_ms=timeout_milliseconds,
        finished_fn=on_finished,
        rigid_body_fn=on_rigid_body,
    )
    # PhysX reports its own timeout through the response; the margin only
    # catches a query that never calls back at all.
    await asyncio.wait_for(finished, timeout=timeout_milliseconds / 1000.0 + 5.0)

    if not responses:
        raise RigidBodyQueryError(f"PhysX returned no rigid body for {prim_path}.")
    response = responses[0]
    if response.result != PhysxPropertyQueryResult.VALID:
        raise RigidBodyQueryError(
            f"PhysX rigid body query for {prim_path} failed: {response.result.name}"
        )
    axis_x, axis_y, axis_z, real = response.principal_axes
    return RigidBodyMassProperties(
        prim_path=prim_path,
        mass=float(response.mass),
        center_of_mass=Gf.Vec3d(*response.center_of_mass),
        diagonal_inertia=Gf.Vec3d(*response.inertia),
        principal_axes=Gf.Quatd(real, axis_x, axis_y, axis_z),
    )
