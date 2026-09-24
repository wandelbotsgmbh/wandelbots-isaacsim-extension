"""Shared shell of the tool windows that export to and load from NOVA: a
segmented tab bar over pages that are built once, plus the Tools menu entry."""

from __future__ import annotations

import weakref
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

import omni.kit.actions.core
import omni.kit.menu.utils
import omni.ui as ui

from wandelbots.omni.constants import EXTENSION_ID, EXTENSION_WINDOW_MENU_ROOT
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.wb_theme import SECTION_GAP, SPACING_MD, SPACING_SM
from wandelbots.omni.ui.widgets.segmented_tab_bar import SegmentedTabBar

WINDOW_MENU_ROOT = "Tools"


class ToolForm(Protocol):
    def refresh_instances(self) -> None: ...


@dataclass(frozen=True)
class TabPage:
    label: str
    # Builds the page into the enclosing layout and returns its form, whose
    # instance list is refreshed every time the window is shown.
    build_fn: Callable[[], ToolForm]


class TabbedToolWindow:
    def __init__(
        self, title: str, pages: Sequence[TabPage], width: int = 400, height: int = 300
    ):
        self.window = ui.Window(title, width=width, height=height)
        self.window.set_visibility_changed_fn(
            lambda visible, weak_self=weakref.proxy(self): (
                weak_self._on_visibility_changed(visible)
            )
        )
        self.window.visible = False
        self.window.deferred_dock_in("Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE)

        self._pages = list(pages)
        self._tab_bar: SegmentedTabBar | None = None
        self._page_stacks: list[ui.VStack] = []
        self.forms: list[ToolForm] = []
        self._build_ui()

    def select_tab(self, index: int) -> None:
        self._tab_bar.select(index)

    def is_page_visible(self, index: int) -> bool:
        return self._page_stacks[index].visible

    def _build_ui(self):
        self.window.frame.clear()
        # The ground is styled through a type name override, because a flat
        # background_color cascades into every descendant and overrides the
        # scoped Button styles the tab bar depends on.
        self.window.frame.style_type_name_override = "RootFrame"
        self.window.frame.style = {
            "RootFrame": {"background_color": NOVAColor.LAYER_BASE.color}
        }
        with self.window.frame:
            with ui.ScrollingFrame(
                vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                width=ui.Percent(100),
                height=ui.Percent(100),
            ):
                # The tab bar sits inside the scroll container so it shares the
                # right edge of the section cards below; outside it would
                # misalign by the scrollbar gutter.
                with ui.HStack():
                    ui.Spacer(width=SPACING_MD)
                    with ui.VStack(spacing=0):
                        ui.Spacer(height=SPACING_SM)
                        with ui.HStack(height=0):
                            self._tab_bar = SegmentedTabBar(
                                labels=[page.label for page in self._pages],
                                on_changed_fn=lambda index, weak_self=weakref.proxy(self): (
                                    weak_self._show_page(index)
                                ),
                                selected=0,
                            )
                        ui.Spacer(height=SPACING_MD)
                        # The pages are built once and switched by visibility.
                        # Rebuilding on a tab switch would re-issue the forms'
                        # instance fetches and drop the entered values.
                        with ui.VStack():
                            for page in self._pages:
                                stack = ui.VStack(spacing=SECTION_GAP, height=0)
                                with stack:
                                    self.forms.append(page.build_fn())
                                self._page_stacks.append(stack)
                    ui.Spacer(width=SPACING_MD)
        self._show_page(0)

    def _show_page(self, index: int) -> None:
        for page_index, stack in enumerate(self._page_stacks):
            stack.visible = page_index == index

    def _on_visibility_changed(self, visible: bool) -> None:
        omni.kit.menu.utils.refresh_menu_items(WINDOW_MENU_ROOT)
        if not visible:
            return
        # The forms fetch their instance lists once at construction, so a
        # re-fetch on every open picks up instances added since then.
        for form in self.forms:
            form.refresh_instances()


@dataclass
class ToolWindowSubscription:
    tool_window: TabbedToolWindow = None
    menu_subscriptions: list = None

    def __del__(self):
        # Hide the window explicitly, the docking causes issues on deletion.
        if self.tool_window:
            self.tool_window.window.visible = False
        # Dropping the menu items is not enough, they have to be removed.
        if self.menu_subscriptions:
            omni.kit.menu.utils.remove_menu_items(
                self.menu_subscriptions, WINDOW_MENU_ROOT
            )


def register_tool_window(
    tool_window: TabbedToolWindow, name: str, action_name: str
) -> ToolWindowSubscription:
    """Add the window under Tools > Wandelbots NOVA with a toggle action."""

    def toggle_visibility():
        tool_window.window.visible = not tool_window.window.visible

    def _is_visible(
        window_ref: Callable[[], TabbedToolWindow | None] = weakref.ref(tool_window),
    ):
        return window_ref().window.visible if window_ref() else False

    action_unique = f"{EXTENSION_ID}_{name}_{action_name}"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(EXTENSION_ID, action_unique)
    action_registry.register_action(
        EXTENSION_ID,
        action_unique,
        toggle_visibility,
        display_name=name,
        tag="MenuItem",
    )

    return ToolWindowSubscription(
        tool_window,
        omni.kit.menu.utils.add_menu_items(
            [
                omni.kit.menu.utils.MenuItemDescription(
                    name=EXTENSION_WINDOW_MENU_ROOT,
                    sub_menu=[
                        omni.kit.menu.utils.MenuItemDescription(
                            name=name,
                            onclick_action=(EXTENSION_ID, action_unique),
                            ticked_fn=_is_visible,
                        )
                    ],
                )
            ],
            WINDOW_MENU_ROOT,
        ),
    )
