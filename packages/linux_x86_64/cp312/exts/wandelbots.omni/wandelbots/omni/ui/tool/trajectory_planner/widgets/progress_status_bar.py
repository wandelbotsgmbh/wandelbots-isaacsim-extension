"""Progress bar with optional status hint label."""

from __future__ import annotations

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor


class ProgressStatusBar:
    """Thin wrapper around a ProgressBar + hint label pair."""

    def __init__(self, name: str = "") -> None:
        self._name = name
        self._progress_bar: ui.ProgressBar | None = None
        self._hint_label: ui.Label | None = None

    def build(self) -> None:
        # Progress bar first so it renders above its status label.
        self._progress_bar = ui.ProgressBar(
            height=4,
            style={
                "color": NOVAColor.PRIMARY_MAIN.color,
                "background_color": NOVAColor.PROGRESS_BAR_BACKGROUND.color,
                "border_radius": 2,
                "secondary_color": NOVAColor.PROGRESS_BAR_BACKGROUND.color,
                "font_size": 1,
            },
        )
        self._progress_bar.model.set_value(0.0)
        self._progress_bar.visible = False

        with ui.HStack(height=16):
            ui.Spacer(width=5)
            self._hint_label = ui.Label(
                "",
                visible=False,
                style={
                    "color": NOVAColor.TEXT_DISABLED.color,
                    "font_size": 13,
                },
            )
            ui.Spacer(width=5)

    def show(self, value: float = 0.1) -> None:
        if self._progress_bar:
            self._progress_bar.model.set_value(value)
            self._progress_bar.visible = True

    def update(self, value: float) -> None:
        if self._progress_bar:
            self._progress_bar.model.set_value(min(value, 1.0))

    def hide(self) -> None:
        if self._progress_bar:
            self._progress_bar.model.set_value(0.0)
            self._progress_bar.visible = False
        self.clear_hint()

    def set_hint(self, text: str) -> None:
        if self._hint_label:
            prefix = f"[{self._name}] " if self._name else ""
            self._hint_label.text = f"{prefix}{text}"
            self._hint_label.visible = True

    def clear_hint(self) -> None:
        if self._hint_label:
            self._hint_label.text = ""
            self._hint_label.visible = False

    @property
    def visible(self) -> bool:
        return self._progress_bar.visible if self._progress_bar else False

    @property
    def value(self) -> float:
        return (
            self._progress_bar.model.get_value_as_float() if self._progress_bar else 0.0
        )

    @property
    def hint_text(self) -> str:
        return self._hint_label.text if self._hint_label else ""

    @property
    def hint_visible(self) -> bool:
        return self._hint_label.visible if self._hint_label else False

    def restore_state(
        self,
        progress_visible: bool,
        progress_value: float,
        hint_text: str,
        hint_visible: bool,
    ) -> None:
        if progress_visible and self._progress_bar:
            self._progress_bar.model.set_value(progress_value)
            self._progress_bar.visible = True
        if hint_visible and self._hint_label:
            self._hint_label.text = hint_text
            self._hint_label.visible = True
