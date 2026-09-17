import weakref

import carb
import omni.timeline
import omni.usd
from isaacsim.core.prims import SingleArticulation
from pxr import Sdf, Tf, Usd, UsdPhysics


def _on_stage_objects_changed(cache: "ArticulationCache"):
    """Notice callback holding *cache* weakly, resolved once per notice."""
    weak_cache = weakref.ref(cache)

    def _callback(notice, sender):
        instance = weak_cache()
        if instance is not None:
            instance._on_objects_changed(notice)

    return _callback


def _paths_overlap(resynced: Sdf.Path, cached: Sdf.Path) -> bool:
    """Whether a structural edit at one path can change what the other resolved to.

    get_root_articulation_path walks up through ancestors and
    find_physx_articulation_path descends into children, so an edit anywhere
    on that line changes the answer.
    """
    return resynced.HasPrefix(cached) or cached.HasPrefix(resynced)


# Relationship an asset authors on a motion group prim to name the articulation
# its links belong to. See _authored_articulation_root.
ARTICULATION_ROOT_RELATIONSHIP = "wandelbots:nova:motionGroup:articulationRoot"


def _authored_articulation_root(prim: Usd.Prim) -> str | None:
    """The articulation root the asset names for this motion group, or None.

    The traversal below finds the root by hopping out of the motion group along
    its root_joint, which only works while the root sits on the far side of
    that joint. An asset may instead fold several motion groups into one
    articulation whose root is in a different branch entirely - a lift unit at
    /World/liftunit carrying the arms that live under /World/RH5v2 - and no
    traversal from the motion group can reach that. The asset knows it at build
    time, so it writes it down and this reads it.
    """
    relationship = prim.GetRelationship(ARTICULATION_ROOT_RELATIONSHIP)
    if not relationship:
        return None
    targets = relationship.GetTargets()
    if not targets:
        return None
    root_prim = prim.GetStage().GetPrimAtPath(targets[0])
    if not root_prim or not root_prim.IsValid():
        carb.log_warn(
            f"{prim.GetPath()} names {targets[0]} as its articulation root, but "
            f"there is no prim there. Falling back to the joint traversal."
        )
        return None
    return targets[0].pathString


def get_root_articulation_path(prim: Usd.Prim) -> str:
    authored_root = _authored_articulation_root(prim)
    if authored_root is not None:
        return authored_root

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
    """Articulation lookups cached until the stage moves underneath them.

    Timeline STOP resets the scene. A prim resync can move what the lookup
    resolved just as well: a root_joint added or removed, ArticulationRootAPI
    applied to an ancestor, a rigid body added under the root. Property
    resyncs are ignored, so a viewport transform drag never drops a handle
    mid-stream.
    """

    def __init__(self):
        self._cache: dict[str, ArticulationCacheHandle] = {}
        # motion-group prim path -> the PhysX anchor path its links belong to.
        # Only the resolution is remembered, never the handle: an asset may put
        # the articulation in a branch the motion group is not under, and a
        # resync there evicts _cache without touching this map. Handing out a
        # remembered handle would then keep the dropped SingleArticulation
        # alive; going through get_articulation rebuilds it instead.
        self._motion_group_anchors: dict[str, str] = {}
        self._timeline = omni.timeline.get_timeline_interface()
        self._timeline_sub = (
            self._timeline.get_timeline_event_stream().create_subscription_to_pop(
                lambda event, weak_self=weakref.ref(self): (
                    weak_self() and weak_self()._on_timeline_event(event)
                )
            )
        )
        self._stage_listener = None
        # Tf holds the notice callback weakly on some Kit versions, so the
        # closure has to be owned here or the listener silently stops firing.
        self._stage_listener_callback = None
        # Stage identity by stage id. A root layer would be a weak handle that
        # expires with the stage, and reopening the same file yields the same
        # layer, so it cannot tell two stages apart.
        self._tracked_stage_id: int | None = None

    def _ensure_stage_listener(self) -> None:
        """Track the context stage, the one the cached paths are resolved on."""
        context = omni.usd.get_context()
        stage = context.get_stage()
        if stage is None:
            return
        stage_id = context.get_stage_id()
        if self._stage_listener is not None and stage_id == self._tracked_stage_id:
            return
        self.release()
        self._stage_listener_callback = _on_stage_objects_changed(self)
        self._stage_listener = Tf.Notice.Register(
            Usd.Notice.ObjectsChanged, self._stage_listener_callback, stage
        )
        self._tracked_stage_id = stage_id

    def release(self) -> None:
        """Drop the cache and revoke the USD listener.

        The cache is module state, so without this the listener outlives an
        extension reload and keeps firing into the stale module. Idempotent.
        """
        self._invalidate()
        if self._stage_listener is not None:
            self._stage_listener.Revoke()
            self._stage_listener = None
        self._stage_listener_callback = None
        self._tracked_stage_id = None

    def _on_objects_changed(self, notice) -> None:
        for path in notice.GetResyncedPaths():
            if not path.IsAbsoluteRootOrPrimPath():
                continue
            if path == Sdf.Path.absoluteRootPath:
                self._invalidate()
                return
            self._drop_handles_touching(path)

    def _drop_handles_touching(self, resynced_path: Sdf.Path) -> None:
        stale_articulations = [
            path
            for path in self._cache
            if _paths_overlap(resynced_path, Sdf.Path(path))
        ]
        for path in stale_articulations:
            del self._cache[path]

        # A resolution is stale when either end moved: the motion group itself
        # (its root_joint, the relationship naming the root) or the articulation
        # it resolved to, which can sit in an entirely different branch.
        stale_resolutions = [
            path
            for path, anchor_path in self._motion_group_anchors.items()
            if _paths_overlap(resynced_path, Sdf.Path(path))
            or _paths_overlap(resynced_path, Sdf.Path(anchor_path))
        ]
        for path in stale_resolutions:
            del self._motion_group_anchors[path]

    def get_articulation(self, articulation_root_path: str) -> ArticulationCacheHandle:
        self._ensure_stage_listener()
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
        self._ensure_stage_listener()
        prim_path = motion_group_prim.GetPath().pathString
        anchor_path = self._motion_group_anchors.get(prim_path)
        if anchor_path is None:
            root_path = get_root_articulation_path(motion_group_prim)
            root_prim = motion_group_prim.GetStage().GetPrimAtPath(root_path)
            anchor_path = find_physx_articulation_path(root_prim)
            self._motion_group_anchors[prim_path] = anchor_path
        return self.get_articulation(anchor_path)

    def _invalidate(self):
        self._cache.clear()
        self._motion_group_anchors.clear()

    def _on_timeline_event(self, event):
        if event.type == omni.timeline.TimelineEventType.STOP.value:
            self._invalidate()


cache = ArticulationCache()


def get_articulation_cache() -> ArticulationCache:
    return cache


def release_articulation_cache() -> None:
    """Drop the articulation cache and its USD listener. Called on shutdown."""
    cache.release()
