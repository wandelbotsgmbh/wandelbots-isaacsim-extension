import omni.ui as ui


class MotionCommandItem(ui.AbstractItem):
    def __init__(self, motion_command: str, display_name: str = None):
        super().__init__()
        self.motion_command = motion_command
        self.display_name = display_name
        if self.display_name is None:
            self.display_name = motion_command
        self.model = ui.SimpleStringModel(self.display_name)


class MotionCommandsModel(ui.AbstractItemModel):
    def __init__(
        self,
        selected_motion_command: str | None,
        motion_commands: list[(str, str)],
    ):
        super().__init__()

        self._items = [
            MotionCommandItem(motion_command, display_name=display_name)
            for motion_command, display_name in motion_commands
        ]

        self._current_index = (
            ui.SimpleIntModel(
                [command_id for command_id, _ in motion_commands].index(
                    selected_motion_command
                )
            )
            if selected_motion_command
            else ui.SimpleIntModel(-1)
        )

        self._current_index.add_value_changed_fn(
            lambda m: self._item_changed(self._items[m.as_int])
        )

    def get_item_children(self, item):
        return self._items

    def get_item_value_model(self, item: MotionCommandItem, column_id):
        if not item:
            return self._current_index
        return item.model

    def select_motion_command(self, motion_command: str):
        for index, item in enumerate(self._items):
            if item.motion_command == motion_command:
                self._current_index.set_value(index)
                break

    @property
    def selected_motion_command(self) -> str | None:
        if self._current_index.as_int < 0 or self._current_index.as_int >= len(
            self._items
        ):
            return None
        return self._items[self._current_index.as_int].motion_command
