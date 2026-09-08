from __future__ import annotations

import asyncio
from typing import Optional

import carb
import omni.ui as ui
from omni.kit.async_engine import run_coroutine

from wandelbots.omni.instances.models import NOVAInstance
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.utils import get_icon

_ROBOT_ICON = "RobotFilled.svg"
_EXTERNAL_AXIS_ICON = "external-axes.svg"
_ICON_SIZE = 16


class TypeIcon(ui.VStack):
    """Robot vs. external-axis glyph for a motion group model.

    The model's kinematics are fetched asynchronously, so the icon stays empty
    until the type is known and then renders the matching glyph. Designed to be
    used as a ``CollapsibleSection`` leading widget. Call :meth:`destroy` to
    cancel the in-flight lookup when the owning row is torn down.
    """

    def __init__(self, instance: NOVAInstance, model_name: str, **kwargs):
        kwargs.setdefault("width", _ICON_SIZE)
        super().__init__(**kwargs)

        self._instance = instance
        self._model_name = model_name
        self._icon: Optional[str] = None
        self._task: Optional[asyncio.Task] = run_coroutine(self._resolve())

    def destroy(self):
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _render(self):
        self.clear()
        if not self._icon:
            return
        with self:
            ui.Spacer()
            ui.Image(
                get_icon(self._icon),
                width=_ICON_SIZE,
                height=_ICON_SIZE,
                style={"color": NOVAColor.TEXT_PRIMARY_CONTRAST.color},
            )
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
