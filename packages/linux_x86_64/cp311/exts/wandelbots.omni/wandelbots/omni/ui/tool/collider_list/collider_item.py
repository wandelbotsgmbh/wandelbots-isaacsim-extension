"""ColliderItem data class and shared constants for the Collider List."""

from __future__ import annotations

import omni.ui as ui

ROW_HEIGHT = 28

# Mesh approximations the NOVA export converts faithfully, and the recommended
# choices offered by the Type dropdown. Anything else is exported as a
# conservative convex hull, so a highlighted row means exported lossily.
NOVA_MESH_COLLIDER_TYPES = [
    "convexHull",
    "convexDecomposition",
    "boundingSphere",
    "boundingCube",
]

# Mesh collision approximations selectable in the Isaac Sim physics collider
# settings. Mirrors omni.physx.scripts.utils.MESH_APPROXIMATIONS, where "none"
# is the exact triangle mesh.
ISAAC_MESH_APPROXIMATION_TYPES = [
    "none",
    "convexHull",
    "convexDecomposition",
    "meshSimplification",
    "convexMeshSimplification",
    "boundingCube",
    "boundingSphere",
    "sphereFill",
    "sdf",
]

# Native shape types (fixed, not changeable)
NATIVE_SHAPE_TYPES = {"sphere", "cube", "cylinder", "capsule", "cone", "plane"}

# Native shapes Wandelbots NOVA can export, see
# collision_export_service.CollisionExportService.get_prim_collider (a cone has
# no NOVA primitive and is exported as a circumscribed convex hull).
NOVA_NATIVE_SHAPE_TYPES = {"sphere", "cube", "cylinder", "capsule", "cone", "plane"}

# Every collider type compatible with Wandelbots NOVA export: the exportable
# native shapes plus the NOVA-compatible mesh approximations.
NOVA_COMPATIBLE_TYPES = NOVA_NATIVE_SHAPE_TYPES | set(NOVA_MESH_COLLIDER_TYPES)


class ColliderItem(ui.AbstractItem):
    """Represents a single prim with CollisionAPI."""

    def __init__(
        self, prim_path: str, collider_type: str, info: str, enabled: bool = True
    ):
        super().__init__()
        self.prim_path = prim_path
        self.collider_type = collider_type
        self.info = info
        self.enabled = enabled

    @property
    def is_native_shape(self) -> bool:
        return self.collider_type in NATIVE_SHAPE_TYPES

    @property
    def is_nova_compatible(self) -> bool:
        """True when this collider type can be exported to Wandelbots NOVA."""
        return self.collider_type in NOVA_COMPATIBLE_TYPES
