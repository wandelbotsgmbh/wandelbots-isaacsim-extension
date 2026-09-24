"""Registering the Wandelbots NOVA page in Kit's Preferences window."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import carb
import omni.kit.actions.core
import omni.kit.menu.utils
from omni.kit.window.preferences import (
    register_page,
    select_page,
    show_preferences_window,
    unregister_page,
)

from wandelbots.omni.constants import EXTENSION_ID, EXTENSION_WINDOW_MENU_ROOT
from wandelbots.omni.ui.preferences.preferences_page import WandelbotsPreferencesPage

MENU_ROOT = EXTENSION_WINDOW_MENU_ROOT
MENU_ITEM_NAME = "Preferences"


@dataclass
class PreferencesPageSubscription:
    page: Optional[WandelbotsPreferencesPage] = None
    menu_subscriptions: list = None

    def __del__(self):
        if self.page is not None:
            unregister_page(self.page)
            self.page = None
        omni.kit.menu.utils.remove_menu_items(self.menu_subscriptions, MENU_ROOT)


def register_preferences_page() -> PreferencesPageSubscription:
    """Add the page, plus a menu item that opens the window on it.

    Kit's own entry is Edit -> Preferences, which lands on whichever page was
    last shown; the menu item here goes straight to this one.
    """
    page = register_page(WandelbotsPreferencesPage())

    def open_page():
        try:
            show_preferences_window()
            select_page(page)
        except (AttributeError, RuntimeError) as error:
            # The window is built lazily; if it is not up yet there is nothing
            # to select and Kit's own Edit -> Preferences still reaches the page.
            carb.log_warn(f"Could not open the preferences window on our page: {error}")

    action_unique = f"{EXTENSION_ID}_open_preferences_page"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(EXTENSION_ID, action_unique)
    action_registry.register_action(
        EXTENSION_ID,
        action_unique,
        open_page,
        display_name=MENU_ITEM_NAME,
        tag="MenuItem",
    )

    return PreferencesPageSubscription(
        page,
        omni.kit.menu.utils.add_menu_items(
            [
                omni.kit.menu.utils.MenuItemDescription(
                    name=MENU_ITEM_NAME,
                    onclick_action=(EXTENSION_ID, action_unique),
                )
            ],
            MENU_ROOT,
        ),
    )
