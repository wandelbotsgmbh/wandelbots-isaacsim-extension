import omni.ui as ui
import carb.settings


class StringValueItem(ui.AbstractItem):
    def __init__(self, string_value: str, display_name: str = None):
        super().__init__()
        self.string_value = string_value
        self.display_name = display_name
        if self.display_name is None:
            self.display_name = string_value
        self.model = ui.SimpleStringModel(self.display_name)


class SettingsStringValuesModel(ui.AbstractItemModel):
    def __init__(
        self,
        carb_setting_path: str,
        string_values: list[(str, str)],
    ):
        super().__init__()

        self._items = [
            StringValueItem(string_value, display_name=display_name)
            for string_value, display_name in string_values
        ]

        if not carb.settings.get_settings().get(carb_setting_path):
            carb.settings.get_settings().set_string(
                carb_setting_path, string_values[0][0]
            )

        selected_string_value = carb.settings.get_settings().get_as_string(
            carb_setting_path
        )

        self._current_index = (
            ui.SimpleIntModel(
                [string_value for string_value, _ in string_values].index(
                    selected_string_value
                )
            )
            if selected_string_value
            and selected_string_value in [v for v, _ in string_values]
            else ui.SimpleIntModel(-1)
        )

        def on_value_changed(m: ui.AbstractValueModel):
            selected_value = self._items[m.as_int]
            carb.settings.get_settings().set(
                carb_setting_path, selected_value.string_value
            )
            self._item_changed(selected_value)

        self._current_index.add_value_changed_fn(on_value_changed)

    def get_item_children(self, item):
        return self._items

    def get_item_value_model(self, item: StringValueItem, column_id):
        if not item:
            return self._current_index
        return item.model

    def select_string_value(self, string_value: str):
        for index, item in enumerate(self._items):
            if item.string_value == string_value:
                self._current_index.set_value(index)
                break

    @property
    def selected(self) -> str | None:
        if self._current_index.as_int < 0 or self._current_index.as_int >= len(
            self._items
        ):
            return None
        return self._items[self._current_index.as_int].string_value
