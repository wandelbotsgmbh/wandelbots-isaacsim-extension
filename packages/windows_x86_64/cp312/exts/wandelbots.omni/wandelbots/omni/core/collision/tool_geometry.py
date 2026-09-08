"""Collision geometry of a tool USD file, read offline.

The reachability Attach Tool flow has no robot prim to reference the tool
into, so the file is opened as its own stage and only read. There is no
physics scene and no PhysX cooking, which is what authored_geometry already
lowers colliders for. Everything here is expressed relative to the tool root
(the prim that mounts at the flange), in meters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import carb
from pxr import Gf, Tf, Usd, UsdGeom, UsdPhysics

from wandelbots.omni.core.collision.authored_geometry import (
    collider_points_local,
    collision_enabled,
    convex_hull_points_and_faces,
    expand_collider_prims,
    mesh_points_local,
)
from wandelbots.omni.usd import TcpUtils
from wandelbots.omni.utils.collider_shells import aabb_corners
from wandelbots.omni.utils.math import m_to_mm, quat_to_rotvec

Point = tuple[float, float, float]

# Reading and hulling authored geometry fails on malformed assets: qhull
# bails on degenerate input and USD raises on a broken transform stack.
_GEOMETRY_ERRORS = (ValueError, IndexError, Tf.ErrorException)

# The 12 triangles of a box, indexing the 8 corners aabb_corners returns.
_BOX_FACES = (
    (0, 1, 3),
    (0, 3, 2),
    (4, 6, 7),
    (4, 7, 5),
    (0, 4, 5),
    (0, 5, 1),
    (2, 3, 7),
    (2, 7, 6),
    (0, 2, 6),
    (0, 6, 4),
    (1, 5, 7),
    (1, 7, 3),
)


@dataclass
class ToolGeometry:
    """A tool's collision geometry relative to the tool root, in meters."""

    # Flat triangle vertex list for the preview.
    preview_triangles: list[Point] = field(default_factory=list)
    # One vertex set per collider for the collision check.
    collider_hulls: list[list[Point]] = field(default_factory=list)
    # Which path produced the geometry, shown when nothing usable came out.
    source_note: str = ""


def find_tool_root(tool_stage: Usd.Stage) -> Usd.Prim | None:
    """Pick a reference prim to treat as the tool's own origin."""
    default_prim = tool_stage.GetDefaultPrim()
    if default_prim and default_prim.IsValid():
        return default_prim
    for child in tool_stage.GetPseudoRoot().GetChildren():
        if child.IsA(UsdGeom.Xformable):
            return child
    return None


def resolve_meters_per_unit(tool_stage: Usd.Stage) -> float:
    """Meters per unit of the tool file, defaulting to meters.

    UsdGeom.GetStageMetersPerUnit falls back to the schema default of
    centimeters when a stage authors nothing, which is essentially never what
    a hand-authored gripper asset means, so only an authored value is honored.
    """
    if UsdGeom.StageHasAuthoredMetersPerUnit(tool_stage):
        return UsdGeom.GetStageMetersPerUnit(tool_stage)
    return 1.0


def _relative_to_root(
    prim: Usd.Prim, tool_root: Usd.Prim, time: Usd.TimeCode
) -> Gf.Matrix4d:
    """`prim`'s transform relative to `tool_root`, computed from their
    local-to-stage-root transforms so it also works on an offline stage."""
    prim_to_stage = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(time)
    root_to_stage = UsdGeom.Xformable(tool_root).ComputeLocalToWorldTransform(time)
    return prim_to_stage * root_to_stage.GetInverse()


def find_tcp_candidates(tool_root: Usd.Prim) -> list[Usd.Prim]:
    return [
        prim
        for prim in Usd.PrimRange(tool_root)
        if prim != tool_root and TcpUtils.is_tcp(prim)
    ]


def tcp_offset_millimeters(
    tcp_prim: Usd.Prim, tool_root: Usd.Prim, meters_per_unit: float
) -> list[float]:
    """[x, y, z, rx, ry, rz] in millimeters plus rotation vector, relative to
    the tool root - the convention of the manual TCP offset fields."""
    time = Usd.TimeCode.Default()
    relative = _relative_to_root(tcp_prim, tool_root, time)
    if not relative.Orthonormalize():
        carb.log_warn(
            f"TCP transform for '{tcp_prim.GetPath()}' is not orthonormal; "
            "offset may be inaccurate."
        )
    translation = relative.ExtractTranslation()
    quaternion = relative.ExtractRotation().GetQuaternion()
    real, (x, y, z) = quaternion.GetReal(), quaternion.GetImaginary()
    position = [m_to_mm(value * meters_per_unit) for value in translation]
    return position + quat_to_rotvec(x, y, z, real)


def _hull_points_and_triangles(
    points_local: list[Point], relative: Gf.Matrix4d, meters_per_unit: float
) -> tuple[list[Point], list[Point]]:
    """Convex hull of prim-local points, in the tool-root frame and in meters.

    Returns the hull vertices for the collision check and their flat triangle
    expansion for the preview; both are empty when the points cannot span a
    hull.
    """
    points = [tuple(relative.Transform(Gf.Vec3d(*point))) for point in points_local]
    vertices, triangles = convex_hull_points_and_faces(points)
    hull_points = [_to_meters(vertex, meters_per_unit) for vertex in vertices]
    triangle_vertices = [
        hull_points[index] for triangle in triangles for index in triangle
    ]
    return hull_points, triangle_vertices


def bounding_box_triangles(points: list[Point]) -> list[Point]:
    """The 12 triangles of the axis-aligned box enclosing `points`."""
    return _triangles_from_corners(aabb_corners(points))


def extract_tool_geometry(tool_root: Usd.Prim, meters_per_unit: float) -> ToolGeometry:
    """Build the tool's collision geometry relative to `tool_root`.

    Authored colliders win and are converted as authored, each kept separate
    so a multi-part decomposition (one collider per gripper finger) survives.
    An asset carrying none falls back to a single hull over all its visual
    meshes, and to their bounding box when even that fails.
    """
    time = Usd.TimeCode.Default()
    collider_prims = [
        prim
        for prim in Usd.PrimRange(tool_root)
        if prim.HasAPI(UsdPhysics.CollisionAPI) and collision_enabled(prim)
    ]
    if collider_prims:
        return _authored_collider_geometry(
            tool_root, collider_prims, meters_per_unit, time
        )
    return _visual_mesh_geometry(tool_root, meters_per_unit, time)


def _to_meters(point: Point, meters_per_unit: float) -> Point:
    return (
        point[0] * meters_per_unit,
        point[1] * meters_per_unit,
        point[2] * meters_per_unit,
    )


def _triangles_from_corners(corners: list[Point]) -> list[Point]:
    if not corners:
        return []
    return [corners[index] for face in _BOX_FACES for index in face]


def _authored_collider_geometry(
    tool_root: Usd.Prim,
    collider_prims: list[Usd.Prim],
    meters_per_unit: float,
    time: Usd.TimeCode,
) -> ToolGeometry:
    targets = expand_collider_prims(collider_prims, time)
    carb.log_info(
        f"Attached tool: converting {len(targets)} authored collider(s) "
        f"(from {len(collider_prims)} CollisionAPI prim(s))."
    )
    if not targets:
        carb.log_warn(
            "Attached tool: colliders are authored but none resolve to "
            "supported geometry prims."
        )
    geometry = ToolGeometry()
    for prim, approximation in targets:
        hull_points, triangles = _collider_hull(
            prim, tool_root, approximation, meters_per_unit, time
        )
        if not hull_points:
            continue
        geometry.collider_hulls.append(hull_points)
        geometry.preview_triangles.extend(triangles)
    if geometry.collider_hulls:
        geometry.source_note = (
            f"converted {len(geometry.collider_hulls)} authored collider(s)"
        )
        return geometry
    geometry.source_note = (
        f"{len(collider_prims)} collider prim(s) are authored in the saved "
        "asset (the attach flow reads the file, not the open stage) but none "
        "could be converted, see the console log"
    )
    return geometry


def _collider_hull(
    prim: Usd.Prim,
    tool_root: Usd.Prim,
    approximation: str | None,
    meters_per_unit: float,
    time: Usd.TimeCode,
) -> tuple[list[Point], list[Point]]:
    try:
        points_local = collider_points_local(prim, time, approximation)
        if points_local is None:
            carb.log_warn(
                f"Attached tool: collider '{prim.GetPath()}' has unsupported "
                f"type '{prim.GetTypeName()}'; skipping it."
            )
            return [], []
        return _hull_points_and_triangles(
            points_local, _relative_to_root(prim, tool_root, time), meters_per_unit
        )
    except _GEOMETRY_ERRORS as exc:
        carb.log_warn(f"Could not build collider for '{prim.GetPath()}': {exc}")
        return [], []


def _visual_mesh_geometry(
    tool_root: Usd.Prim, meters_per_unit: float, time: Usd.TimeCode
) -> ToolGeometry:
    mesh_prims = [prim for prim in Usd.PrimRange(tool_root) if prim.IsA(UsdGeom.Mesh)]
    carb.log_info(
        "Attached tool: no colliders authored; building one convex hull over "
        f"all {len(mesh_prims)} visual mesh(es) instead."
    )
    merged_points = _merged_mesh_points(mesh_prims, tool_root, time)
    try:
        hull_points, triangles = _hull_points_and_triangles(
            merged_points, Gf.Matrix4d(1.0), meters_per_unit
        )
    except _GEOMETRY_ERRORS as exc:
        carb.log_warn(f"Could not hull the tool's visual meshes: {exc}")
        hull_points, triangles = [], []
    if hull_points:
        return ToolGeometry(
            preview_triangles=triangles,
            collider_hulls=[hull_points],
            source_note=(f"one whole-tool hull over {len(mesh_prims)} visual mesh(es)"),
        )
    return _bounding_box_geometry(merged_points, len(mesh_prims), meters_per_unit)


def _merged_mesh_points(
    mesh_prims: list[Usd.Prim], tool_root: Usd.Prim, time: Usd.TimeCode
) -> list[Point]:
    """Every visual mesh point of the tool, in the tool-root frame."""
    merged: list[Point] = []
    for prim in mesh_prims:
        try:
            relative = _relative_to_root(prim, tool_root, time)
            merged.extend(
                tuple(relative.Transform(Gf.Vec3d(*point)))
                for point in mesh_points_local(prim, time)
            )
        except _GEOMETRY_ERRORS as exc:
            carb.log_warn(f"Could not read mesh points from '{prim.GetPath()}': {exc}")
    return merged


def _bounding_box_geometry(
    points_local: list[Point], mesh_count: int, meters_per_unit: float
) -> ToolGeometry:
    if len(points_local) < 2:
        return ToolGeometry(
            source_note=(
                f"no colliders authored and the {mesh_count} visual mesh(es) "
                "yielded no usable points"
            )
        )
    # A hull fails on degenerate (for example flat) input, and a tool without
    # any collider would be checked as if it were not there at all.
    carb.log_warn(
        "Attached tool: convex hull over the visual meshes failed; using "
        "their bounding box instead."
    )
    points = [_to_meters(point, meters_per_unit) for point in points_local]
    corners = aabb_corners(points)
    return ToolGeometry(
        preview_triangles=_triangles_from_corners(corners),
        collider_hulls=[corners],
        source_note=(
            f"whole-tool bounding box over {mesh_count} visual mesh(es) "
            "(convex hull failed, see the console log)"
        ),
    )
