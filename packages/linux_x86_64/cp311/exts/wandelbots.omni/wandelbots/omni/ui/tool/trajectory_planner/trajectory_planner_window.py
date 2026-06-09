"""Trajectory Planner window - container for independent trajectory planner widgets."""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from typing import Callable

import carb
import omni.kit.actions.core
import omni.kit.menu.utils
import omni.ui as ui
import omni.usd

from wandelbots.omni.constants import EXTENSION_ID, EXTENSION_WINDOW_MENU_ROOT
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.styles import TOOLTIP_STYLE, _TOOLTIP_SUB
from wandelbots.omni.ui.utils import defer_call
from wandelbots.omni.ui.tool.trajectory_planner.pose_context_menu import (
    register_trajectory_pose_context_menu,
)
from wandelbots.omni.ui.tool.trajectory_planner.pose_tree_widget import PoseItem
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_widget import (
    TrajectoryPlannerWidget,
)
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
    get_trajectory_planner_store,
)

WINDOW_MENU_ROOT = "Tools"


class TrajectoryPlannerWindow:
    """Main window hosting multiple independent trajectory planner widgets."""

    _singleton: "TrajectoryPlannerWindow | None" = None

    def __init__(self) -> None:
        if TrajectoryPlannerWindow._singleton is not None:
            try:
                TrajectoryPlannerWindow._singleton.destroy()
            except Exception as exc:
                carb.log_warn(
                    f"Failed to destroy previous TrajectoryPlannerWindow: {exc}"
                )
        TrajectoryPlannerWindow._singleton = self

        self._widgets: list[TrajectoryPlannerWidget] = []
        self._widgets_frame: ui.Frame | None = None
        self._name_field_model: ui.SimpleStringModel | None = None
        self._name_placeholder: ui.Label | None = None
        self._pending_ignore_prim_path: str | None = None

        self._window = ui.Window(
            "Trajectory Planner",
            width=720,
            height=600,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )
        self._window.visible = False
        self._window.deferred_dock_in(
            "Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE
        )
        self._window.set_visibility_changed_fn(self._on_visibility_changed)
        self._build_ui()

        self._stage_event_sub = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(
                lambda event, ws=weakref.ref(self): (
                    ws()._on_stage_event(event) if ws() else None
                )
            )
        )

        # Load saved configs or add a default skill
        self._load_from_store()
        # Sync widget visibility with initial window state
        for w in self._widgets:
            w.set_visible(self._window.visible)

    def destroy(self) -> None:
        self._save_to_store()
        self._stage_event_sub = None
        for w in self._widgets:
            w.destroy()
        self._widgets.clear()
        if self._window:
            self._window.set_visibility_changed_fn(None)
            self._window.visible = False
        self._window = None
        if TrajectoryPlannerWindow._singleton is self:
            TrajectoryPlannerWindow._singleton = None

    def __del__(self) -> None:
        self.destroy()

    @property
    def window(self) -> ui.Window:
        return self._window

    def _on_visibility_changed(self, visible: bool) -> None:
        if not visible:
            self._save_to_store()
            for w in self._widgets:
                w._preview.hide()
        for w in self._widgets:
            w.set_visible(visible)

    def _on_stage_event(self, event) -> None:
        if event.type == int(omni.usd.StageEventType.OPENED):
            self._reset()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._reset()
        elif event.type == int(omni.usd.StageEventType.SELECTION_CHANGED):
            self._on_stage_selection_changed()

    def _reset(self) -> None:
        for w in self._widgets:
            w.destroy()
        self._widgets.clear()
        self._rebuild_widgets()

    # -- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        with self._window.frame:
            with ui.VStack(spacing=0, style=TOOLTIP_STYLE):
                self._content_frame = ui.Frame()
        self._rebuild_content()

    def _rebuild_content(self) -> None:
        if self._content_frame is None:
            return
        self._content_frame.clear()
        is_empty = len(self._widgets) == 0
        self._name_field_model = ui.SimpleStringModel()
        with self._content_frame:
            with ui.VStack(spacing=0):
                if is_empty:
                    ui.Spacer()
                else:
                    with ui.ScrollingFrame(
                        height=ui.Fraction(1),
                        vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,  # The spacing stays so ON/NEEDED has the same effect
                        horizontal_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                    ):
                        self._widgets_frame = ui.Frame(height=0)
                        self._rebuild_widgets_only()
                    ui.Spacer(height=4)
                if is_empty:
                    self._build_create_section_empty()
                    ui.Spacer()
                else:
                    self._build_create_section_inline()
                    ui.Spacer(height=4)

    def _build_name_field(self) -> None:
        with ui.ZStack():
            ui.Rectangle(
                style={
                    "background_color": NOVAColor.BACKGROUND_PAPER_DARK.color,
                    "border_radius": 4,
                },
            )
            with ui.VStack():
                ui.Spacer()
                ui.StringField(
                    self._name_field_model,
                    height=18,
                    style={
                        "background_color": 0x00000000,
                        "font_size": 14,
                        "color": NOVAColor.TEXT_PRIMARY.color,
                        "margin_width": 4,
                    },
                )
                ui.Spacer()
            self._name_placeholder = ui.Label(
                "Name the skill ...",
                style={
                    "color": 0xFF666666,
                    "margin_width": 8,
                    "font_size": 14,
                },
                alignment=ui.Alignment.LEFT_CENTER,
            )
        self._name_field_model.add_value_changed_fn(
            lambda m, ws=weakref.ref(self): (
                setattr(
                    ws()._name_placeholder,
                    "visible",
                    m.get_value_as_string() == "",
                )
                if ws() and ws()._name_placeholder
                else None
            )
        )
        self._name_field_model.add_end_edit_fn(
            lambda m, ws=weakref.ref(self): (
                ws()._add_widget() if ws() and m.get_value_as_string().strip() else None
            )
        )

    def _build_create_section_empty(self) -> None:
        with ui.HStack():
            ui.Spacer(width=ui.Fraction(1))
            with ui.VStack(spacing=8, height=0, width=ui.Fraction(4)):
                with ui.ZStack(height=0):
                    ui.Rectangle(
                        style={
                            "background_color": NOVAColor.COLLAPSIBLE_SECTION_BODY.color,
                            "border_radius": 8,
                        },
                    )
                    with ui.VStack(spacing=8, height=0):
                        ui.Spacer(height=8)
                        with ui.HStack(height=24):
                            ui.Spacer(width=12)
                            self._build_name_field()
                            ui.Spacer(width=12)
                        with ui.HStack(height=24):
                            ui.Spacer(width=12)
                            ui.Button(
                                "Create Skill",
                                height=24,
                                tooltip="Add a new trajectory planner skill.",
                                clicked_fn=lambda ws=weakref.ref(self): (
                                    ws()._add_widget() if ws() else None
                                ),
                                style={
                                    "Button": {
                                        "background_color": NOVAColor.PRIMARY_MAIN.color,
                                        "font_size": 14,
                                        "border_radius": 4,
                                    },
                                    "Button:hovered": {
                                        "background_color": NOVAColor.PRIMARY_DARK.color,
                                    },
                                    **_TOOLTIP_SUB,
                                },
                            )
                            ui.Spacer(width=12)
                        ui.Spacer(height=8)
            ui.Spacer(width=ui.Fraction(1))

    def _build_create_section_inline(self) -> None:
        with ui.ZStack(height=40):
            ui.Rectangle(
                style={
                    "background_color": NOVAColor.COLLAPSIBLE_SECTION_BODY.color,
                    "border_radius": 4,
                },
            )
            with ui.VStack():
                ui.Spacer()
                with ui.HStack(height=24, spacing=8):
                    ui.Spacer(width=8)
                    self._build_name_field()
                    ui.Button(
                        "Create Skill",
                        width=140,
                        height=24,
                        tooltip="Add a new trajectory planner skill.",
                        clicked_fn=lambda ws=weakref.ref(self): (
                            ws()._add_widget() if ws() else None
                        ),
                        style={
                            "Button": {
                                "background_color": 0xFF292929,
                                "font_size": 14,
                                "margin": 0,
                            },
                            "Button:hovered": {
                                "background_color": NOVAColor.BUTTON_HOVER.color
                            },
                            **_TOOLTIP_SUB,
                        },
                    )
                    ui.Spacer(width=8)
                ui.Spacer()

    # -- Widget management -------------------------------------------------

    def _add_widget(self) -> None:
        name = ""
        if self._name_field_model:
            name = self._name_field_model.get_value_as_string().strip()
            self._name_field_model.set_value("")
        if not name:
            name = f"Trajectory {len(self._widgets) + 1}"
        widget = TrajectoryPlannerWidget(
            name=name,
            on_delete=lambda w, ws=weakref.ref(self): (
                ws()._remove_widget(w) if ws() else None
            ),
            on_selection_changed=lambda w, item, ws=weakref.ref(self): (
                ws()._on_widget_selection_changed(w, item) if ws() else None
            ),
        )
        widget.set_visible(self._window.visible)
        self._widgets.append(widget)
        self._save_to_store()
        defer_call(self._rebuild_widgets)

    def _remove_widget(self, widget: TrajectoryPlannerWidget) -> None:
        widget.destroy()
        self._widgets = [w for w in self._widgets if w is not widget]
        self._save_to_store()
        defer_call(self._rebuild_widgets)

    def _rebuild_widgets(self) -> None:
        self._rebuild_content()

    def _rebuild_widgets_only(self) -> None:
        if self._widgets_frame is None:
            return
        self._widgets_frame.clear()
        with self._widgets_frame:
            with ui.VStack(spacing=0, height=0):
                for i, widget in enumerate(self._widgets):
                    if i > 0:
                        ui.Spacer(height=8)
                        ui.Line(
                            height=2,
                            style={
                                "border_width": 2,
                                "color": NOVAColor.DIVIDER.color,
                            },
                        )
                        ui.Spacer(height=8)
                    frame = ui.Frame(height=0)
                    widget.build(frame)

    # -- Persistence -------------------------------------------------------

    @classmethod
    def get_live_configs(cls):
        """Return current widget configs if the window is alive, else None."""
        instance = cls._singleton
        if instance and instance._widgets:
            try:
                return [w.to_config() for w in instance._widgets]
            except Exception:
                return None
        return None

    def _save_to_store(self) -> None:
        try:
            configs = [w.to_config() for w in self._widgets]
            get_trajectory_planner_store().save_configs(configs)
        except Exception as exc:
            carb.log_warn(f"Failed to save trajectory planner configs: {exc}")

    def _load_from_store(self) -> None:
        try:
            configs = get_trajectory_planner_store().load_configs()
        except Exception as exc:
            carb.log_warn(f"Failed to load trajectory planner configs: {exc}")
            configs = []

        if not configs:
            self._add_widget()
            return

        for config in configs:
            widget = TrajectoryPlannerWidget(
                name=config.name,
                on_delete=lambda w, ws=weakref.ref(self): (
                    ws()._remove_widget(w) if ws() else None
                ),
                on_selection_changed=lambda w, item, ws=weakref.ref(self): (
                    ws()._on_widget_selection_changed(w, item) if ws() else None
                ),
            )
            widget.apply_config(config)
            self._widgets.append(widget)
        self._rebuild_widgets()

    # -- Selection sync ----------------------------------------------------

    def _on_stage_selection_changed(self) -> None:
        selected = omni.usd.get_context().get_selection().get_selected_prim_paths()
        if not selected:
            return
        prim_path = selected[0]
        if prim_path == self._pending_ignore_prim_path:
            self._pending_ignore_prim_path = None
            return
        for widget in self._widgets:
            if widget.select_by_prim_path(prim_path):
                return

    def _on_widget_selection_changed(
        self, widget: TrajectoryPlannerWidget, item: PoseItem | None
    ) -> None:
        self._pending_ignore_prim_path = item.prim_path if item else None
        for w in self._widgets:
            if w is not widget:
                w.clear_selection()


# -- Registration ----------------------------------------------------------


@dataclass
class TrajectoryPlannerSubscription:
    window: TrajectoryPlannerWindow = None
    menu_subscriptions: list = field(default_factory=list)
    context_menu_sub: object = None

    def __del__(self):
        if self.window:
            self.window.destroy()
            self.window = None
        if self.menu_subscriptions:
            omni.kit.menu.utils.remove_menu_items(
                self.menu_subscriptions, WINDOW_MENU_ROOT
            )


def register_trajectory_planner_window():
    planner_window = TrajectoryPlannerWindow()
    context_menu_sub = register_trajectory_pose_context_menu()

    def toggle_visibility():
        planner_window.window.visible = not planner_window.window.visible

    def _is_visible(
        window_ref: Callable[[], TrajectoryPlannerWindow | None] = weakref.ref(
            planner_window
        ),
    ):
        return window_ref().window.visible if window_ref() else False

    ext_id = EXTENSION_ID
    name = "Trajectory Planner"
    action_name = "toggle_trajectory_planner_window"
    action_unique = f"{ext_id}_{name}_{action_name}"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(ext_id, action_unique)
    action_registry.register_action(
        ext_id,
        action_unique,
        toggle_visibility,
        display_name=name,
        tag="MenuItem",
    )

    return TrajectoryPlannerSubscription(
        window=planner_window,
        menu_subscriptions=omni.kit.menu.utils.add_menu_items(
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
        context_menu_sub=context_menu_sub,
    )
