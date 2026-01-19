from typing import cast
import weakref
import carb
import omni.ui as ui
from wandelbots.omni.utils.teaching import GhostObject
import omni.usd
from wandelbots.omni.utils.teaching import GhostObjectUtils
from pxr import Usd
import carb.events
from omni.kit.async_engine import run_coroutine


class GhostObjectItem(ui.AbstractItem):
    def __init__(self, ghost_object: GhostObject, display_name: str = None):
        super().__init__()
        self.ghost_object = ghost_object
        self.display_name = display_name
        if self.display_name is None:
            self.display_name = ghost_object.prim_path
        self.model = ui.SimpleStringModel(self.display_name)


class GhostObjectModel(ui.AbstractItemModel):
    def __init__(
        self,
        selected_ghost_object: GhostObject | None,
        ghost_objects: list[GhostObject],
    ):
        super().__init__()

        ghost_object_name_count = {}
        for ghost_object in ghost_objects:
            ghost_object_name_count[ghost_object.name] = (
                ghost_object_name_count.get(ghost_object.name, 0) + 1
            )

        def get_display_name(ghost_object: GhostObject):
            count = ghost_object_name_count[ghost_object.name]
            name = ghost_object.name
            if count > 1:
                return f"{name} ({ghost_object.prim_path})"
            return name

        self._items = [
            GhostObjectItem(ghost_object, display_name=get_display_name(ghost_object))
            for ghost_object in ghost_objects
        ]
        self._current_index = (
            ui.SimpleIntModel(ghost_objects.index(selected_ghost_object))
            if selected_ghost_object
            else ui.SimpleIntModel(-1)
        )
        self._current_index.add_value_changed_fn(
            lambda m: self._item_changed(self._items[m.as_int])
        )

    def get_item_children(self, item):
        return self._items

    def get_item_value_model(self, item: GhostObjectItem, column_id):
        if not item:
            return self._current_index
        return item.model

    def select_prim_path(self, prim_path: str):
        for index, item in enumerate(self._items):
            if item.ghost_object.prim_path == prim_path:
                self._current_index.set_value(index)
                break

    @property
    def selected_ghost_object(self) -> GhostObject | None:
        if self._current_index.as_int < 0 or self._current_index.as_int >= len(
            self._items
        ):
            return None
        return self._items[self._current_index.as_int].ghost_object


class GhostObjectSelector:
    def __init__(
        self,
        ghost_objects: list[GhostObject],
        selected_ghost_object: GhostObject | None = None,
        ghost_object_changed_fn=None,
        can_select_in_scene: bool = False,
        can_select_from_scene: bool = False,
    ):
        self.ghost_objects = ghost_objects
        self._ghost_object_changed_fn = ghost_object_changed_fn
        self._assign_go_subscription = None
        self._stage_event_subscription = None
        self.can_select_in_scene = can_select_in_scene
        self._can_select_from_scene = can_select_from_scene
        self._container = ui.Frame()

        self.ghost_objects_model = GhostObjectModel(
            selected_ghost_object=selected_ghost_object,
            ghost_objects=self.ghost_objects,
        )

        self._assign_go_subscription = (
            self.ghost_objects_model.subscribe_item_changed_fn(
                lambda model,
                item,
                weak_self=weakref.proxy(self): weak_self._assign_ghost_object(
                    model, item
                )
            )
        )

        if self.selected_ghost_object is None and self._can_select_from_scene:
            self._select_ghost_object_from_stage_selection()

        self._subscribe_to_stage_selection()

        self._build_ui()

    def _build_ui(self):
        self._container.clear()
        with self._container:
            ui.ComboBox(self.ghost_objects_model)

    def _deferred_build_ui(self):
        async def wait_one_frame_and_build():
            await omni.kit.app.get_app().next_update_async()
            self._build_ui()

        run_coroutine(wait_one_frame_and_build())

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type == int(omni.usd.StageEventType.SELECTION_CHANGED):
            self._select_ghost_object_from_stage_selection()

    def _select_ghost_object_from_stage_selection(self):
        selection = cast(omni.usd.Selection, omni.usd.get_context().get_selection())
        stage: Usd.Stage = omni.usd.get_context().get_stage()
        selected_prim_paths = selection.get_selected_prim_paths()
        if len(selected_prim_paths) > 1 or len(selected_prim_paths) == 0:
            carb.log_verbose(
                "Multiple or no prims selected in stage, cannot select ghost object"
            )
            return
        selected_prim: Usd.Prim = stage.GetPrimAtPath(selected_prim_paths[0])
        if not selected_prim:
            return
        carb.log_verbose(
            f"Select {selected_prim.GetPath().pathString}, old selection: {self.selected_ghost_object.prim_path if self.selected_ghost_object else 'None'}"
        )
        if (
            self.selected_ghost_object
            and selected_prim.GetPath().pathString
            == self.selected_ghost_object.prim_path
        ):
            carb.log_verbose(f"{selected_prim} is already the selected ghost object")
            return
        if not GhostObjectUtils.is_ghost_object(selected_prim):
            carb.log_verbose(f"{selected_prim} is not a ghost object")
            return

        self.ghost_objects_model.select_prim_path(selected_prim.GetPath().pathString)
        self._deferred_build_ui()

    def _assign_ghost_object(self, model: GhostObjectModel, item: GhostObjectItem):
        self._ghost_object_changed_fn(item.ghost_object)

        if not self.can_select_in_scene:
            return
        selection: omni.usd.Selection = omni.usd.get_context().get_selection()
        selection.set_selected_prim_paths([item.ghost_object.prim_path])

    def _subscribe_to_stage_selection(self):
        if self._stage_event_subscription:
            return
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
                name="ghost_object_selection_stage_event",
            )
        )

    @property
    def selected_ghost_object(self) -> GhostObjectItem | None:
        return self.ghost_objects_model.selected_ghost_object

    @property
    def can_select_from_scene(self) -> bool:
        return self._can_select_from_scene

    @can_select_from_scene.setter
    def can_select_from_scene(self, value: bool):
        self._can_select_from_scene = value
        if self._can_select_from_scene:
            self._subscribe_to_stage_selection()
            self._select_ghost_object_from_stage_selection()
