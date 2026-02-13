from dataclasses import dataclass
from typing import Callable
import omni
import weakref
import omni.ui as ui
import omni.kit.menu.utils
import omni.kit.actions.core
from wandelbots.omni.constants import EXTENSION_ID, EXTENSION_WINDOW_MENU_ROOT
from .widgets.collision_export_form import CollisionExportForm
from .widgets.collision_load_setup_form import CollisionLoadSetupForm
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.utils import get_icon

WINDOW_MENU_ROOT = "Tools"


class SphereRadiusModel(ui.SimpleFloatModel):
    def min(self):
        return 0


class CollisionSetupWindow:
    def __init__(self):
        self.window = None

        self.window = ui.Window("Collision Setup", width=400, height=300)
        self.window.set_visibility_changed_fn(
            lambda _: omni.kit.menu.utils.refresh_menu_items(WINDOW_MENU_ROOT)
        )
        self.window.visible = False
        self.window.deferred_dock_in("Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE)

        self._collision_export_form: CollisionExportForm | None = None
        self._load_collision_setup_form: CollisionLoadSetupForm | None = None

        self._build_ui()

    def _build_ui(self):
        self.window.frame.clear()
        with self.window.frame:
            with ui.ScrollingFrame(
                vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                width=ui.Percent(100),
                height=ui.Percent(100),
            ):
                with ui.HStack():
                    ui.Spacer(width=8)
                    with ui.VStack(spacing=4):
                        with ui.HStack(height=30, spacing=12):
                            ui.Label(
                                "Load from NOVA",
                                height=30,
                                width=0,
                                style={
                                    "font_size": 18,
                                },
                            )
                            with ui.HStack():
                                ui.Line(
                                    style={"color": 0x338A8777}, width=ui.Fraction(1)
                                )

                                def refresh_callback(weak_self=weakref.ref(self)):
                                    self = weak_self()
                                    if (
                                        self is None
                                        or self._load_collision_setup_form is None
                                    ):
                                        return
                                    self._load_collision_setup_form.refresh()

                                ui.Button(
                                    image_url=get_icon("refresh.svg"),
                                    width=28,
                                    height=28,
                                    style={
                                        "color": NOVAColor.ACTION_ACTIVE.color,
                                    },
                                    tooltip="Click to refresh instance data",
                                    clicked_fn=refresh_callback,
                                )
                        self._load_collision_setup_form = CollisionLoadSetupForm()

                        with ui.HStack(height=30, spacing=12):
                            ui.Label(
                                "Export to NOVA",
                                height=30,
                                width=0,
                                style={
                                    "font_size": 18,
                                },
                            )
                            ui.Line(
                                style={
                                    "border_width": 1,
                                    "color": NOVAColor.DIVIDER.color,
                                },
                                width=ui.Fraction(1),
                            )

                        self._collision_export_form = CollisionExportForm()


@dataclass
class CollisionSetupWindowSubscription:
    collision_export_window: CollisionSetupWindow = None
    menu_subscriptions: list = None

    def __del__(self):
        # Need to explicitly hide the collision_export_window because the docking causes issues on deletion
        if self.collision_export_window:
            self.collision_export_window.window.visible = False

        # Dropping the menu items is not enough we need to explicitly remove them
        omni.kit.menu.utils.remove_menu_items(self.menu_subscriptions, WINDOW_MENU_ROOT)


def register_collision_setup_window():
    collision_setup_window = CollisionSetupWindow()

    def toggle_visibility():
        collision_setup_window.window.visible = (
            not collision_setup_window.window.visible
        )

    def _is_visible(
        toolbar: Callable[[], CollisionSetupWindow | None] = weakref.ref(
            collision_setup_window
        ),
    ):
        return toolbar().window.visible if toolbar() else False

    ext_id = EXTENSION_ID
    name = "Collision Setup"
    action_name = "toggle_collision_setup_window"
    action_unique = f"{ext_id}_{name}_{action_name}"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(ext_id, action_unique)
    action_registry.register_action(
        ext_id, action_unique, toggle_visibility, display_name=name, tag="MenuItem"
    )

    return CollisionSetupWindowSubscription(
        collision_setup_window,
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
