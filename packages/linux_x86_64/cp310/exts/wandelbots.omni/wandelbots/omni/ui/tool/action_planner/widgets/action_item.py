import weakref
import omni.ui as ui
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.utils.teaching import GhostObject
from wandelbots.omni.ui.utils import get_icon
from wandelbots.omni.ui.tool.action_planner.utils import PlanAction


class GhostObjectItem(ui.AbstractItem):
    def __init__(self, ghost_object: GhostObject):
        super().__init__()
        self.ghost_object = ghost_object
        self.model = ui.SimpleStringModel(ghost_object.prim_path)


class GhostObjectModel(ui.AbstractItemModel):
    def __init__(
        self, selected_ghost_object: GhostObject, ghost_objects: list[GhostObject]
    ):
        super().__init__()
        self._items = [GhostObjectItem(ghost_object) for ghost_object in ghost_objects]
        self._current_index = ui.SimpleIntModel(
            ghost_objects.index(selected_ghost_object)
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


class ActionItem:
    def __init__(
        self,
        plan_action: PlanAction,
        ghost_objects: list[GhostObject],
        is_first: bool = False,
        is_last: bool = False,
        ghost_object_changed_fn=None,
        play_trajectory_fn=None,
        remove_action_fn=None,
        move_action_fn=None,
    ):
        self.plan_action = plan_action
        self.ghost_objects = ghost_objects
        self.is_first = is_first
        self.is_last = is_last
        self._ghost_object_changed_fn = ghost_object_changed_fn
        self._remove_action_fn = remove_action_fn
        self._move_action_fn = move_action_fn
        self._play_trajectory_fn = play_trajectory_fn
        self._assign_go_subscription = None

        self._build_ui()

    def _build_ui(self):
        self._root = ui.ZStack(style={"margin": 2}, height=0)
        with self._root:
            ui.Rectangle(
                style={
                    "border_width": 1,
                    "border_radius": 4,
                    "border_color": NOVAColor.DIVIDER.color,
                    "background_color": NOVAColor.BACKGROUND_PAPER.color,
                },
                alignment=ui.Alignment.TOP,
            )
            with ui.Frame(style={"padding": 4}, alignment=ui.Alignment.TOP):
                with ui.VStack(width=ui.Fraction(1)):
                    with ui.HStack(height=ui.Pixel(30)):
                        self._pose_label = ui.Label(
                            f"Move to {self.plan_action.ghost_object.pose}",
                            width=ui.Fraction(1),
                        )
                        if self.plan_action.trajectory:
                            ui.Button(
                                image_url=get_icon("play.svg"),
                                width=ui.Pixel(30),
                                height=ui.Pixel(30),
                                clicked_fn=lambda a=weakref.proxy(self): (
                                    a._play_trajectory_fn(self.plan_action.trajectory)
                                ),
                            )
                        ui.Button(
                            image_url=get_icon("arrow_down.svg"),
                            width=ui.Pixel(30),
                            height=ui.Pixel(30),
                            enabled=not self.is_last,
                            style={
                                ":disabled": {
                                    "background_color": NOVAColor.ACTION_DISABLED_BACKGROUND.color,
                                    "color": NOVAColor.TEXT_SECONDARY.color,
                                },
                            },
                            clicked_fn=lambda a=weakref.proxy(self): a._move_action_fn(
                                1
                            ),
                        )
                        ui.Button(
                            image_url=get_icon("arrow_up.svg"),
                            width=ui.Pixel(30),
                            height=ui.Pixel(30),
                            enabled=not self.is_first,
                            style={
                                ":disabled": {
                                    "background_color": NOVAColor.ACTION_DISABLED_BACKGROUND.color,
                                    "color": NOVAColor.TEXT_SECONDARY.color,
                                },
                            },
                            clicked_fn=lambda a=weakref.proxy(self): a._move_action_fn(
                                -1
                            ),
                        )

                        ui.Button(
                            image_url=get_icon("delete.svg"),
                            width=ui.Pixel(30),
                            height=ui.Pixel(30),
                            style={
                                "color": NOVAColor.ACTION_ACTIVE.color,
                            },
                            tooltip="Remove action",
                            clicked_fn=self._remove_action_fn,
                        )

                    def assign_ghost_object(
                        model: GhostObjectModel,
                        item: GhostObjectItem,
                    ):
                        self._ghost_object_changed_fn(item.ghost_object)
                        self._pose_label.text = f"Move to {item.ghost_object.pose}"

                    ghost_objects_model = GhostObjectModel(
                        selected_ghost_object=self.plan_action.ghost_object,
                        ghost_objects=self.ghost_objects,
                    )
                    self._assign_go_subscription = (
                        ghost_objects_model.subscribe_item_changed_fn(
                            assign_ghost_object
                        )
                    )
                    ui.ComboBox(ghost_objects_model)
