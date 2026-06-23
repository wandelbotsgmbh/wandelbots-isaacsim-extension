"""Collider List window — flat TreeView of all prims with CollisionAPI.

Registered under Tools -> Wandelbots NOVA -> Collider List.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import weakref

import omni.kit.actions.core
import omni.kit.app
import omni.kit.menu.utils
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from pxr import Tf, Usd, UsdPhysics

from wandelbots.omni.constants import EXTENSION_ID, EXTENSION_WINDOW_MENU_ROOT
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.styles import ICON_BTN_STYLE
from wandelbots.omni.ui.utils import defer_call, get_icon

from wandelbots.omni.ui.tool.collider_list.collider_item import (
    ColliderItem,
    ISAAC_MESH_APPROXIMATION_TYPES,
    NATIVE_SHAPE_TYPES,
)
from wandelbots.omni.ui.tool.collider_list.collider_model import ColliderModel
from wandelbots.omni.ui.tool.collider_list.collider_delegate import ColliderDelegate

WINDOW_MENU_ROOT = "Tools"

# Type-filter options — every collider type Isaac Sim supports: all mesh
# approximations (omni.physx MESH_APPROXIMATIONS), the native collision shapes,
# and the bare-mesh fallback. Sorted A->Z, with "All types" pinned to the top as
# the clear-filter option.
_TYPE_FILTER_OPTIONS = ["All types"] + sorted(
    set(ISAAC_MESH_APPROXIMATION_TYPES) | NATIVE_SHAPE_TYPES | {"mesh"},
    key=str.lower,
)


class ColliderListWindow:
    """Window showing all colliders on stage in a flat TreeView."""

    def __init__(self):
        self.window = ui.Window("Collider List", width=500, height=400)
        self.window.set_visibility_changed_fn(
            lambda _: omni.kit.menu.utils.refresh_menu_items(WINDOW_MENU_ROOT)
        )
        self.window.visible = False
        self.window.deferred_dock_in("Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE)

        self._model = ColliderModel()
        self._delegate = ColliderDelegate(
            on_remove_fn=self._remove_collider,
            on_toggle_fn=self._toggle_collider,
            on_sort_fn=self._sort_by_column,
            on_type_changed_fn=self._change_collider_type,
        )
        self._tree_view: ui.TreeView | None = None
        self._sort_ascending: dict[int, bool] = {1: True, 2: True, 3: True}
        self._syncing_selection: bool = False
        # True while we author USD ourselves, so our own edits don't trigger a
        # redundant external refresh via the stage listener below.
        self._suppress_listener: bool = False
        # Listens for collider changes made anywhere on the stage (preset tool,
        # property panel, scripts) so the list stays in sync. Re-created on open.
        self._stage_listener = None
        # One-shot next-frame subscription used to coalesce bursts of notices
        # (e.g. the preset tool applying colliders in batches) into one refresh.
        self._pending_refresh_sub = None

        self._build_ui()

        # Subscribe to stage selection changes (Events 2.0)
        self._stage_event_sub = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(
                lambda event, ws=weakref.ref(self): (
                    ws()._on_stage_event(event) if ws() else None
                )
            )
        )
        self._register_stage_listener()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.window.frame.clear()
        with self.window.frame:
            with ui.VStack(spacing=2):
                self._build_description()
                self._build_toolbar()
                self._build_filter_row()
                self._build_count_label()
                self._build_tree_view()

    def _build_description(self):
        with ui.HStack(height=0):
            ui.Spacer(width=8)
            ui.Label(
                "Review, enable/disable, retype or remove the colliders on your "
                "stage before exporting to Wandelbots NOVA.",
                word_wrap=True,
                style={
                    "font_size": 12,
                    "color": NOVAColor.TEXT_SECONDARY.color,
                },
            )
            ui.Spacer(width=8)

    def _build_toolbar(self):
        with ui.HStack(height=28, spacing=4):
            ui.Spacer(width=8)
            ui.Label(
                "Colliders on Stage",
                style={"font_size": 15},
                width=0,
            )
            ui.Spacer()
            ui.Button(
                "",
                width=24,
                height=24,
                image_url=get_icon("refresh.svg"),
                image_width=16,
                image_height=16,
                tooltip="Refresh collider list",
                clicked_fn=self._refresh,
                style=ICON_BTN_STYLE,
            )
            ui.Spacer(width=8)

    def _build_filter_row(self):
        # Filter bar styled to match the reachability tool: a translucent bar
        # holding a dark search field (with overlaid placeholder) on the left and
        # the collider-type filter on the right.
        with ui.ZStack(height=30):
            ui.Rectangle(
                style={
                    "background_color": ui.color("#FFFFFF1A"),
                    "border_radius": 0,
                }
            )
            with ui.VStack(spacing=4):
                ui.Spacer(height=2)
                with ui.HStack(height=22, spacing=0):
                    ui.Spacer(width=8)
                    with ui.ZStack(width=ui.Fraction(1), height=22):
                        self._search_field = ui.StringField(
                            height=22,
                            style={
                                "background_color": ui.color("#1A1A1A"),
                                "border_radius": 4,
                                "color": NOVAColor.TEXT_SECONDARY.color,
                            },
                            tooltip="Filter by prim path or collider type",
                        )
                        self._search_placeholder = ui.Label(
                            "Filter colliders by string ...",
                            style={
                                "color": ui.color("#666666"),
                                "margin_width": 6,
                            },
                            alignment=ui.Alignment.LEFT_CENTER,
                        )
                        self._search_field.model.add_value_changed_fn(
                            lambda m, ws=weakref.ref(self): (
                                ws()._on_search_changed(m) if ws() else None
                            )
                        )
                    ui.Spacer(width=6)
                    ui.Label(
                        "Type",
                        width=0,
                        alignment=ui.Alignment.LEFT_CENTER,
                        style={
                            "color": NOVAColor.TEXT_SECONDARY.color,
                            "font_size": 12,
                        },
                    )
                    ui.Spacer(width=6)
                    self._type_frame = ui.Frame(width=160)
                    with self._type_frame:
                        self._type_combo = ui.ComboBox(0, *_TYPE_FILTER_OPTIONS)
                        self._type_combo.model.add_item_changed_fn(
                            lambda m, _i, ws=weakref.ref(self): (
                                ws()._on_type_filter_changed(m) if ws() else None
                            )
                        )
                    ui.Spacer(width=8)
                ui.Spacer(height=2)

    def _on_search_changed(self, search_model):
        text = search_model.get_value_as_string()
        self._search_placeholder.visible = text == ""
        self._model.set_search(text)
        self._update_count_label()

    def _on_type_filter_changed(self, combo_model):
        idx = combo_model.get_item_value_model().get_value_as_int()
        value = (
            _TYPE_FILTER_OPTIONS[idx]
            if 0 <= idx < len(_TYPE_FILTER_OPTIONS)
            else "All types"
        )
        self._model.set_type_filter(value)
        self._update_count_label()

    def _build_count_label(self):
        with ui.HStack(height=18):
            ui.Spacer(width=8)
            self._count_label = ui.Label(
                "0 colliders",
                height=18,
                style={
                    "font_size": 12,
                    "color": NOVAColor.TEXT_SECONDARY.color,
                },
            )

    def _build_tree_view(self):
        with ui.ScrollingFrame(
            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
            style={
                "ScrollingFrame": {
                    "background_color": NOVAColor.TREEVIEW_BACKGROUND.color
                }
            },
        ):
            self._tree_view = ui.TreeView(
                self._model,
                delegate=self._delegate,
                root_visible=False,
                header_visible=True,
                columns_resizable=True,
                height=0,
                column_widths=[
                    ui.Pixel(32),
                    ui.Fraction(3),
                    ui.Fraction(2),
                    ui.Fraction(1),
                    ui.Pixel(36),
                ],
                selection_changed_fn=self._on_selection_changed,
                style={
                    "TreeView": {
                        "background_color": NOVAColor.TREEVIEW_BACKGROUND.color,
                    },
                    "TreeView.Item": {
                        "margin": 0,
                        "background_color": NOVAColor.TREEVIEW_BACKGROUND.color,
                    },
                    "TreeView.Row": {
                        "margin": 0,
                        "background_color": NOVAColor.TREEVIEW_BACKGROUND.color,
                    },
                    "TreeView.Item:selected": {
                        "background_color": ui.color("#8E56FC40"),
                        "border_color": 0x00000000,
                        "border_width": 0,
                    },
                    "TreeView.Row:selected": {
                        "background_color": ui.color("#8E56FC40"),
                        "border_color": 0x00000000,
                        "border_width": 0,
                    },
                    "TreeView.Item:focused": {
                        "border_color": 0x00000000,
                        "border_width": 0,
                    },
                    "TreeView.Row:focused": {
                        "border_color": 0x00000000,
                        "border_width": 0,
                    },
                    "TreeView.Item:hovered": {
                        "background_color": NOVAColor.TREEVIEW_HOVERED.color,
                    },
                    "TreeView.Row:hovered": {
                        "background_color": NOVAColor.TREEVIEW_HOVERED.color,
                    },
                },
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _refresh(self):
        self._model.refresh()
        self._rebuild_tree_widgets()
        self._update_count_label()

    def _rebuild_tree_widgets(self):
        """Force the delegate to re-run build_widget for all rows so reused
        ColliderItem objects show their updated type/info/enabled state.

        omni.ui's TreeView only re-runs the delegate when an item's identity
        changes; bulk edits reuse the same ColliderItem objects, so we clear
        the delegate's widget caches and explicitly mark the rows dirty (same
        pattern as the trajectory planner's refresh_tree_view)."""
        self._delegate._widgets.clear()
        self._delegate._subs.clear()
        if self._tree_view:
            self._tree_view.dirty_widgets()

    def _update_count_label(self):
        items = self._model.items
        total = len(items)
        enabled = sum(1 for i in items if i.enabled)
        disabled = total - enabled
        shown = len(self._model.displayed_items)
        suffix = f"({enabled} enabled, {disabled} disabled)"
        if shown == total:
            self._count_label.text = f"{total} colliders {suffix}"
        else:
            self._count_label.text = f"Showing {shown} of {total} colliders {suffix}"

    def _sort_by_column(self, column_id: int):
        ascending = self._sort_ascending.get(column_id, True)
        if column_id == 1:
            self._model.sort_by_name(ascending)
        elif column_id == 2:
            self._model.sort_by_type(ascending)
        elif column_id == 3:
            self._model.sort_by_vertices(ascending)
        self._sort_ascending[column_id] = not ascending

    def _remove_collider(self, item: ColliderItem):
        """Remove CollisionAPI from the prim and refresh the list."""
        self._suppress_listener = True
        try:
            self._model.remove_item(item)
        finally:
            self._suppress_listener = False
        self._update_count_label()

    def _toggle_collider(self, item: ColliderItem):
        """Toggle collider enabled state — bulk-applies to all selected rows."""
        new_state = not item.enabled

        self._suppress_listener = True
        try:
            if item.prim_path in self._delegate.selected_paths:
                for model_item in self._model.items:
                    if model_item.prim_path in self._delegate.selected_paths:
                        self._model.set_item_enabled(model_item, new_state)
            else:
                self._model.set_item_enabled(item, new_state)
        finally:
            self._suppress_listener = False

        # Force rebuild all rows to update checkbox states and text colors.
        # Deferred because we are inside the CheckBox's value-changed callback.
        defer_call(self._rebuild_tree_widgets)
        self._update_count_label()

    def _change_collider_type(self, item: ColliderItem, new_type: str):
        """Change the collider approximation type — bulk-applies to all selected rows."""
        selected_paths = self._delegate.selected_paths
        bulk = item.prim_path in selected_paths
        changed: list[ColliderItem] = []
        skipped: list[ColliderItem] = []
        self._suppress_listener = True
        try:
            if bulk:
                for model_item in self._model.items:
                    if model_item.prim_path not in selected_paths:
                        continue
                    if model_item.is_native_shape:
                        skipped.append(model_item)  # primitive shapes can't retype
                    elif model_item.collider_type != new_type:
                        self._model.change_collider_type(model_item, new_type)
                        changed.append(model_item)
            else:
                self._model.change_collider_type(item, new_type)
        finally:
            self._suppress_listener = False

        # Re-apply the filter: a changed type may move a row in/out of the view.
        self._model.update_filter()
        self._update_count_label()
        # Force rebuild to update dropdowns/info on all affected rows, then
        # restore the row highlight (the rebuild drops it). Deferred because we
        # are inside the ComboBox's item-changed callback.
        defer_call(self._rebuild_tree_widgets)
        defer_call(self._reapply_tree_selection)

        # Feedback only for a genuine multi-selection bulk apply.
        if len(selected_paths) > 1:
            if changed:
                nm.post_notification(
                    f"Changed {len(changed)} collider(s) to '{new_type}'.",
                    duration=3.0,
                    status=nm.NotificationStatus.INFO,
                )
            if skipped:
                nm.post_notification(
                    f"{len(skipped)} primitive collider(s) can't change type "
                    "and were skipped.",
                    duration=4.0,
                    status=nm.NotificationStatus.WARNING,
                )

    def _reapply_tree_selection(self):
        """Re-apply the current selection to the tree after a rebuild so the
        highlighted rows stay in sync with the selected prims."""
        if not self._tree_view:
            return
        displayed = self._model.displayed_items
        selected_items = [
            item
            for item in displayed
            if item.prim_path in self._delegate.selected_paths
        ]
        self._syncing_selection = True
        self._tree_view.selection = selected_items
        self._syncing_selection = False

    # ------------------------------------------------------------------
    # Selection sync
    # ------------------------------------------------------------------

    def _on_selection_changed(self, selection: list):
        """Called by TreeView when native selection changes (row click)."""
        if self._syncing_selection:
            return
        self._delegate.selected_paths = {
            item.prim_path for item in selection if isinstance(item, ColliderItem)
        }
        if self._delegate.selected_paths:
            self._syncing_selection = True
            ctx = omni.usd.get_context()
            if ctx:
                ctx.get_selection().set_selected_prim_paths(
                    list(self._delegate.selected_paths), True
                )
            self._syncing_selection = False

    def _on_stage_event(self, event):
        """Handle stage events — selection sync, and re-arm the collider
        listener when the stage is swapped."""
        if event.type == int(omni.usd.StageEventType.SELECTION_CHANGED):
            self._on_stage_selection_changed()
        elif event.type == int(omni.usd.StageEventType.OPENED):
            self._register_stage_listener()
            if self.window.visible:
                self._refresh()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._stage_listener = None

    # ------------------------------------------------------------------
    # Live updates — react to colliders changed elsewhere on the stage
    # ------------------------------------------------------------------

    def _register_stage_listener(self):
        """(Re)subscribe to USD object changes on the current stage."""
        self._stage_listener = None
        stage = omni.usd.get_context().get_stage()
        if stage:
            self._stage_listener = Tf.Notice.Register(
                Usd.Notice.ObjectsChanged,
                lambda notice, sender, ws=weakref.ref(self): (
                    ws()._on_objects_changed(notice) if ws() else None
                ),
                stage,
            )

    def _on_objects_changed(self, notice):
        """USD object-change callback: refresh when a collider is added,
        removed, or retyped anywhere on the stage."""
        if self._suppress_listener or not self.window.visible:
            return
        if self._is_collision_relevant(notice):
            self._request_refresh()

    def _is_collision_relevant(self, notice) -> bool:
        """True if the notice touches a collider (so the list must refresh)."""
        stage = omni.usd.get_context().get_stage()
        known = {item.prim_path for item in self._model.items}

        # Resynced paths cover structural changes: applying/removing CollisionAPI
        # resyncs the prim, and deleting a prim resyncs its path.
        for path in notice.GetResyncedPaths():
            prim_path = path.GetPrimPath().pathString
            if prim_path in known:
                return True  # a collider we track changed or was removed
            if stage:
                prim = stage.GetPrimAtPath(path.GetPrimPath())
                if prim and prim.IsValid() and prim.HasAPI(UsdPhysics.CollisionAPI):
                    return True  # a new collider appeared

        # Info-only changes: enabling/disabling or changing the approximation.
        for path in notice.GetChangedInfoOnlyPaths():
            try:
                fields = notice.GetChangedFields(path)
            except Exception:
                fields = []
            if any("physics" in f.lower() or "collision" in f.lower() for f in fields):
                return True
        return False

    def _request_refresh(self):
        """Coalesce a burst of notices into a single refresh on the next frame."""
        if self._pending_refresh_sub is not None:
            return
        self._pending_refresh_sub = (
            omni.kit.app.get_app()
            .get_update_event_stream()
            .create_subscription_to_pop(
                lambda _e, ws=weakref.ref(self): (
                    ws()._do_pending_refresh() if ws() else None
                )
            )
        )

    def _do_pending_refresh(self):
        # Dropping the only reference unsubscribes the one-shot callback.
        self._pending_refresh_sub = None
        self._refresh()
        # Re-highlight the row(s) matching the current stage selection.
        self._on_stage_selection_changed()

    def _on_stage_selection_changed(self):
        """When a prim is selected in the stage, highlight the matching row."""
        if self._syncing_selection:
            return
        if not self.window.visible:
            return

        selected_paths = (
            omni.usd.get_context().get_selection().get_selected_prim_paths()
        )
        if not selected_paths:
            if not self._delegate.selected_paths:
                return
            self._delegate.selected_paths = set()
            if self._tree_view:
                self._tree_view.selection = []
            return

        selected_set = set(selected_paths)
        # Only rows currently visible in the tree can be selected.
        displayed = self._model.displayed_items
        new_selection = {
            item.prim_path for item in displayed if item.prim_path in selected_set
        }
        if new_selection == self._delegate.selected_paths:
            return
        self._delegate.selected_paths = new_selection
        if self._tree_view:
            selected_items = [
                item for item in displayed if item.prim_path in new_selection
            ]
            self._tree_view.selection = selected_items


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


@dataclass
class ColliderListWindowSubscription:
    collider_list_window: ColliderListWindow = None
    menu_subscriptions: list = None

    def __del__(self):
        if self.collider_list_window:
            self.collider_list_window._stage_event_sub = None
            self.collider_list_window._stage_listener = None
            self.collider_list_window._pending_refresh_sub = None
            self.collider_list_window.window.visible = False
        omni.kit.menu.utils.remove_menu_items(self.menu_subscriptions, WINDOW_MENU_ROOT)


def register_collider_list_window():
    collider_list_window = ColliderListWindow()

    def toggle_visibility():
        collider_list_window.window.visible = not collider_list_window.window.visible
        if collider_list_window.window.visible:
            collider_list_window._refresh()

    def _is_visible(
        win_ref: Callable[[], ColliderListWindow | None] = weakref.ref(
            collider_list_window
        ),
    ):
        return win_ref().window.visible if win_ref() else False

    ext_id = EXTENSION_ID
    name = "Collider List"
    action_name = "toggle_collider_list_window"
    action_unique = f"{ext_id}_{name}_{action_name}"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(ext_id, action_unique)
    action_registry.register_action(
        ext_id, action_unique, toggle_visibility, display_name=name, tag="MenuItem"
    )

    return ColliderListWindowSubscription(
        collider_list_window,
        omni.kit.menu.utils.add_menu_items(
            [
                omni.kit.menu.utils.MenuItemDescription(
                    name=EXTENSION_WINDOW_MENU_ROOT,
                    sub_menu=[
                        omni.kit.menu.utils.MenuItemDescription(
                            name=name,
                            onclick_action=(ext_id, action_unique),
                            ticked_fn=_is_visible,
                        )
                    ],
                )
            ],
            WINDOW_MENU_ROOT,
        ),
    )
