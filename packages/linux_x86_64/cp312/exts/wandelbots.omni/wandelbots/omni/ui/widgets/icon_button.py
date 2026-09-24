from typing import Callable, Optional

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.wb_theme import TOOLTIP_RESET, build_tooltip
from wandelbots.omni.ui.utils import get_icon, is_ui_busy

# Edge length of the chip. Public so a row can leave a matching gap where a
# chip would sit, keeping stacked rows the same width.
ICON_BUTTON_SIZE = 20
_ICON_SIZE = 16

# Chip look via a scoped "Button" selector so the translucent fill stays on the
# button without cascading. A single interactive ui.Button is used (not a
# transparent ZStack overlay) because such overlays don't reliably receive
# clicks/tooltips/hover in this Kit build. The tooltip is self-drawn via
# build_tooltip; TOOLTIP_RESET hides the native popup wrapper.
_CHIP_STYLE = {
    "Button": {
        "background_color": NOVAColor.SURFACE_OVERLAY.color,
        "border_color": NOVAColor.SURFACE_OVERLAY_HOVER.color,
        "border_width": 0,
        "border_radius": 4,
        # Pin padding so the button's natural size is EXACTLY ICON_BUTTON_SIZE (16px image +
        # 2px each side). Default button padding would overflow the 20px box and,
        # since omni.ui treats row height as preferred, stretch the whole header.
        "padding": (ICON_BUTTON_SIZE - _ICON_SIZE) // 2,
        "margin": 0,
    },
    "Button.Image": {"color": 0x8FFFFFFF},
    "Button:hovered": {
        "background_color": NOVAColor.SURFACE_OVERLAY_HOVER.color,
    },
    **TOOLTIP_RESET,
}


def IconButton(
    icon: str,
    tooltip: str = "",
    clicked_fn: Optional[Callable] = None,
    enabled: bool = True,
    identifier: Optional[str] = None,
    **kwargs,
) -> ui.Button:
    """Interactive clickable icon in a uniform 20x20 chip.

    A single ``ui.Button`` carries the icon via ``image_url`` and the chip face
    via a scoped ``{"Button": {...}}`` style; ``:hovered`` brightens the chip.
    The button handles clicks, tooltip and hover directly, which is reliable in
    this build (an alpha-0 overlay button stacked in a ``ui.ZStack`` is not).
    """
    kwargs.setdefault("width", ICON_BUTTON_SIZE)
    kwargs.setdefault("height", ICON_BUTTON_SIZE)

    button_kwargs = {
        "text": "",
        "image_url": get_icon(icon),
        "image_width": _ICON_SIZE,
        "image_height": _ICON_SIZE,
        "enabled": enabled,
        "style": _CHIP_STYLE,
        **kwargs,
    }
    if tooltip:
        button_kwargs["tooltip_fn"] = lambda t=tooltip: build_tooltip(t)
    if clicked_fn is not None:
        # Ignore clicks while a blocking operation runs; omni.ui's ``enabled``
        # does not reliably gate this button when it is nested in a
        # ScrollingFrame, so consult the global busy gate here.
        def _gated_click(_fn=clicked_fn):
            if is_ui_busy():
                return
            _fn()

        button_kwargs["clicked_fn"] = _gated_click
    if identifier is not None:
        button_kwargs["identifier"] = identifier

    return ui.Button(**button_kwargs)
