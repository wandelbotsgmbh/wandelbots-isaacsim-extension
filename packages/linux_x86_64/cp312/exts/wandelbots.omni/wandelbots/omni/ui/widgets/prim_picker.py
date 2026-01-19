import asyncio
from typing import Callable
from attr import dataclass
from pxr import Usd
import omni
import weakref
import omni.ui as ui
from wandelbots.omni.ui.dialogs import PrimSelectDialog
from wandelbots.omni.ui.utils import get_icon
import omni.usd
from omni.kit.async_engine import run_coroutine
import omni.kit.app


@dataclass
class PrimPickerDialogProperties:
    title: str = "Select Prim"
    filter_fn: Callable[[Usd.Prim], bool] = lambda _: True


class PrimPicker:
    def __init__(
        self,
        stage: Usd.Stage,
        prim_picked_fn: Callable[[Usd.Prim], None],
        prim: Usd.Prim = None,
        dialog_properties: PrimPickerDialogProperties = PrimPickerDialogProperties(),
    ):
        self._stage = stage
        self._prim = prim
        self._prim_picked_fn = prim_picked_fn
        self._root_widget = ui.HStack(spacing=5)
        self._dialog_properties = dialog_properties
        self._build_ui()

    def _pick_prim(self):
        def _on_prim_selected(future: asyncio.Future[list[str] | None]):
            prims = future.result()
            self._prim = prims[0] if prims and len(prims) > 0 else None
            self._deferred_build_ui()
            self._prim_picked_fn(self._prim)

        dialog = PrimSelectDialog(
            stage=self._stage,
            window_title=self._dialog_properties.title,
            modal_window=True,
        )
        run_coroutine(
            dialog.show(1, self._dialog_properties.filter_fn)
        ).add_done_callback(_on_prim_selected)

    def _highlight_prim(self):
        if not self._prim:
            return

        context_selection = omni.usd.get_context().get_selection()
        context_selection.clear_selected_prim_paths()
        context_selection.set_selected_prim_paths(
            [self._prim.GetPath().pathString], False
        )

    def _clear(self):
        self._prim = None
        self._deferred_build_ui()
        self._prim_picked_fn(None)

    def _build_ui(self):
        self._root_widget.clear()
        with self._root_widget:
            if self._prim is None:
                self._build_prim_none_ui()
            else:
                self._build_prim_selected_ui()

    def _deferred_build_ui(self):
        async def wait_one_frame():
            await omni.kit.app.get_app().next_update_async()
            self._build_ui()

        run_coroutine(wait_one_frame())

    def _build_prim_none_ui(self):
        with self._root_widget:
            ui.Button(
                text="Select prim",
                clicked_fn=lambda a=weakref.proxy(self): a._pick_prim(),
                height=20,
                alignment=ui.Alignment.CENTER,
            )

    def _build_prim_selected_ui(self):
        with self._root_widget:
            with ui.HStack(spacing=0):
                ui.StringField(
                    model=ui.SimpleStringModel(self._prim.GetPath().pathString),
                    read_only=True,
                    height=20,
                    alignment=ui.Alignment.CENTER,
                    width=ui.Fraction(1),
                    mouse_released_fn=lambda a1,
                    a2,
                    a3,
                    a4,
                    a=weakref.proxy(self): a._highlight_prim(),
                )
                ui.Button(
                    clicked_fn=lambda a=weakref.proxy(self): a._clear(),
                    image_url=get_icon("close.svg"),
                    width=ui.Pixel(24),
                    height=ui.Pixel(24),
                    tooltip="Clear selection",
                    style={"margin": 0, "padding": 2},
                )
            ui.Button(
                clicked_fn=lambda a=weakref.proxy(self): a._pick_prim(),
                image_url=get_icon("colorize.svg"),
                width=ui.Pixel(24),
                height=ui.Pixel(24),
                tooltip="Select a new prim",
                style={"margin": 0, "padding": 2},
            )

    @property
    def prim(self):
        return self._prim
