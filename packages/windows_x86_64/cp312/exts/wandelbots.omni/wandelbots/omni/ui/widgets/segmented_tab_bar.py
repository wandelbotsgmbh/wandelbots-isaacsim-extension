"""Segmented tab bar: a row of equally sized buttons with exactly one active.

Built from real buttons, because transparent click catchers do not reliably
receive click and hover events in this Kit build (see icon_button.py).
"""

from typing import Callable, Sequence

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.wb_theme import CORNER_RADIUS

_TAB_ACTIVE = {
    "Button": {
        "background_color": NOVAColor.SURFACE_OVERLAY.color,
        "border_radius": CORNER_RADIUS,
        "padding": 6,
        "margin": 0,
    },
    "Button.Label": {"color": NOVAColor.TEXT_PRIMARY_CONTRAST.color},
}
_TAB_INACTIVE = {
    "Button": {
        "background_color": NOVAColor.SURFACE_TRANSPARENT.color,
        # Outline so the unselected tab still reads as a clickable segment.
        "border_color": NOVAColor.DIVIDER.color,
        "border_width": 1,
        "border_radius": CORNER_RADIUS,
        "padding": 6,
        "margin": 0,
    },
    "Button:hovered": {
        "background_color": NOVAColor.SURFACE_OVERLAY_HOVER.color,
    },
    "Button.Label": {"color": NOVAColor.TEXT_SECONDARY.color},
}


class SegmentedTabBar:
    def __init__(
        self,
        labels: Sequence[str],
        on_changed_fn: Callable[[int], None] | None = None,
        selected: int = 0,
        height: int = 28,
    ):
        self._on_changed_fn = on_changed_fn
        self._selected = selected
        self._buttons: list[ui.Button] = []
        with ui.HStack(height=height, spacing=4):
            for index, label in enumerate(labels):
                button = ui.Button(
                    label,
                    width=ui.Fraction(1),
                    clicked_fn=lambda i=index: self.select(i),
                )
                self._buttons.append(button)
        self._apply_styles()

    @property
    def selected(self) -> int:
        return self._selected

    def select(self, index: int) -> None:
        if index == self._selected or not 0 <= index < len(self._buttons):
            return
        self._selected = index
        self._apply_styles()
        if self._on_changed_fn:
            self._on_changed_fn(index)

    def _apply_styles(self) -> None:
        for index, button in enumerate(self._buttons):
            button.set_style(_TAB_ACTIVE if index == self._selected else _TAB_INACTIVE)
