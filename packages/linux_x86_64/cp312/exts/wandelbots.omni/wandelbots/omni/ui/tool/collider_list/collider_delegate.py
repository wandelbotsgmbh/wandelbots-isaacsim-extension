"""ColliderDelegate — TreeView delegate rendering collider rows."""

from __future__ import annotations

from typing import Callable

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.styles import ICON_BTN_STYLE
from wandelbots.omni.ui.utils import get_icon

from wandelbots.omni.ui.tool.collider_list.collider_item import (
    ColliderItem,
    NOVA_MESH_COLLIDER_TYPES,
    ROW_HEIGHT,
)

# Row background when a collider type is not compatible with Wandelbots NOVA:
# a translucent red, matching the trajectory planner's unreachable-row highlight
# (pose_tree_widget.py — "#EF5350" at 0x30 alpha).
_INCOMPATIBLE_HIGHLIGHT = ui.color("#EF535030")


class ColliderDelegate(ui.AbstractItemDelegate):
    """Renders each collider row with 5 columns: Checkbox | Path | Type | Vertices | Delete."""

    def __init__(
        self,
        on_remove_fn: Callable[[ColliderItem], None] | None = None,
        on_toggle_fn: Callable[[ColliderItem], None] | None = None,
        on_sort_fn: Callable[[int], None] | None = None,
        on_type_changed_fn: Callable[[ColliderItem, str], None] | None = None,
    ):
        super().__init__()
        self._on_remove_fn = on_remove_fn
        self._on_toggle_fn = on_toggle_fn
        self._on_sort_fn = on_sort_fn
        self._on_type_changed_fn = on_type_changed_fn
        self.selected_paths: set[str] = set()
        self._widgets: list = []
        self._subs: list = []

    # ------------------------------------------------------------------
    # Delegate interface
    # ------------------------------------------------------------------

    def build_branch(self, model, item, column_id, level, expanded):
        pass

    def build_widget(self, model, item, column_id, level, expanded):
        if item is None:
            return

        with ui.ZStack(height=ROW_HEIGHT):
            # Highlight the whole row when the collider is not NOVA-compatible.
            # build_widget runs per column, so drawing the rectangle in every
            # cell spans the full row (same approach as the trajectory planner).
            if not item.is_nova_compatible:
                ui.Rectangle(
                    style={"background_color": _INCOMPATIBLE_HIGHLIGHT},
                    tooltip="Collider type is not compatible with Wandelbots NOVA",
                )
            with ui.HStack(height=ROW_HEIGHT):
                if column_id == 0:
                    self._build_checkbox_cell(item)
                elif column_id == 1:
                    self._build_path_cell(item)
                elif column_id == 2:
                    self._build_type_cell(item)
                elif column_id == 3:
                    self._build_info_cell(item)
                elif column_id == 4:
                    self._build_delete_cell(item)

    def build_header(self, column_id):
        headers = ["", "Path", "Type", "Vertices", ""]
        sortable = {1, 2, 3}
        with ui.HStack(height=22):
            ui.Spacer(width=4)
            if column_id in sortable and self._on_sort_fn:
                label = ui.Label(
                    headers[column_id],
                    alignment=ui.Alignment.LEFT_CENTER,
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 13,
                    },
                )
                label.set_mouse_pressed_fn(
                    lambda x, y, btn, mod, col=column_id: (
                        self._on_sort_fn(col) if btn == 0 else None
                    )
                )
            else:
                ui.Label(
                    headers[column_id] if column_id < len(headers) else "",
                    alignment=ui.Alignment.LEFT_CENTER,
                    style={"color": NOVAColor.TEXT_SECONDARY.color, "font_size": 13},
                )

    # ------------------------------------------------------------------
    # Cell builders
    # ------------------------------------------------------------------

    def _build_checkbox_cell(self, item: ColliderItem):
        """Column 0 — enable/disable checkbox."""
        with ui.VStack(width=30):
            ui.Spacer()
            with ui.HStack(height=0):
                ui.Spacer(width=6)
                cb = ui.CheckBox(
                    width=18,
                    height=18,
                    tooltip="Enable/disable collider",
                    style={
                        "color": 0xFFFFFFFF,
                        "background_color": 0xFF4A4A4A,
                        "border_color": 0xFF888888,
                        "border_width": 1,
                        "border_radius": 3,
                    },
                )
                cb.model.set_value(item.enabled)
                cb.model.add_value_changed_fn(
                    lambda m, i=item: (
                        self._on_toggle_fn(i) if self._on_toggle_fn else None
                    )
                )
                ui.Spacer(width=6)
            ui.Spacer()

    def _build_path_cell(self, item: ColliderItem):
        """Column 1 — full prim path."""
        ui.Spacer(width=4)
        ui.Label(
            item.prim_path,
            alignment=ui.Alignment.LEFT_CENTER,
            style={
                "font_size": 14,
                "color": NOVAColor.TEXT_PRIMARY.color
                if item.enabled
                else NOVAColor.TEXT_DISABLED.color,
            },
            elided_text=True,
            tooltip=item.prim_path,
        )
        ui.Spacer(width=4)

    def _build_type_cell(self, item: ColliderItem):
        """Column 2 — collider type dropdown (mesh) or static label (native shape)."""
        ui.Spacer(width=4)
        if item.is_native_shape:
            ui.Label(
                item.collider_type,
                alignment=ui.Alignment.LEFT_CENTER,
                style={
                    "font_size": 14,
                    "color": NOVAColor.TEXT_SECONDARY.color,
                },
            )
        else:
            # NOVA-compatible mesh approximations. If the prim currently uses a
            # type outside that set, keep it as the first option so it is shown
            # and selected instead of being silently dropped.
            types = list(NOVA_MESH_COLLIDER_TYPES)
            if item.collider_type not in types:
                types = [item.collider_type] + types
            current_idx = types.index(item.collider_type)

            def _on_combo_changed(combo_model, _changed_item, i=item, t=types):
                idx = combo_model.get_item_value_model().get_value_as_int()
                if not (0 <= idx < len(t)):
                    return
                new_type = t[idx]
                if new_type != i.collider_type and self._on_type_changed_fn:
                    self._on_type_changed_fn(i, new_type)

            combo = ui.ComboBox(
                current_idx,
                *types,
                height=22,
                tooltip="Select collider type",
                style={
                    "font_size": 13,
                    "color": NOVAColor.TEXT_PRIMARY.color,
                    "background_color": 0xFF3D3D3D,
                    "border_radius": 3,
                    "padding": 4,
                    "margin": 0,
                },
            )
            combo.model.add_item_changed_fn(_on_combo_changed)
            self._widgets.append(combo)
        ui.Spacer(width=4)

    def _build_info_cell(self, item: ColliderItem):
        """Column 3 — collider vertex count."""
        ui.Spacer(width=4)
        ui.Label(
            item.info,
            alignment=ui.Alignment.LEFT_CENTER,
            style={
                "font_size": 14,
                "color": NOVAColor.TEXT_SECONDARY.color,
            },
        )
        ui.Spacer(width=4)

    def _build_delete_cell(self, item: ColliderItem):
        """Column 4 — remove collider button."""
        ui.Spacer(width=2)
        ui.Button(
            "",
            width=24,
            height=24,
            image_url=get_icon("close.svg"),
            image_width=16,
            image_height=16,
            tooltip="Remove collider",
            clicked_fn=lambda i=item: (
                self._on_remove_fn(i) if self._on_remove_fn else None
            ),
            style=ICON_BTN_STYLE,
        )
        ui.Spacer(width=2)
