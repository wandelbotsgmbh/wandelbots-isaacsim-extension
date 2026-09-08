import weakref

import carb
import omni.timeline
from isaacsim.core.prims import SingleArticulation
from pxr import Usd, UsdPhysics


def get_root_articulation_path(prim: Usd.Prim) -> str:
    current_prim = prim
    visited_prims = set()

    while current_prim is not None:
        prim_path = current_prim.GetPath()

        if prim_path in visited_prims:
            carb.log_warn(
                f"Circular reference detected in motion group chain at {prim_path}. "
                f"Returning current prim as root."
            )
            return current_prim.GetPath().pathString
        visited_prims.add(prim_path)

        root_joint_prim = current_prim.GetChild("root_joint")
        if not root_joint_prim.IsValid():
            carb.log_warn(
                f"No valid root_joint found for prim {current_prim.GetPath()}. "
                f"Returning current prim as root."
            )
            return current_prim.GetPath().pathString

        joint = UsdPhysics.Joint(root_joint_prim)

        body0_rel = joint.GetBody0Rel()
        if not body0_rel:
            return current_prim.GetPath().pathString

        body0_targets = body0_rel.GetTargets()
        if not body0_targets:
            return current_prim.GetPath().pathString

        body0_prim = current_prim.GetStage().GetPrimAtPath(body0_targets[0])
        if not body0_prim.IsValid():
            return current_prim.GetPath().pathString

        parent_prim = body0_prim.GetParent()
        if not parent_prim or not UsdPhysics.ArticulationRootAPI(parent_prim):
            return current_prim.GetPath().pathString

        # Continue traversal with parent
        current_prim = parent_prim

    return current_prim.GetPath().pathString


def find_physx_articulation_path(prim: Usd.Prim) -> str:
    """Return the path PhysX actually registers the articulation under.

    ``ArticulationRootAPI`` is conventionally applied to a parent Xform that
    has no ``RigidBodyAPI`` itself.  PhysX registers (and pattern-matches)
    articulations against rigid-body prims, so passing the Xform path to
    ``SingleArticulation`` fails with "did not match any rigid bodies".
    This function descends breadth-first to the first ``RigidBodyAPI``
    descendant -- which is the prim PhysX actually uses as the anchor -- and
    returns its path.  If the prim itself already has ``RigidBodyAPI`` the
    path is returned unchanged.
    """
    if UsdPhysics.RigidBodyAPI(prim):
        return prim.GetPath().pathString

    queue = list(prim.GetChildren())
    while queue:
        child = queue.pop(0)
        if UsdPhysics.RigidBodyAPI(child):
            return child.GetPath().pathString
        queue.extend(child.GetChildren())

    carb.log_warn(
        f"No RigidBodyAPI descendant found under {prim.GetPath()}. "
        f"Using prim path directly."
    )
    return prim.GetPath().pathString


class ArticulationCacheHandle:
    def __init__(self, articulation_root_path: str):
        self.articulation_root_path = articulation_root_path
        self._cached_articulation: SingleArticulation | None = None

    @property
    def articulation(self) -> SingleArticulation:
        if self._cached_articulation is None:
            self._cached_articulation = SingleArticulation(self.articulation_root_path)
        return self._cached_articulation


class ArticulationCache:
    """Articulation lookups cached until timeline STOP (scene reset/reload)."""

    def __init__(self):
        self._cache: dict[str, ArticulationCacheHandle] = {}
        # motion-group prim path -> fully resolved articulation handle
        self._motion_group_handles: dict[str, ArticulationCacheHandle] = {}
        self._timeline = omni.timeline.get_timeline_interface()
        self._timeline_sub = (
            self._timeline.get_timeline_event_stream().create_subscription_to_pop(
                lambda event, weak_self=weakref.ref(self): (
                    weak_self() and weak_self()._on_timeline_event(event)
                )
            )
        )

    def get_articulation(self, articulation_root_path: str) -> ArticulationCacheHandle:
        if articulation_root_path in self._cache:
            return self._cache[articulation_root_path]
        else:
            articulation = ArticulationCacheHandle(articulation_root_path)
            self._cache[articulation_root_path] = articulation
            return articulation

    def get_articulation_for_motion_group(
        self, motion_group_prim: Usd.Prim
    ) -> ArticulationCacheHandle:
        """Resolve motion-group prim -> root articulation -> PhysX anchor,
        cached per prim path: get_motion_group_current_joint_positions runs
        this per pose update (e.g. while dragging a ghost object), and
        re-walking USD each call re-logged the same warning for every motion
        group without a root_joint.
        """
        prim_path = motion_group_prim.GetPath().pathString
        handle = self._motion_group_handles.get(prim_path)
        if handle is None:
            root_path = get_root_articulation_path(motion_group_prim)
            root_prim = motion_group_prim.GetStage().GetPrimAtPath(root_path)
            handle = self.get_articulation(find_physx_articulation_path(root_prim))
            self._motion_group_handles[prim_path] = handle
        return handle

    def _invalidate(self):
        self._cache.clear()
        self._motion_group_handles.clear()

    def _on_timeline_event(self, event):
        if event.type == omni.timeline.TimelineEventType.STOP.value:
            self._invalidate()


cache = ArticulationCache()


def get_articulation_cache() -> ArticulationCache:
    return cache
