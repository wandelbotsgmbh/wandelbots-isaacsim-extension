"""Form row layout helpers.

``labeled_row`` keeps the label at a fixed width and lets the content stretch.
``form_row`` stretches the label instead and pushes a fixed-width field column
to the right margin, the convention of the Connect-to-NOVA forms.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

import omni.ui as ui

from wandelbots.omni.ui.wb_theme import (
    TOOLTIP_RESET,
    BUTTON_HEIGHT,
    FORM_FIELD_WIDTH,
    FORM_SIDE_MARGIN,
    build_tooltip,
)

_DEFAULT_LABEL_WIDTH = 150


@contextmanager
def labeled_row(
    label: str,
    *,
    label_width: int = _DEFAULT_LABEL_WIDTH,
    height: int = 24,
    left_inset: int = 15,
    right_inset: int = 10,
    label_alignment: Optional[ui.Alignment] = None,
):
    """Lay out a form row as ``[inset] label [content] [inset]``.

    The caller builds the content inside the ``with`` block. ``label_alignment``
    is left unset by default so the omni.ui default applies.
    """
    with ui.HStack(height=height):
        ui.Spacer(width=left_inset)
        if label_alignment is None:
            ui.Label(label, width=label_width)
        else:
            ui.Label(label, width=label_width, alignment=label_alignment)
        yield
        ui.Spacer(width=right_inset)


@contextmanager
def form_row(
    label: str,
    tooltip: str | None = None,
    field_width: int = FORM_FIELD_WIDTH,
    height: int = BUTTON_HEIGHT,
    gap: int = 0,
):
    """Build one form row; the ``with`` body populates the field column.

    The label stretches, pushing the field to a right margin that matches the
    left inset, and carries the themed tooltip. ``gap`` keeps the field column
    off the label; it defaults to none so existing forms keep their layout.
    """
    with ui.HStack(height=height, spacing=0):
        ui.Spacer(width=FORM_SIDE_MARGIN)
        label_kwargs = {
            "width": ui.Fraction(1),
            "alignment": ui.Alignment.LEFT_CENTER,
        }
        if tooltip:
            # The tooltip is drawn by hand, a scoped style does not reliably
            # override the default popup in this build (see build_tooltip).
            label_kwargs["style"] = TOOLTIP_RESET
            label_kwargs["tooltip_fn"] = lambda text=tooltip: build_tooltip(text)
        ui.Label(label, **label_kwargs)
        if gap:
            ui.Spacer(width=gap)
        with ui.VStack(width=field_width):
            ui.Spacer()
            with ui.VStack(height=0):
                yield
            ui.Spacer()
        ui.Spacer(width=FORM_SIDE_MARGIN)


def message_row(text: str, color: int, height: int = 0) -> None:
    """One inset, word-wrapped line of feedback under a form: an error, a
    hint or a note, in the given text color."""
    with ui.HStack(height=0):
        ui.Spacer(width=FORM_SIDE_MARGIN)
        ui.Label(text, word_wrap=True, height=height, style={"color": color})
        ui.Spacer(width=FORM_SIDE_MARGIN)
