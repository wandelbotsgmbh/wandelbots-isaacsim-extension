from __future__ import annotations

import asyncio
from typing import Optional

import carb
import omni.ui as ui
from omni.kit.async_engine import run_coroutine

from wandelbots.omni.instances.models import NOVAInstance
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.utils import get_icon
from wandelbots.omni.ui.wb_theme import TOOLTIP_RESET, build_tooltip

_ROBOT_ICON = "RobotFilled.svg"
_EXTERNAL_AXIS_ICON = "external-axes.svg"
_WARNING_ICON = "warning.svg"
_ICON_SIZE = 16


class TypeIcon(ui.VStack):
    """Robot vs. external-axis glyph for a motion group model.

    The model's kinematics are fetched asynchronously, so the icon stays empty
    until the type is known and then renders the matching glyph. Designed to be
    used as a ``CollapsibleSection`` leading widget. Call :meth:`destroy` to
    cancel the in-flight lookup when the owning row is torn down.

    A geometry mismatch replaces the glyph with a warning, because the row is
    collapsed by default and the message inside it is easy to miss.
    """

    def __init__(self, instance: NOVAInstance, model_name: str, **kwargs):
        kwargs.setdefault("width", _ICON_SIZE)
        super().__init__(**kwargs)

        self._instance = instance
        self._model_name = model_name
        self._icon: Optional[str] = None
        self._warning: Optional[str] = None
        self._task: Optional[asyncio.Task] = run_coroutine(self._resolve())

    def destroy(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def show_warning(self, message: str):
        """Replace the glyph with a warning carrying *message* as its tooltip."""
        self._warning = message
        self._render()

    def _render(self):
        self.clear()
        # The warning outranks the type, and shows even before the type
        # resolves: it is the thing the user has to act on.
        icon = _WARNING_ICON if self._warning else self._icon
        if not icon:
            return
        color = (
            NOVAColor.WARNING_LIGHT.color
            if self._warning
            else NOVAColor.TEXT_PRIMARY_CONTRAST.color
        )
        with self:
            ui.Spacer()
            image = ui.Image(
                get_icon(icon),
                width=_ICON_SIZE,
                height=_ICON_SIZE,
                style={"color": color, **TOOLTIP_RESET},
            )
            if self._warning:
                image.set_tooltip_fn(lambda text=self._warning: build_tooltip(text))
            ui.Spacer()

    async def _resolve(self):
        # Imported lazily to avoid a circular import: this widget lives in the
        # shared widgets package, but the resolver lives in the instances package
        # which imports back into widgets at module load.
        from wandelbots.omni.ui.instances.articulations.virtual_controller_service import (
            is_robot_model,
        )

        try:
            is_robot = await is_robot_model(self._instance, self._model_name)
        except Exception as error:
            carb.log_verbose(f"Could not resolve motion group type icon: {error}")
            return
        self._icon = _ROBOT_ICON if is_robot else _EXTERNAL_AXIS_ICON
        self._render()
