import carb.events
from typing import Any, Callable, cast
from wandelbots.omni.utils.teaching import GhostObjectUtils, GhostObject
import omni.usd
import weakref


class GhostObjectsSubscription:
    def __init__(
        self,
        ghost_object_changed_fn: Callable[[], Any],
    ):
        self.ghost_object_changed_fn = ghost_object_changed_fn
        self._ghost_objects = self._load_ghost_objects()
        self._stage_event_subscription = (
            cast(
                omni.usd.UsdContext,
                omni.usd.get_context(),
            )
            .get_stage_event_stream()
            .create_subscription_to_pop(
                lambda event, weak_self=weakref.proxy(self): weak_self._on_stage_event(
                    event
                ),
                name="GhostObjectsSubscription_stage_event",
            )
        )

    def _load_ghost_objects(self) -> list[str]:
        return set(
            [
                ghost_object.prim_path
                for ghost_object in GhostObjectUtils.get_ghost_objects()
            ]
        )

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type != int(omni.usd.StageEventType.HIERARCHY_CHANGED):
            return
        updated_ghost_objects: list[GhostObject] = self._load_ghost_objects()
        if set(self._ghost_objects) != updated_ghost_objects:
            self._ghost_objects = updated_ghost_objects
            self.ghost_object_changed_fn()
