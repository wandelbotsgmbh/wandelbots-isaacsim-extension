import asyncio
import sys
import weakref
from functools import partial
from typing import Callable

import omni.ui as ui
from omni.kit.widget.stage import StageWidget
from omni.kit.property.usd.relationship import SelectionWatch
from pxr import Usd


class PrimSelectDialog:
    def __init__(
        self,
        stage,
        select_btn_label="Select",
        modal_window=False,
        window_title="Select Prim",
        window_size=(400, 400),
    ):
        assert stage is not None, "Stage is required in PrimSelectDialog"
        self._window_title = window_title
        self._select_btn_label = select_btn_label
        self._weak_stage = weakref.ref(stage)

        self._selected_prims: list[Usd.Prim] = []

        self._modal_window = modal_window
        self._prims_selection_limit = None

        self._show_result_future: asyncio.Future = None

        def on_window_visibility_changed(visible):
            if not visible:
                self._stage_widget.open_stage(None)
                if self._show_result_future and not self._show_result_future.done():
                    self._show_result_future.set_result(None)
            else:
                # NVIDIA: Only attach the stage when picker is open. Otherwise the Tf notice listener in StageWidget kills perf
                self._stage_widget.open_stage(self._weak_stage())

        self._window = ui.Window(
            self._window_title,
            width=window_size[0],
            height=window_size[1],
            visible=False,
            flags=ui.WINDOW_FLAGS_MODAL if self._modal_window else ui.WINDOW_FLAGS_NONE,
            visibility_changed_fn=on_window_visibility_changed,
        )
        with self._window.frame:
            with ui.VStack():
                with ui.Frame():
                    self._stage_widget = StageWidget(None, columns_enabled=["Type"])
                    self._selection_watch = SelectionWatch(
                        stage=stage,
                        on_selection_changed_fn=self._on_selection_changed,
                        filter_type_list=[],
                        filter_lambda=lambda _: True,
                    )
                    self._stage_widget.set_selection_watch(self._selection_watch)

                with ui.VStack(
                    height=0, style={"Button.Label:disabled": {"color": 0xFF606060}}
                ):
                    self._button = ui.Button(
                        self._select_btn_label,
                        height=10,
                        clicked_fn=partial(
                            PrimSelectDialog._on_select,
                            weak_self=weakref.ref(self),
                        ),
                        enabled=False,
                        identifier="select_button",
                    )

    @staticmethod
    def _on_select(weak_self: weakref):
        weak_self: PrimSelectDialog = weak_self()
        if not weak_self:
            return

        weak_self._show_result_future.set_result(weak_self._selected_prims)
        weak_self._window.visible = False

    def clean(self):
        self._window.set_visibility_changed_fn(None)
        self._window = None
        self._selection_watch = None
        self._stage_widget.open_stage(None)
        self._stage_widget.destroy()
        self._stage_widget = None
        self._prims_selected_fn = None

    async def show(
        self,
        prims_selection_limit,
        prim_filter_fn: Callable[[Usd.Prim], bool] = lambda _: True,
    ) -> list[str] | None:
        self._prims_selection_limit = prims_selection_limit
        self._prim_filter_fn = prim_filter_fn

        self._selection_watch._filter_lambda = self._prim_filter_fn
        self._selection_watch.reset(prims_selection_limit)
        self._window.visible = True
        if self._prim_filter_fn is not None:
            self._stage_widget.filter_by_lambda(
                {"prim_filter": self._prim_filter_fn}, True
            )

        self._show_result_future = asyncio.get_event_loop().create_future()
        await self._show_result_future
        return self._show_result_future.result()

    def _on_selection_changed(self, paths: list[str]):
        stage: Usd.Stage = self._weak_stage()
        # Preserve selection order: keep existing prims that are still selected,
        # then append newly selected prims at the end.
        new_paths_set = set(paths)
        # Retain prims still in the new selection (preserving their order).
        retained = [
            p for p in self._selected_prims if p.GetPath().pathString in new_paths_set
        ]
        retained_paths = {p.GetPath().pathString for p in retained}
        # Append newly added prims in the order they appear in paths.
        added = [stage.GetPrimAtPath(p) for p in paths if p not in retained_paths]
        self._selected_prims = retained + added

        if self._button:
            self._button.enabled = len(self._selected_prims) > 0
        if self._prims_selection_limit > 1:
            if self._prims_selection_limit < sys.maxsize:
                self._button.text = f"{self._select_btn_label} ({len(self._selected_prims)}/{self._prims_selection_limit})"
            else:
                self._button.text = (
                    f"{self._select_btn_label} ({len(self._selected_prims)})"
                )
