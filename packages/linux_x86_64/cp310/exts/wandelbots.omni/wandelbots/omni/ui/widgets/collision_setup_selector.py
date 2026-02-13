from typing import Callable
import wandelbots_api_client.v2 as wb
from omni.kit.async_engine import run_coroutine
import omni.ui as ui
from wandelbots.omni.utils.api import get_api_client_from_config
import carb


class CollisionSetupItem(ui.AbstractItem):
    def __init__(self, collision_setup: str):
        super().__init__()
        self.collision_setup = collision_setup
        self.model = ui.SimpleStringModel(collision_setup)


class CollisionSetupModel(ui.AbstractItemModel):
    def __init__(self, selected_collision_setup: str, collision_setups: list[str]):
        super().__init__()
        self._items = [
            CollisionSetupItem(collision_setup) for collision_setup in collision_setups
        ]
        self._current_index = ui.SimpleIntModel(
            collision_setups.index(selected_collision_setup)
            if selected_collision_setup is not None
            else -1
        )
        self._current_index.add_value_changed_fn(
            lambda m: self._item_changed(self._items[m.as_int])
        )

    def get_item_children(self, item):
        return self._items

    def get_item_value_model(self, item: CollisionSetupItem, column_id):
        if not item:
            return self._current_index
        return item.model


class CollisionSetupSelector:
    def __init__(
        self,
        api_configuration,
        cell: str,
        collision_setup_changed_fn: Callable[[str], None],
        selected_collision_setup: str = None,
    ):
        self._api_configuration = api_configuration
        self._cell = cell
        self._collision_setup_changed_fn = collision_setup_changed_fn
        self._collision_setups = []
        self._selected_collision_setup = selected_collision_setup
        run_coroutine(self.refresh_collision_setups()).add_done_callback(
            lambda _: self._build_ui()
        )
        with ui.Frame() as self._frame:
            ui.Label("Loading collision setups...")

    async def refresh_collision_setups(self):
        try:
            async with get_api_client_from_config(self._api_configuration) as api:
                self._collision_setups = await wb.StoreCollisionSetupsApi(
                    api
                ).list_stored_collision_setups_keys(cell=self._cell)
        except Exception as e:
            self._collision_setups = []
            carb.log_warn(f"Failed to fetch collision setups: {e}")

    def _build_ui(self):
        self._frame.clear()
        with self._frame:
            if len(self._collision_setups) == 0:
                ui.Label("No collision setups found")
                return
            self._collision_setups_model = CollisionSetupModel(
                self._selected_collision_setup, self._collision_setups
            )

            def assign_collision_setup(
                model: CollisionSetupModel,
                item: CollisionSetupItem,
            ):
                self._collision_setup_changed_fn(item.collision_setup)

            self._collision_setups_model_sub = (
                self._collision_setups_model.subscribe_item_changed_fn(
                    assign_collision_setup
                )
            )
            ui.ComboBox(self._collision_setups_model)
