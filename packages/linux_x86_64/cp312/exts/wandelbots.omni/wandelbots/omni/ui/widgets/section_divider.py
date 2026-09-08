import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.wb_theme import DIVIDER_INSET


def section_divider(inset: float = DIVIDER_INSET, thickness: float = 1) -> None:
    """Hairline divider line, inset from the panel's left and right edges.

    Uses a fixed-height ``ui.Line`` rather than ``ui.Separator``, which expands
    to fill vertical space and would push siblings as the window grows. Pass a
    larger *inset* for a narrower line (e.g. a subordinate divider inside a card)
    and a larger *thickness* for a stronger line.
    """
    with ui.HStack(height=0):
        ui.Spacer(width=inset)
        ui.Line(style={"color": NOVAColor.DIVIDER.color, "border_width": thickness})
        ui.Spacer(width=inset)
