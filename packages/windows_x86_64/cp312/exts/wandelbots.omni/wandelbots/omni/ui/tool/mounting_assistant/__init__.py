"""Mounting Assistant tool package."""

from __future__ import annotations

import weakref
from dataclasses import dataclass
from typing import Callable

import omni.kit.actions.core
import omni.kit.menu.utils

from wandelbots.omni.constants import EXTENSION_ID, EXTENSION_WINDOW_MENU_ROOT
from wandelbots.omni.ui.tool.mounting_assistant.mounting_assistant_window import (
    MountingAssistantWindow,
)

_WINDOW_MENU_ROOT = "Tools"


@dataclass
class MountingAssistantWindowSubscription:
    mounting_assistant_window: MountingAssistantWindow = None
    menu_subscriptions: list = None

    def __del__(self):
        if self.mounting_assistant_window:
            self.mounting_assistant_window.destroy()
            self.mounting_assistant_window = None
        if self.menu_subscriptions:
            omni.kit.menu.utils.remove_menu_items(
                self.menu_subscriptions, _WINDOW_MENU_ROOT
            )


def register_mounting_assistant_window() -> MountingAssistantWindowSubscription:
    window = MountingAssistantWindow()

    def toggle_visibility():
        window.window.visible = not window.window.visible

    def _is_visible(
        window_ref: Callable[[], MountingAssistantWindow | None] = weakref.ref(window),
    ):
        return window_ref().window.visible if window_ref() else False

    name = "Mounting Assistant"
    action_unique = f"{EXTENSION_ID}_Mounting Assistant_toggle"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(EXTENSION_ID, action_unique)
    action_registry.register_action(
        EXTENSION_ID,
        action_unique,
        toggle_visibility,
        display_name=name,
        tag="MenuItem",
    )

    return MountingAssistantWindowSubscription(
        window,
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
            _WINDOW_MENU_ROOT,
        ),
    )


__all__ = ["register_mounting_assistant_window"]
