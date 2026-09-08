from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Optional

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection


@contextmanager
def group_section(
    title: str,
    *,
    collapsed: bool = False,
    build_header_fn: Optional[Callable[[CollapsibleSection], None]] = None,
):
    """Open a group-level CollapsibleSection and yield its content stack.

    Group sections (e.g. "Cloud Instances", "Custom Instances") share the
    LAYER_BASE header convention so they blend with the window background, and a
    body that stacks child widgets with uniform spacing plus trailing padding.
    Callers build the children inside the ``with`` block.
    """
    section = CollapsibleSection(
        title=title,
        collapsed=collapsed,
        header_color=NOVAColor.LAYER_BASE,
        header_hover_color=NOVAColor.SURFACE_OVERLAY,
        title_color=NOVAColor.TEXT_PRIMARY_CONTRAST,
        build_header_fn=build_header_fn,
    )
    with section.body:
        with ui.VStack(spacing=5, height=0):
            yield section
            ui.Spacer(height=10)
