from __future__ import annotations

from typing import Callable, Optional

import omni.ui as ui

LABEL_WIDTH = 110


def build_input_row(
    label: str,
    tooltip: str = "",
    height: int = 26,
    build_widget_fn: Optional[Callable[[], None]] = None,
) -> ui.Label:
    with ui.HStack(height=height, spacing=8):
        ui.Spacer(width=5)
        lbl = ui.Label(label, width=LABEL_WIDTH, tooltip=tooltip)
        if build_widget_fn:
            build_widget_fn()
        ui.Spacer(width=5)
    return lbl
