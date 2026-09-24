"""Payload data for the NOVA trajectory planner from the stage's rigid bodies.

The planner's ``Payload`` carries the mass [kg], the centre of mass [mm] and
the moments of inertia [kg m^2] in the frame the payload hangs from (the
flange, or a tool frame). Every rigid body in the stage counts: PhysX supplies
each body's values from its colliders and MassAPI attributes, and the bodies
are combined about their common centre of mass in the picked reference frame.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np
import omni.usd
from numpy.typing import NDArray
from pxr import Gf, Usd, UsdGeom, UsdPhysics
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.core.payload.rigid_body_mass import (
    RigidBodyMassProperties,
    RigidBodyQueryError,
    find_rigid_bodies,
    query_rigid_body_mass,
)
from wandelbots.omni.utils.scene import SceneUtils


class PayloadCalculationError(RuntimeError):
    """The stage holds nothing a payload can be derived from."""


CALCULATION_ERRORS = (
    PayloadCalculationError,
    RigidBodyQueryError,
    asyncio.TimeoutError,
)


@dataclass(frozen=True)
class BodyMass:
    """One body in a shared frame, in stage units: mass, centre of mass and
    the inertia tensor about that centre along the frame's axes."""

    prim_path: str
    mass: float
    center_of_mass: NDArray[np.floating]
    inertia_tensor: NDArray[np.floating]


@dataclass(frozen=True)
class PayloadProperties:
    """Mass [kg], centre of mass [mm] and inertia tensor [kg m^2] about the
    centre of mass, in the reference prim's frame."""

    mass: float
    center_of_mass: tuple[float, float, float]
    inertia_tensor: NDArray[np.floating]
    body_paths: tuple[str, ...] = ()
    # Size [mm] of the world-axis box around all bodies' geometry, the yardstick
    # of plausibility_warnings: a stage whose metersPerUnit contradicts its
    # geometry yields a tiny payload with no other visible symptom.
    extent_millimeters: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def moment_of_inertia(self) -> tuple[float, float, float]:
        return tuple(float(value) for value in np.diagonal(self.inertia_tensor))

    @property
    def products_of_inertia(self) -> tuple[float, float, float]:
        """(Ixy, Iyz, Ixz); the planner's payload has no field for them."""
        tensor = self.inertia_tensor
        return float(tensor[0, 1]), float(tensor[1, 2]), float(tensor[0, 2])

    def to_payload(self, name: str) -> wb_v2_models.Payload:
        return wb_v2_models.Payload(
            name=name,
            payload=self.mass,
            center_of_mass=list(self.center_of_mass),
            moment_of_inertia=list(self.moment_of_inertia),
        )


# Osmium is the densest material there is; a payload denser than that has its
# mass or its stage units wrong. Below the density of air it has too little
# mass for its size, the mirror image of the same mistake. The bodies' box
# overestimates their volume, so the density it implies is a lower bound.
MAX_PLAUSIBLE_DENSITY = 22600.0
MIN_PLAUSIBLE_DENSITY = 1.0
# Radius of gyration over the box diagonal. Above one the inertia needs mass
# outside the bodies; below a hundredth it belongs to a far smaller body than
# the geometry shows, which authored inertia attributes often do.
MIN_GYRATION_RATIO = 0.01
MAX_GYRATION_RATIO = 1.0


def plausibility_warnings(properties: PayloadProperties) -> list[str]:
    """Why the payload does not look like a real object, one line per
    finding; empty when it does. Bodies without geometry cannot be judged."""
    extent_meters = np.asarray(properties.extent_millimeters) / 1000.0
    volume = float(np.prod(extent_meters))
    diagonal = float(np.linalg.norm(extent_meters))
    if properties.mass <= 0.0 or volume <= 0.0:
        return []

    size = " x ".join(f"{value:.3g}" for value in properties.extent_millimeters)
    mass = f"{properties.mass:.3g} kg"
    check_units = (
        "Check the stage's metersPerUnit and the bodies' mass and density attributes."
    )
    warnings = []
    density = properties.mass / volume
    if density > MAX_PLAUSIBLE_DENSITY:
        warnings.append(
            f"{mass} inside a {size} mm box is {density:.3g} kg/m3, denser than "
            f"any material. {check_units}"
        )
    elif density < MIN_PLAUSIBLE_DENSITY:
        warnings.append(
            f"{mass} inside a {size} mm box is {density:.3g} kg/m3, lighter than "
            f"air. {check_units}"
        )

    largest_moment = max(max(properties.moment_of_inertia), 0.0)
    gyration_ratio = np.sqrt(largest_moment / properties.mass) / diagonal
    if gyration_ratio > MAX_GYRATION_RATIO:
        warnings.append(
            f"The inertia {largest_moment:.3g} kg m2 needs mass outside the "
            f"bodies' {size} mm box. Check authored inertia and centre of mass "
            "attributes."
        )
    elif gyration_ratio < MIN_GYRATION_RATIO:
        warnings.append(
            f"The inertia {largest_moment:.3g} kg m2 is tiny for {mass} spread "
            f"over {size} mm. Check authored diagonal inertia attributes."
        )
    return warnings


def rigid_world_transform(prim: Usd.Prim) -> Gf.Matrix4d:
    """World transform of the prim with scale and shear removed.

    A payload frame is a rigid frame, so a scaled reference Xform must not
    stretch the centre of mass or the inertia expressed in it. PhysX bodies
    are rigid frames as well; their scale is already baked into the values.
    """
    return Gf.Matrix4d(omni.usd.get_world_transform_matrix(prim)).RemoveScaleShear()


def rigid_bodies_extent(rigid_bodies: list[Usd.Prim]) -> Gf.Vec3d:
    """Size of the world-axis box around the bodies' geometry, in stage units.

    Colliders are often guide-purpose or hidden meshes, so both count.
    """
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        includedPurposes=[
            UsdGeom.Tokens.default_,
            UsdGeom.Tokens.render,
            UsdGeom.Tokens.proxy,
            UsdGeom.Tokens.guide,
        ],
        useExtentsHint=False,
        ignoreVisibility=True,
    )
    total = Gf.Range3d()
    for prim in rigid_bodies:
        total.UnionWith(bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange())
    return Gf.Vec3d(0.0) if total.IsEmpty() else total.GetSize()


def rotation_matrix(orientation: Gf.Quatd) -> NDArray[np.floating]:
    """3x3 rotation for column vectors, rotated = R @ vector. Gf matrices act
    on row vectors (vector * M), so the column form is the transpose."""
    return np.asarray(Gf.Matrix3d(Gf.Rotation(orientation)), dtype=np.float64).T


def body_in_frame(
    body: RigidBodyMassProperties, body_to_frame: Gf.Matrix4d
) -> BodyMass:
    """Express a PhysX body in another rigid frame; body_to_frame maps the
    body's rigid frame into that frame (row-vector convention)."""
    matrix = np.asarray(body_to_frame, dtype=np.float64)
    center = np.asarray(body.center_of_mass, dtype=np.float64) @ matrix[:3, :3]
    center = center + matrix[3, :3]
    principal_to_frame = matrix[:3, :3].T @ rotation_matrix(body.principal_axes)
    inertia = (
        principal_to_frame
        @ np.diag(np.asarray(body.diagonal_inertia, dtype=np.float64))
        @ principal_to_frame.T
    )
    return BodyMass(body.prim_path, body.mass, center, inertia)


def combine_bodies(bodies: list[BodyMass]) -> tuple[float, NDArray, NDArray]:
    """Total mass, common centre of mass and the inertia tensor about it, for
    bodies given in one shared frame (parallel axis theorem)."""
    mass = sum(body.mass for body in bodies)
    if mass <= 0.0:
        raise PayloadCalculationError("The rigid bodies in the stage have no mass.")
    center = sum(body.mass * body.center_of_mass for body in bodies) / mass
    inertia = np.zeros((3, 3))
    for body in bodies:
        offset = body.center_of_mass - center
        inertia = inertia + body.inertia_tensor
        inertia = inertia + body.mass * (
            np.dot(offset, offset) * np.eye(3) - np.outer(offset, offset)
        )
    return mass, center, inertia


async def compute_stage_payload_properties(
    stage: Usd.Stage, reference_prim: Usd.Prim
) -> PayloadProperties:
    """Combined mass properties of every rigid body in the stage, in the
    frame of reference_prim."""
    rigid_bodies = find_rigid_bodies(stage)
    if not rigid_bodies:
        raise PayloadCalculationError("The stage has no prim with a Rigid Body API.")
    world_to_reference = rigid_world_transform(reference_prim).GetInverse()

    bodies: list[BodyMass] = []
    for prim in rigid_bodies:
        physx_body = await query_rigid_body_mass(prim)
        body_to_reference = rigid_world_transform(prim) * world_to_reference
        bodies.append(body_in_frame(physx_body, body_to_reference))
    mass, center, inertia = combine_bodies(bodies)

    kilograms_per_unit = UsdPhysics.GetStageKilogramsPerUnit(stage)
    meters_per_unit = SceneUtils.get_stage_units(stage)
    return PayloadProperties(
        mass=mass * kilograms_per_unit,
        center_of_mass=tuple(
            SceneUtils.value_to_millimeters(float(value), stage) for value in center
        ),
        inertia_tensor=inertia * kilograms_per_unit * meters_per_unit**2,
        body_paths=tuple(body.prim_path for body in bodies),
        extent_millimeters=tuple(
            SceneUtils.value_to_millimeters(float(value), stage)
            for value in rigid_bodies_extent(rigid_bodies)
        ),
    )
