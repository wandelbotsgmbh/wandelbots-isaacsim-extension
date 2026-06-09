from __future__ import annotations

import weakref
from typing import Callable

import omni.kit.app
import omni.ui as ui
from omni.kit.async_engine import run_coroutine
from omni.kit.property.usd.relationship import RelationshipTargetPicker
from omni.kit.property.usd.widgets import ICON_PATH as _KIT_ICON_PATH
from omni.kit.window.property.templates import HORIZONTAL_SPACING, LABEL_HEIGHT
from pxr import Usd

_REMOVE_ICON = str(_KIT_ICON_PATH / "remove.svg")
_FOLDER_ICON = str(_KIT_ICON_PATH / "small_folder.png")


class _PrimPathItem(ui.AbstractItem):
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.model = ui.SimpleStringModel(path)


class _PrimPathListModel(ui.AbstractItemModel):
    def __init__(self, paths: list[str]):
        super().__init__()
        self._paths = paths
        self._items = [_PrimPathItem(p) for p in paths]

    def get_item_children(self, item):
        return [] if item else self._items

    def get_item_value_model_count(self, item):
        return 1

    def get_item_value_model(self, item, column_id):
        return item.model

    def get_drag_mime_data(self, item):
        return str(self._items.index(item))

    def drop_accepted(self, target_item, source, drop_location=-1):
        return source in self._items and not target_item and drop_location >= 0

    def drop(self, target_item, source, drop_location=-1):
        src = self._items.index(source)
        if src == drop_location:
            return
        item = self._items.pop(src)
        path = self._paths.pop(src)
        dst = drop_location - 1 if src < drop_location else drop_location
        self._items.insert(dst, item)
        self._paths.insert(dst, path)
        self._item_changed(None)


class _PrimPathDelegate(ui.AbstractItemDelegate):
    def __init__(
        self, on_replace_fn: Callable[[str], None], on_remove_fn: Callable[[str], None]
    ):
        super().__init__()
        self._on_replace = on_replace_fn
        self._on_remove = on_remove_fn

    def build_branch(self, model, item, column_id, level, expanded):
        pass

    def build_widget(self, model, item, column_id, level, expanded):
        _ROW_HEIGHT = LABEL_HEIGHT + 4
        path = item.path
        with ui.ZStack(height=_ROW_HEIGHT):
            ui.Rectangle(style={"background_color": 0x20FFFFFF, "border_radius": 2})
            with ui.HStack(spacing=HORIZONTAL_SPACING, height=_ROW_HEIGHT):
                field = ui.StringField(
                    name="models", read_only=True, height=LABEL_HEIGHT
                )
                field.model.set_value(path)
                ui.Spacer(width=HORIZONTAL_SPACING)
                ui.Button(
                    "",
                    width=_ROW_HEIGHT,
                    height=_ROW_HEIGHT,
                    style={
                        "image_url": _FOLDER_ICON,
                        "background_color": 0x40000000,
                        "border_radius": 2,
                        "margin": 0,
                        "padding": 4,
                    },
                    clicked_fn=lambda p=path: self._on_replace(p),
                    tooltip="Replace target",
                )
                ui.Button(
                    "",
                    width=_ROW_HEIGHT,
                    height=_ROW_HEIGHT,
                    fill_policy=ui.FillPolicy.PRESERVE_ASPECT_FIT,
                    clicked_fn=lambda p=path: self._on_remove(p),
                    name="remove",
                    style={
                        "image_url": _REMOVE_ICON,
                        "background_color": 0x40000000,
                        "border_radius": 2,
                        "margin": 0,
                        "padding": 0,
                    },
                    tooltip="Remove target",
                )


class PrimPathList:
    """Reusable prim path list widget with Kit SDK-style rows, drag reorder,
    browse-replace, remove, and an Add Target button backed by RelationshipTargetPicker."""

    def __init__(
        self,
        stage: Usd.Stage,
        *,
        on_changed_fn: Callable[[list[str]], None] | None = None,
        filter_type_list: list | None = None,
        filter_lambda: Callable | None = None,
        target_name: str = "Target",
        target_plural_name: str = "Targets",
    ):
        self._paths: list[str] = []
        self._on_changed_fn = on_changed_fn
        self._target_name = target_name
        self._target_plural_name = target_plural_name
        self._filter_type_list = filter_type_list or []
        self._filter_lambda = filter_lambda
        self._frame: ui.Frame | None = None
        self._delegate: _PrimPathDelegate | None = None
        self._model: _PrimPathListModel | None = None

        self._picker = RelationshipTargetPicker(
            stage=stage,
            filter_type_list=self._filter_type_list,
            filter_lambda=self._filter_lambda,
            additional_widget_kwargs={
                "target_name": self._target_name,
                "target_plural_name": self._target_plural_name,
            },
        )

        self._frame = ui.Frame(height=0)
        self._rebuild()

    @property
    def paths(self) -> list[str]:
        return list(self._paths)

    @paths.setter
    def paths(self, value: list[str]):
        self._paths = list(value)
        self._rebuild()

    def _notify_changed(self):
        if self._on_changed_fn:
            self._on_changed_fn(list(self._paths))

    def _rebuild(self):
        _ROW_HEIGHT = LABEL_HEIGHT + 4
        if not self._frame:
            return
        self._frame.clear()
        with self._frame:
            with ui.VStack(height=0, spacing=2):
                self._delegate = _PrimPathDelegate(
                    self._replace_path, self._remove_path
                )
                self._model = _PrimPathListModel(self._paths)
                with ui.Frame(
                    height=_ROW_HEIGHT * len(self._paths) if self._paths else 0
                ):
                    ui.TreeView(
                        self._model,
                        delegate=self._delegate,
                        root_visible=False,
                        header_visible=False,
                        drop_between_items=True,
                        style={
                            "TreeView:selected": {"background_color": 0x00},
                            "TreeView": {"background_color": 0xFFA07D4F},
                        },
                    )
                ui.Button(
                    f"{ui.get_custom_glyph_code('${glyphs}/menu_context.svg')} Add {self._target_name}...",
                    height=LABEL_HEIGHT,
                    tooltip=f"Open picker to add a new {self._target_name.lower()}",
                    clicked_fn=lambda ws=weakref.ref(self): (
                        ws()._open_picker() if ws() else None
                    ),
                )

    def _open_picker(self):
        self._picker.show(
            targets_limit=0, on_targets_selected=self._on_targets_selected
        )

    def _on_targets_selected(self, paths: list[str]):
        for path in paths:
            if path not in self._paths:
                self._paths.append(path)
        self._rebuild()
        self._notify_changed()

    def _replace_path(self, old_path: str):
        def on_selected(paths: list[str]):
            if not paths:
                return
            try:
                idx = self._paths.index(old_path)
            except ValueError:
                return
            self._paths[idx] = paths[0]
            self._rebuild()
            self._notify_changed()

        self._picker.show(targets_limit=1, on_targets_selected=on_selected)

    def _remove_path(self, path: str):
        if path in self._paths:
            self._paths.remove(path)

        async def _deferred():
            await omni.kit.app.get_app().next_update_async()
            self._rebuild()
            self._notify_changed()

        run_coroutine(_deferred())

    def reset(self, stage: Usd.Stage | None = None):
        self._paths.clear()
        if stage is not None:
            self._picker.clean()
            self._picker = RelationshipTargetPicker(
                stage=stage,
                filter_type_list=self._filter_type_list,
                filter_lambda=self._filter_lambda,
                additional_widget_kwargs={
                    "target_name": self._target_name,
                    "target_plural_name": self._target_plural_name,
                },
            )
        self._rebuild()
        self._notify_changed()

    def destroy(self):
        if self._picker:
            self._picker.clean()
            self._picker = None
        self._frame = None
        self._delegate = None
        self._model = None
        self._on_changed_fn = None
