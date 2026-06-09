"""Shared UI styles for trajectory planner components."""

from wandelbots.omni.ui.colors import NOVAColor

_TOOLTIP_SUB = {
    "Tooltip": {
        "background_color": NOVAColor.TOOLTIP_BACKGROUND.color,
        "color": NOVAColor.TOOLTIP_TEXT.color,
        "border_width": 1,
        "border_radius": 4,
        "border_color": NOVAColor.TOOLTIP_BORDER.color,
        "margin": 4,
    },
}

TOOLTIP_STYLE = _TOOLTIP_SUB

ICON_BTN_STYLE = {
    "Button": {
        "background_color": 0x00000000,
    },
    "Button:hovered": {"background_color": NOVAColor.BUTTON_HOVER.color},
    **_TOOLTIP_SUB,
}
