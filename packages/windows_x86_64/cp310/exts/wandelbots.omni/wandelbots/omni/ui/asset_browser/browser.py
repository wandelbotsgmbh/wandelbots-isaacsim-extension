"""
Wandelbots Asset Browser Manager
"""

import weakref
import omni.ui as ui
import omni.kit.menu.utils
from .window import WandelbotsAssetBrowserWindow

VISIBLE_ON_STARTUP = True
BROWSER_MENU_ROOT = "Window"


class WandelbotsAssetBrowserManager:
    """Simple asset browser manager"""

    def __init__(self):
        self._window = None
        self._menu_entry = None

        ui.Workspace.set_show_window_fn(
            "Wandelbots NOVA Assets Browser",
            lambda visible, ws=weakref.ref(self): ws() and ws()._show_window(visible),
        )
        if VISIBLE_ON_STARTUP:
            self._show_window(True)
        self._menu_item = self._register_menu_item()

    def __del__(self):
        """Cleanup the asset browser"""
        self._menu = None
        if self._window:
            self._window.destroy()
            self._window = None

    def _show_window(self, visible: bool):
        """Show or hide the window"""
        if visible:
            if self._window is None:
                self._window = WandelbotsAssetBrowserWindow()
                self._window.set_visibility_changed_fn(
                    lambda vis, ws=weakref.ref(self): ws()
                    and ws()._on_window_visibility_changed(vis)
                )
            else:
                self._window.visible = True
            self._window.focus()
        else:
            if self._window:
                self._window.visible = False

    def _on_window_visibility_changed(self, visible):
        """Handle window visibility changes (including close button)"""
        omni.kit.menu.utils.refresh_menu_items(BROWSER_MENU_ROOT)

    def _is_window_visible(self) -> bool:
        return self._window.visible if self._window else False

    def _toggle_window(self):
        self._show_window(not self._is_window_visible())

    def _register_menu_item(self):
        self._menu_entry = [
            omni.kit.menu.utils.MenuItemDescription(
                name="Browsers",
                sub_menu=[
                    omni.kit.menu.utils.MenuItemDescription(
                        name="Wandelbots NOVA Assets Browser",
                        ticked=VISIBLE_ON_STARTUP,
                        ticked_fn=lambda ws=weakref.ref(self): ws()
                        and ws()._is_window_visible(),
                        onclick_fn=lambda ws=weakref.ref(self): ws()
                        and ws()._toggle_window(),
                    )
                ],
            )
        ]
        omni.kit.menu.utils.add_menu_items(self._menu_entry, BROWSER_MENU_ROOT)
