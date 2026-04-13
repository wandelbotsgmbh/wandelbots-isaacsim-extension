import weakref
from isaacsim.core.prims import SingleArticulation
import omni.timeline


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
    def __init__(self):
        self._cache: dict[str, ArticulationCacheHandle] = {}
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

    def _invalidate(self):
        self._cache.clear()

    def _on_timeline_event(self, event):
        if event.type == omni.timeline.TimelineEventType.STOP.value:
            self._invalidate()


cache = ArticulationCache()


def get_articulation_cache() -> ArticulationCache:
    return cache
