"""Optional "Attach Tool" control for the Reachability window.

Lets the user pick a tool USD file. The tool is rendered in the reachability
preview and its convex hulls ride the analyzed model's flange during collision
checking, so the attached tool is the single source of tool geometry for the
analysis. Reachability tests hypothetical models by name, so there is no robot
prim to reference the tool into: the file is read as its own offline stage by
core/collision/tool_geometry.py. A TCP prim inside the tool pre-fills the
caller's manual TCP offset fields, which keep working without a tool.
"""

from __future__ import annotations

import weakref
from typing import Callable

import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine
from omni.kit.window.filepicker import FilePickerDialog
from pxr import Tf, Usd

from wandelbots.omni.core.collision.tool_geometry import (
    ToolGeometry,
    bounding_box_triangles,
    extract_tool_geometry,
    find_tcp_candidates,
    find_tool_root,
    resolve_meters_per_unit,
    tcp_offset_millimeters,
)
from wandelbots.omni.ui.utils import defer_call, get_icon
from wandelbots.omni.ui.wb_theme import (
    COMBOBOX_STYLE,
    FONT_SIZE_LG,
    TOOLTIP_RESET,
    build_tooltip,
)
from wandelbots.omni.ui.widgets.form_row import form_row
from wandelbots.omni.utils.locations import join_location, parent_location

# Above this, fall back to a bounding box instead. Every collider is already
# reduced to its convex hull, but a hull of an already-convex, densely
# tessellated shape can still keep nearly every input point. Also keeps well
# clear of the 16-bit index limit sc.PolygonMesh-style primitives use.
_MAX_PREVIEW_TRIANGLES = 5_000

# Reading a tool asset fails on malformed geometry, which USD and qhull
# report differently.
_TOOL_READ_ERRORS = (ValueError, IndexError, Tf.ErrorException)


class AttachToolWidget:
    def __init__(self, tcp_offset_picked_fn: Callable[[list[float]], None]):
        self._tcp_offset_picked_fn = tcp_offset_picked_fn
        self._tool_usd_path: str | None = None
        self._tool_mesh_vertices: list[tuple[float, float, float]] | None = None
        self._tool_collider_hulls: list[list[tuple[float, float, float]]] | None = None
        self._tcp_options: list[tuple[str, list[float]]] = []
        self._selected_tcp_index: int | None = None
        self._tcp_combo_sub = None
        self._file_picker: FilePickerDialog | None = None
        # Filled by set_tcp_row_frame: the caller-provided container below
        # the Attach Tool row that hosts the TCP picker form row.
        self._tcp_row_frame: ui.Frame | None = None
        self._root_widget = ui.HStack(spacing=5)
        self._build_ui()

    @property
    def tool_mesh_vertices(self) -> list[tuple[float, float, float]] | None:
        """Preview triangle mesh (may be a bounding-box stand-in for very
        dense tools); display-only."""
        return self._tool_mesh_vertices

    @property
    def tool_collider_hulls(self) -> list[list[tuple[float, float, float]]] | None:
        """Per-collider convex-hull vertices (meters, relative to the tool
        root) for collision checking - always the real collider geometry,
        regardless of the preview's triangle cap."""
        return self._tool_collider_hulls

    def clear(self) -> None:
        """Detach the current tool and rebuild the row."""
        self._reset_state()
        defer_call(self._build_ui)

    def _reset_state(self) -> None:
        self._tool_usd_path = None
        self._tool_mesh_vertices = None
        self._tool_collider_hulls = None
        self._tcp_options = []
        self._selected_tcp_index = None
        self._tcp_combo_sub = None

    # -- attach flow -----------------------------------------------------

    def _on_attach_clicked(self) -> None:
        def _on_apply(filename: str, path: str, ws=weakref.proxy(self)) -> None:
            ws._file_picker.hide()
            usd_path = join_location(path, filename) if path else filename
            run_coroutine(ws._attach_tool_async(usd_path))

        def _on_cancel(filename: str, path: str, ws=weakref.proxy(self)) -> None:
            ws._file_picker.hide()

        self._file_picker = FilePickerDialog(
            "Select Tool USD",
            apply_button_label="Select",
            click_apply_handler=_on_apply,
            click_cancel_handler=_on_cancel,
            file_extension_options=[("USD Files", "*.usd *.usda *.usdc")],
        )
        self._file_picker.show(parent_location(omni.usd.get_context().get_stage_url()))

    async def _attach_tool_async(self, usd_path: str) -> None:
        tool_stage = self._open_tool_stage(usd_path)
        if tool_stage is None:
            return
        tool_root = find_tool_root(tool_stage)
        if tool_root is None:
            self._warn(f"Tool USD '{usd_path}' has no usable root prim.")
            return

        meters_per_unit = resolve_meters_per_unit(tool_stage)
        geometry = self._read_geometry(tool_root, meters_per_unit, usd_path)
        tcp_options = self._read_tcp_options(tool_root, meters_per_unit, usd_path)
        self._apply_attachment(usd_path, geometry, tcp_options)

    @staticmethod
    def _open_tool_stage(usd_path: str) -> Usd.Stage | None:
        try:
            tool_stage = Usd.Stage.Open(usd_path)
        except Tf.ErrorException as exc:
            carb.log_warn(f"Could not open tool USD '{usd_path}': {exc}")
            tool_stage = None
        if tool_stage is None:
            AttachToolWidget._warn(f"Could not open tool USD '{usd_path}'")
            return None
        try:
            # A layer for this URL can still sit in the process-wide layer
            # registry from a previous attach and would silently serve that
            # attach's content instead of what the file holds now.
            tool_stage.Reload()
        except Tf.ErrorException as exc:
            carb.log_warn(f"Could not reload tool stage '{usd_path}': {exc}")
        return tool_stage

    def _read_geometry(
        self, tool_root: Usd.Prim, meters_per_unit: float, usd_path: str
    ) -> ToolGeometry:
        geometry = self._extract_geometry(tool_root, meters_per_unit, usd_path)
        if not geometry.preview_triangles:
            self._warn(
                f'No usable collision geometry found in tool "{usd_path}" '
                f"({geometry.source_note}).",
                duration=8.0,
            )
            return geometry
        geometry.preview_triangles = self._capped_preview_triangles(
            geometry.preview_triangles
        )
        return geometry

    @staticmethod
    def _extract_geometry(
        tool_root: Usd.Prim, meters_per_unit: float, usd_path: str
    ) -> ToolGeometry:
        try:
            return extract_tool_geometry(tool_root, meters_per_unit)
        except _TOOL_READ_ERRORS as exc:
            carb.log_warn(f"Could not extract tool mesh from '{usd_path}': {exc}")
            return ToolGeometry(source_note=f"geometry extraction failed: {exc}")

    @staticmethod
    def _capped_preview_triangles(
        triangles: list[tuple[float, float, float]],
    ) -> list[tuple[float, float, float]]:
        """Replace a preview mesh too large to render with its bounding box.
        The collision hulls keep the real geometry."""
        triangle_count = len(triangles) // 3
        if triangle_count <= _MAX_PREVIEW_TRIANGLES:
            return triangles
        nm.post_notification(
            f"Tool mesh has {triangle_count} triangles, too many to render "
            f"directly (limit: {_MAX_PREVIEW_TRIANGLES}) - showing its "
            "bounding box instead. Collision checking still uses the full "
            "hulls. Attach a simplified version of this tool for an accurate "
            "preview.",
            duration=10.0,
            status=nm.NotificationStatus.WARNING,
        )
        return bounding_box_triangles(triangles)

    def _read_tcp_options(
        self, tool_root: Usd.Prim, meters_per_unit: float, usd_path: str
    ) -> list[tuple[str, list[float]]]:
        tcp_options: list[tuple[str, list[float]]] = []
        for candidate in find_tcp_candidates(tool_root):
            try:
                offset = tcp_offset_millimeters(candidate, tool_root, meters_per_unit)
            except _TOOL_READ_ERRORS as exc:
                carb.log_warn(
                    f"Could not compute TCP offset for '{candidate.GetPath()}': {exc}"
                )
                continue
            tcp_options.append((candidate.GetPath().pathString, offset))
        if not tcp_options:
            self._warn(
                f'No TCP found in tool "{usd_path}". '
                "Enter the TCP offset manually if needed.",
                duration=8.0,
            )
        return tcp_options

    def _apply_attachment(
        self,
        usd_path: str,
        geometry: ToolGeometry,
        tcp_options: list[tuple[str, list[float]]],
    ) -> None:
        self._tool_usd_path = usd_path
        self._tool_mesh_vertices = geometry.preview_triangles
        self._tool_collider_hulls = geometry.collider_hulls or None
        self._tcp_options = tcp_options
        self._selected_tcp_index = 0 if tcp_options else None
        if tcp_options:
            self._tcp_offset_picked_fn(tcp_options[0][1])
        defer_call(self._build_ui)

    @staticmethod
    def _warn(message: str, duration: float = 5.0) -> None:
        nm.post_notification(
            message, duration=duration, status=nm.NotificationStatus.WARNING
        )

    def _on_tcp_combo_changed(self, index: int) -> None:
        if not (0 <= index < len(self._tcp_options)):
            return
        self._selected_tcp_index = index
        self._tcp_offset_picked_fn(self._tcp_options[index][1])

    # -- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        self._root_widget.clear()
        with self._root_widget:
            if self._tool_usd_path is None:
                self._build_none_ui()
            else:
                self._build_attached_ui()
        self._rebuild_tcp_row()

    def _build_none_ui(self) -> None:
        with self._root_widget:
            ui.Button(
                text="Attach Tool...",
                clicked_fn=lambda a=weakref.proxy(self): a._on_attach_clicked(),
                height=20,
                alignment=ui.Alignment.CENTER,
                style={"margin": 0},
            )

    def _build_attached_ui(self) -> None:
        display_name = self._tool_usd_path.rsplit("/", 1)[-1]
        triangle_count = (
            len(self._tool_mesh_vertices) // 3 if self._tool_mesh_vertices else 0
        )
        with self._root_widget:
            with ui.HStack(spacing=0):
                # 24px like the icon buttons beside it and the TCP combo
                # below; font pinned to omni.ui's 16px default so it matches
                # the (unstyled) coordinate drags above.
                ui.StringField(
                    model=ui.SimpleStringModel(
                        f"{display_name}  ({triangle_count} tri)"
                    ),
                    read_only=True,
                    height=24,
                    alignment=ui.Alignment.CENTER,
                    width=ui.Fraction(1),
                    tooltip_fn=lambda p=self._tool_usd_path: build_tooltip(p),
                    style={"font_size": FONT_SIZE_LG, **TOOLTIP_RESET},
                )
                ui.Button(
                    clicked_fn=lambda a=weakref.proxy(self): a.clear(),
                    image_url=get_icon("close.svg"),
                    image_width=20,
                    image_height=20,
                    width=ui.Pixel(24),
                    height=ui.Pixel(24),
                    tooltip_fn=lambda: build_tooltip("Detach tool"),
                    style={"margin": 0, "padding": 2, **TOOLTIP_RESET},
                )
            ui.Button(
                clicked_fn=lambda a=weakref.proxy(self): a._on_attach_clicked(),
                image_url=get_icon("colorize.svg"),
                image_width=20,
                image_height=20,
                width=ui.Pixel(24),
                height=ui.Pixel(24),
                tooltip_fn=lambda: build_tooltip("Attach a different tool"),
                style={"margin": 0, "padding": 2, **TOOLTIP_RESET},
            )

    def set_tcp_row_frame(self, frame: ui.Frame) -> None:
        """Adopt the container for the TCP picker row, placed by the caller
        directly below the Attach Tool row (so the picker becomes a proper
        form row with "TCP" in the label column). Filled when an attached
        tool carries several TCPs, cleared otherwise."""
        self._tcp_row_frame = frame
        self._rebuild_tcp_row()

    def _rebuild_tcp_row(self) -> None:
        frame = self._tcp_row_frame
        if frame is None:
            return
        frame.clear()
        if self._tool_usd_path is None or len(self._tcp_options) <= 1:
            return
        with frame:
            with form_row(
                "TCP",
                tooltip=(
                    "TCP of the attached tool used to auto-fill the TCP "
                    "offset fields above."
                ),
            ):
                # A ComboBox stretches UP to a taller parent (while a
                # smaller `height` is ignored); the 24px wrapper matches
                # the name field and buttons in the row above.
                with ui.HStack(height=24):
                    # COMBOBOX_STYLE pins 14px; lift to the 16px the
                    # coordinate drags and the name field use.
                    combo = ui.ComboBox(
                        self._selected_tcp_index or 0,
                        *[path.rsplit("/", 1)[-1] for path, _ in self._tcp_options],
                        style={**COMBOBOX_STYLE, "font_size": FONT_SIZE_LG},
                        tooltip_fn=lambda: build_tooltip(
                            "TCP of the attached tool used to auto-fill the "
                            "TCP offset fields above."
                        ),
                    )
        # The subscription must be kept alive on self - a discarded return
        # value is collected immediately and the callback never fires.
        self._tcp_combo_sub = combo.model.subscribe_item_changed_fn(
            lambda m, _, a=weakref.proxy(self): a._on_tcp_combo_changed(
                m.get_item_value_model().as_int
            )
        )
