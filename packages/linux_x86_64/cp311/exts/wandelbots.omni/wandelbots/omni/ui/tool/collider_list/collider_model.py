"""ColliderModel — AbstractItemModel backed by stage CollisionAPI prims."""

from __future__ import annotations

import carb
import omni.physx
import omni.ui as ui
import omni.usd
from pxr import PhysicsSchemaTools, Usd, UsdPhysics, UsdUtils

from wandelbots.omni.core.collision.shapes import get_convex_hull_vertex_count
from wandelbots.omni.ui.tool.collider_list.collider_item import ColliderItem


class ColliderModel(ui.AbstractItemModel):
    """Flat list of all prims on the stage with UsdPhysics.CollisionAPI."""

    def __init__(self):
        super().__init__()
        self._items: list[ColliderItem] = []
        self._filtered: list[ColliderItem] = []
        self._search: str = ""
        self._type_filter: str = ""  # "" = all types
        self._physx_cooking = None  # acquired lazily for convex-hull vertex counts

    def get_item_children(self, item=None):
        if item is None:
            return self._filtered
        return []

    def get_item_value_model_count(self, item=None) -> int:
        return 5

    def get_item_value_model(self, item=None, column_id: int = 0):
        if item is None:
            return ui.SimpleStringModel("")
        if column_id == 0:
            return ui.SimpleStringModel(item.prim_path)
        elif column_id == 1:
            return ui.SimpleStringModel(item.collider_type)
        elif column_id == 2:
            return ui.SimpleStringModel(item.info)
        return ui.SimpleStringModel("")

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    def refresh(self):
        """Scan the stage for all prims with CollisionAPI."""
        self._items.clear()
        stage = omni.usd.get_context().get_stage()
        if not stage:
            self._item_changed(None)
            return

        for prim in stage.Traverse():
            if not prim.HasAPI(UsdPhysics.CollisionAPI):
                continue
            collision_api = UsdPhysics.CollisionAPI.Get(stage, prim.GetPath())
            enabled = collision_api.GetCollisionEnabledAttr().Get()

            prim_path = prim.GetPath().pathString
            collider_type = self._get_collider_type(prim)
            info = self._get_collider_info(prim, collider_type)
            self._items.append(ColliderItem(prim_path, collider_type, info, enabled))

        self._apply_filter()

    def remove_item(self, item: ColliderItem):
        """Remove CollisionAPI from the prim and remove from list."""
        stage = omni.usd.get_context().get_stage()
        if stage:
            prim = stage.GetPrimAtPath(item.prim_path)
            if prim and prim.IsValid():
                prim.RemoveAPI(UsdPhysics.CollisionAPI)
                if prim.HasAPI(UsdPhysics.MeshCollisionAPI):
                    prim.RemoveAPI(UsdPhysics.MeshCollisionAPI)
        if item in self._items:
            self._items.remove(item)
        self._apply_filter()

    # ------------------------------------------------------------------
    # Filtering (search bar + type filter)
    # ------------------------------------------------------------------

    def set_search(self, text: str | None) -> None:
        self._search = (text or "").strip().lower()
        self._apply_filter()

    def set_type_filter(self, collider_type: str | None) -> None:
        # "" / "All types" mean no type restriction.
        self._type_filter = (
            "" if not collider_type or collider_type == "All types" else collider_type
        )
        self._apply_filter()

    def update_filter(self) -> None:
        """Recompute the filtered view (e.g. after bulk type changes)."""
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = self._search
        tf = self._type_filter
        self._filtered = [
            it
            for it in self._items
            if (not q or q in it.prim_path.lower() or q in it.collider_type.lower())
            and (not tf or it.collider_type == tf)
        ]
        self._item_changed(None)

    def toggle_item_enabled(self, item: ColliderItem):
        """Toggle the collision enabled state on the prim."""
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        prim = stage.GetPrimAtPath(item.prim_path)
        if not prim or not prim.IsValid():
            return
        collision_api = UsdPhysics.CollisionAPI.Get(stage, prim.GetPath())
        new_state = not item.enabled
        collision_api.GetCollisionEnabledAttr().Set(new_state)
        item.enabled = new_state
        self._item_changed(None)

    def set_item_enabled(self, item: ColliderItem, enabled: bool):
        """Set the collision enabled state on the prim to a specific value."""
        if item.enabled == enabled:
            return
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        prim = stage.GetPrimAtPath(item.prim_path)
        if not prim or not prim.IsValid():
            return
        collision_api = UsdPhysics.CollisionAPI.Get(stage, prim.GetPath())
        collision_api.GetCollisionEnabledAttr().Set(enabled)
        item.enabled = enabled

    def change_collider_type(self, item: ColliderItem, new_type: str):
        """Change the physics:approximation on a mesh prim."""
        if item.is_native_shape:
            return
        stage = omni.usd.get_context().get_stage()
        if not stage:
            return
        prim = stage.GetPrimAtPath(item.prim_path)
        if not prim or not prim.IsValid():
            return
        if not prim.HasAPI(UsdPhysics.MeshCollisionAPI):
            UsdPhysics.MeshCollisionAPI.Apply(prim)
        mesh_collision_api = UsdPhysics.MeshCollisionAPI.Get(stage, prim.GetPath())
        mesh_collision_api.GetApproximationAttr().Set(new_type)
        item.collider_type = new_type
        # The info column (vertex count) depends on the approximation, so recompute
        # it now — otherwise it shows the stale count of the previous type.
        item.info = self._get_collider_info(prim, new_type)
        self._item_changed(None)

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------

    def sort_by_name(self, ascending: bool = True):
        """Sort items alphabetically by prim name."""
        self._items.sort(
            key=lambda item: (item.prim_path.rsplit("/", 1)[-1]).lower(),
            reverse=not ascending,
        )
        self._apply_filter()

    def sort_by_type(self, ascending: bool = True):
        """Sort items by collider type."""
        self._items.sort(
            key=lambda item: item.collider_type.lower(),
            reverse=not ascending,
        )
        self._apply_filter()

    def sort_by_vertices(self, ascending: bool = True):
        """Sort items by vertex info (numeric extraction)."""

        def _vert_key(item):
            parts = item.info.split()
            if parts and parts[0].isdigit():
                return int(parts[0])
            return 0

        self._items.sort(key=_vert_key, reverse=not ascending)
        self._apply_filter()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def items(self) -> list[ColliderItem]:
        """All colliders on the stage (unfiltered)."""
        return self._items

    @property
    def displayed_items(self) -> list[ColliderItem]:
        """Colliders currently shown in the tree (after the type filter)."""
        return self._filtered

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_collider_type(prim: Usd.Prim) -> str:
        prim_type = prim.GetTypeName()
        if prim_type in ("Sphere", "Cube", "Cylinder", "Capsule", "Cone", "Plane"):
            return prim_type.lower()

        approx_attr = prim.GetAttribute("physics:approximation")
        if approx_attr and approx_attr.HasValue():
            approx = approx_attr.Get(Usd.TimeCode.Default())
            if approx:
                return approx
        return "mesh"

    def _get_collider_info(self, prim: Usd.Prim, collider_type: str) -> str:
        """Vertices column text — the cooked collider vertex count.

        Only mesh approximations that produce a hull (convexHull /
        convexDecomposition) have a meaningful collider vertex count. Primitive
        shapes and plain meshes report nothing here: only collider vertices are
        of interest, not the source mesh/object geometry.
        """
        try:
            if collider_type in ("convexHull", "convexDecomposition"):
                count = self._convex_vertex_count(prim)
                if count:
                    return f"{count} verts"
            return ""
        except Exception:
            return ""

    def _convex_vertex_count(self, prim: Usd.Prim) -> int:
        """Cooked convex-hull vertex count for a mesh prim.

        Uses PhysX so the number matches the actual collider geometry (and the
        exported hull). Returns 0 if cooking is unavailable or fails — we never
        fall back to the raw source-mesh point count, since that reflects the
        mesh/object, not the collider.
        """
        try:
            if self._physx_cooking is None:
                self._physx_cooking = omni.physx.get_physx_cooking_interface()
            stage = prim.GetStage()
            stage_id = UsdUtils.StageCache.Get().GetId(stage).ToLongInt()
            prim_id = PhysicsSchemaTools.sdfPathToInt(prim.GetPath())
            count = get_convex_hull_vertex_count(
                self._physx_cooking, stage_id, prim, prim_id
            )
            if count:
                return count
        except Exception as exc:
            carb.log_warn(f"Convex hull cook failed for {prim.GetPath()}: {exc}")
        return 0
