"""Point-shell generators for convex collider construction.

NOVA's collision world is convex-only, so every collider kind is lowered to a
convex hull over a point set. These helpers build the prim-local points that
span an authored shape: cube and bounding box exactly, the round shapes as
tessellated shells whose radii are inflated until the hull circumscribes the
true surface, so a collider never under-reports the geometry it stands for.

Pure math on purpose - no pxr, carb or omni imports - so the offline Attach
Tool flow, the collision setup export and plain-python tests can all use it.
"""

from __future__ import annotations

import math

_Vec3 = tuple[float, float, float]

# 16 segments per ring and 30 deg latitude steps keep the circumscription
# inflation below ~6% (1/(cos(11.25 deg) * cos(15 deg)) for doubly-curved
# surfaces, 1/cos(11.25 deg) ~ 2% for rings only).
RING_SEGMENTS = 16
_SPHERE_LATITUDES_DEG = (-60.0, -30.0, 0.0, 30.0, 60.0)
ROUND_SHAPE_INFLATION = 1.0 / (
    math.cos(math.pi / RING_SEGMENTS) * math.cos(math.radians(15.0))
)


def _oriented(ring_x: float, ring_y: float, axis_offset: float, axis: str) -> _Vec3:
    """Map ring-plane coordinates and the offset along `axis` into xyz."""
    if axis == "X":
        return (axis_offset, ring_x, ring_y)
    if axis == "Y":
        return (ring_y, axis_offset, ring_x)
    return (ring_x, ring_y, axis_offset)


def _ring_points(radius: float, offset: float, axis: str = "Z") -> list[_Vec3]:
    """RING_SEGMENTS points on a circle of `radius` perpendicular to `axis`, at
    `offset` along it. The radius is used verbatim - callers inflate it
    themselves when the ring polygon has to circumscribe a circle."""
    points = []
    for segment in range(RING_SEGMENTS):
        angle = 2.0 * math.pi * segment / RING_SEGMENTS
        points.append(
            _oriented(radius * math.cos(angle), radius * math.sin(angle), offset, axis)
        )
    return points


def sphere_shell(radius: float, center: _Vec3 = (0.0, 0.0, 0.0)) -> list[_Vec3]:
    """Latitude rings plus poles, circumscribing the sphere."""
    inflated_radius = radius * ROUND_SHAPE_INFLATION
    center_x, center_y, center_z = center
    points = [
        (center_x, center_y, center_z + inflated_radius),
        (center_x, center_y, center_z - inflated_radius),
    ]
    for latitude_deg in _SPHERE_LATITUDES_DEG:
        latitude = math.radians(latitude_deg)
        for ring_x, ring_y, height in _ring_points(
            inflated_radius * math.cos(latitude),
            center_z + inflated_radius * math.sin(latitude),
            "Z",
        ):
            points.append((center_x + ring_x, center_y + ring_y, height))
    return points


def capsule_shell(radius: float, height: float, axis: str = "Z") -> list[_Vec3]:
    """Cylinder-section rings at +/-height/2 plus hemispherical cap rings and
    tip points, circumscribing the capsule."""
    inflated_radius = radius * ROUND_SHAPE_INFLATION
    half_height = height / 2.0
    points = [
        _oriented(0.0, 0.0, half_height + inflated_radius, axis),
        _oriented(0.0, 0.0, -(half_height + inflated_radius), axis),
    ]
    for sign in (1.0, -1.0):
        for latitude_deg in (0.0, 30.0, 60.0):
            latitude = math.radians(latitude_deg)
            points.extend(
                _ring_points(
                    inflated_radius * math.cos(latitude),
                    sign * (half_height + inflated_radius * math.sin(latitude)),
                    axis,
                )
            )
    return points


def cylinder_shell(radius: float, height: float, axis: str = "Z") -> list[_Vec3]:
    """A prism circumscribing the cylinder: only the ring polygon needs
    inflating, the flat caps are exact."""
    ring_radius = radius / math.cos(math.pi / RING_SEGMENTS)
    half_height = height / 2.0
    return _ring_points(ring_radius, half_height, axis) + _ring_points(
        ring_radius, -half_height, axis
    )


def cone_shell(radius: float, height: float, axis: str = "Z") -> list[_Vec3]:
    """Base ring at -height/2 plus the apex at +height/2 (UsdGeom.Cone
    convention), the base polygon circumscribing the base circle."""
    ring_radius = radius / math.cos(math.pi / RING_SEGMENTS)
    half_height = height / 2.0
    return [_oriented(0.0, 0.0, half_height, axis)] + _ring_points(
        ring_radius, -half_height, axis
    )


def cube_corners(size: float) -> list[_Vec3]:
    """The 8 corners of a UsdGeom.Cube of edge length `size` (exact)."""
    half_size = size / 2.0
    return [
        (sign_x * half_size, sign_y * half_size, sign_z * half_size)
        for sign_x in (-1.0, 1.0)
        for sign_y in (-1.0, 1.0)
        for sign_z in (-1.0, 1.0)
    ]


def aabb_corners(points: list[_Vec3]) -> list[_Vec3]:
    """The 8 corners of the axis-aligned bounding box of `points` (exact);
    empty input yields an empty list. The first corner is the minimum, the
    last the maximum."""
    if not points:
        return []
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    z_values = [point[2] for point in points]
    return [
        (x, y, z)
        for x in (min(x_values), max(x_values))
        for y in (min(y_values), max(y_values))
        for z in (min(z_values), max(z_values))
    ]
