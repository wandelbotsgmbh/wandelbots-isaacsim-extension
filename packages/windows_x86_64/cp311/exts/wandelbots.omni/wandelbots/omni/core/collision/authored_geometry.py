"""Prim-local point sets for colliders authored in USD.

Shared by the collision setup export and the reachability Attach Tool flow so
both lower an authored collider the same way. Only reads USD, so it also works
on an offline stage that was opened without a physics scene.
"""

from __future__ import annotations

import math

from pxr import Usd, UsdGeom, UsdPhysics

from wandelbots.omni.utils.collider_shells import (
    aabb_corners,
    capsule_shell,
    cone_shell,
    cube_corners,
    cylinder_shell,
    sphere_shell,
)
from wandelbots.omni.utils.mesh import MeshUtils

_Point = tuple[float, float, float]

# Geometry prim types a collider expansion must keep. All but Plane are
# convertible by collider_points_local; a Plane has no convex point set and
# is handled (or rejected) by the consumer: the export builds a parametric
# NOVA plane from it, the tool flow skips it with a warning.
_SUPPORTED_COLLIDER_TYPES = (
    "Cube",
    "Sphere",
    "Capsule",
    "Cylinder",
    "Cone",
    "Plane",
    "Mesh",
)

_APPROXIMATION_ATTRIBUTE = "physics:approximation"


def collision_enabled(prim: Usd.Prim) -> bool:
    enabled = UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()
    return enabled is None or bool(enabled)


def authored_approximation(prim: Usd.Prim, time: Usd.TimeCode) -> str | None:
    if not prim.HasAttribute(_APPROXIMATION_ATTRIBUTE):
        return None
    return prim.GetAttribute(_APPROXIMATION_ATTRIBUTE).Get(time)


def expand_collider_prims(
    collider_roots: list[Usd.Prim], time: Usd.TimeCode
) -> list[tuple[Usd.Prim, str | None]]:
    """Resolve CollisionAPI prims to the geometry prims to convert, each paired
    with its effective approximation.

    CollisionAPI on an Xform or Scope makes every geometry prim below it a
    collider, so such a root is replaced by its descendants. They inherit the
    root's approximation unless they author their own. A descendant with its
    own CollisionAPI is skipped: it is either a root itself or deliberately
    disabled.
    """
    expanded: dict[str, tuple[Usd.Prim, str | None]] = {}
    for root in collider_roots:
        if root.GetTypeName() in _SUPPORTED_COLLIDER_TYPES:
            expanded[root.GetPath().pathString] = (
                root,
                authored_approximation(root, time),
            )
    for root in collider_roots:
        if root.GetTypeName() in _SUPPORTED_COLLIDER_TYPES:
            continue
        root_approximation = authored_approximation(root, time)
        for descendant in Usd.PrimRange(root):
            if descendant == root:
                continue
            if descendant.GetTypeName() not in _SUPPORTED_COLLIDER_TYPES:
                continue
            if descendant.HasAPI(UsdPhysics.CollisionAPI):
                continue
            path = descendant.GetPath().pathString
            if path in expanded:
                continue
            expanded[path] = (
                descendant,
                authored_approximation(descendant, time) or root_approximation,
            )
    return list(expanded.values())


def mesh_points_local(prim: Usd.Prim, time: Usd.TimeCode) -> list[_Point]:
    points = UsdGeom.Mesh(prim).GetPointsAttr().Get(time)
    return [tuple(point) for point in (points or [])]


def _bounding_sphere_shell(points: list[_Point]) -> list[_Point]:
    """Point shell of the sphere around the points' bounding box. Wider than
    the tightest enclosing sphere, which keeps it conservative."""
    corners = aabb_corners(points)
    if not corners:
        return []
    center = tuple((corners[0][axis] + corners[-1][axis]) / 2.0 for axis in range(3))
    return sphere_shell(math.dist(corners[0], corners[-1]) / 2.0, center)


def collider_points_local(
    prim: Usd.Prim, time: Usd.TimeCode, approximation: str | None = None
) -> list[_Point] | None:
    """Prim-local points spanning an authored collider, or None for a type
    that has no convex point set (a Plane, for example).

    Shape prims are built from their attributes. Meshes honor the
    approximations that can be computed without physics: boundingCube is the
    local bounding box, boundingSphere the sphere around it. The convex family
    needs PhysX cooking, so the mesh points are returned instead; their hull
    equals the cooked result for convexHull and contains it for the others.
    """
    type_name = prim.GetTypeName()
    if type_name == "Cube":
        return cube_corners(UsdGeom.Cube(prim).GetSizeAttr().Get(time))
    if type_name == "Sphere":
        return sphere_shell(UsdGeom.Sphere(prim).GetRadiusAttr().Get(time))
    if type_name == "Capsule":
        capsule = UsdGeom.Capsule(prim)
        return capsule_shell(
            capsule.GetRadiusAttr().Get(time),
            capsule.GetHeightAttr().Get(time),
            capsule.GetAxisAttr().Get(time) or "Z",
        )
    if type_name == "Cylinder":
        cylinder = UsdGeom.Cylinder(prim)
        return cylinder_shell(
            cylinder.GetRadiusAttr().Get(time),
            cylinder.GetHeightAttr().Get(time),
            cylinder.GetAxisAttr().Get(time) or "Z",
        )
    if type_name == "Cone":
        cone = UsdGeom.Cone(prim)
        return cone_shell(
            cone.GetRadiusAttr().Get(time),
            cone.GetHeightAttr().Get(time),
            cone.GetAxisAttr().Get(time) or "Z",
        )
    if prim.IsA(UsdGeom.Mesh):
        points = mesh_points_local(prim, time)
        if approximation == "boundingCube":
            return aabb_corners(points)
        if approximation == "boundingSphere":
            return _bounding_sphere_shell(points)
        return points
    return None


def convex_hull_points_and_faces(
    points: list[_Point],
) -> tuple[list[_Point], list[tuple[int, int, int]]]:
    """Convex hull of the points, reduced to the vertices its faces use.

    triangulate_convex_hull indexes into the point list it was given, so a
    dense mesh would otherwise ship its whole point cloud as hull vertices.
    Degenerate input makes qhull return no faces, which yields empty lists.
    """
    if len(points) < 4:
        return [], []
    hull_points, faces, _ = MeshUtils.triangulate_convex_hull(
        [list(point) for point in points]
    )
    used_indices = sorted({index for face in faces for index in face})
    if not used_indices:
        return [], []
    reindexed = {old: new for new, old in enumerate(used_indices)}
    vertices = [tuple(hull_points[index]) for index in used_indices]
    triangles = [tuple(reindexed[index] for index in face) for face in faces]
    return vertices, triangles
