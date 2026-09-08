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
from wandelbots.omni.ui.wb_theme import SECTION_GAP, SPACING_MD, SPACING_SM
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.widgets.segmented_tab_bar import SegmentedTabBar

WINDOW_MENU_ROOT = "Tools"

_EXPORT_TAB = 0
_LOAD_TAB = 1


class CollisionSetupWindow:
    def __init__(self):
        self.window = None

        self.window = ui.Window("Collision Setup", width=400, height=300)
        self.window.set_visibility_changed_fn(
            lambda visible, weak_self=weakref.proxy(self): (
                weak_self._on_visibility_changed(visible)
            )
        )
        self.window.visible = False
        self.window.deferred_dock_in("Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE)

        self._collision_export_form: CollisionExportForm | None = None
        self._load_collision_setup_form: CollisionLoadSetupForm | None = None
        self._tab_bar: SegmentedTabBar | None = None
        self._export_page: ui.VStack | None = None
        self._load_page: ui.VStack | None = None

        self._build_ui()

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
                                labels=["Export to NOVA", "Load from NOVA"],
                                on_changed_fn=lambda index, weak_self=weakref.proxy(self): (
                                    weak_self._on_tab_changed(index)
                                ),
                                selected=_EXPORT_TAB,
                            )
                        ui.Spacer(height=SPACING_MD)
                        # Both pages are built once and switched by visibility.
                        # Rebuilding on a tab switch would re-issue the forms'
                        # instance fetches and drop the entered values.
                        with ui.VStack():
                            self._export_page = ui.VStack(spacing=SECTION_GAP, height=0)
                            with self._export_page:
                                self._collision_export_form = CollisionExportForm()
                            self._load_page = ui.VStack(spacing=SECTION_GAP, height=0)
                            with self._load_page:
                                load_section = CollapsibleSection(
                                    "Load Setup", collapsed=False
                                )
                                with load_section.body:
                                    self._load_collision_setup_form = (
                                        CollisionLoadSetupForm()
                                    )
                    ui.Spacer(width=SPACING_MD)
        self._load_page.visible = False

    def _on_visibility_changed(self, visible: bool) -> None:
        omni.kit.menu.utils.refresh_menu_items(WINDOW_MENU_ROOT)
        if not visible:
            return
        # The forms fetch their instance lists once at construction, so a
        # re-fetch on every open picks up instances added since then.
        if self._collision_export_form is not None:
            self._collision_export_form.refresh_instances()
        if self._load_collision_setup_form is not None:
            self._load_collision_setup_form.refresh_instances()

    def _on_tab_changed(self, index: int) -> None:
        self._export_page.visible = index == _EXPORT_TAB
        self._load_page.visible = index == _LOAD_TAB


@dataclass
class CollisionSetupWindowSubscription:
    collision_export_window: CollisionSetupWindow = None
    menu_subscriptions: list = None

    def __del__(self):
        # Hide the window explicitly, the docking causes issues on deletion.
        if self.collision_export_window:
            self.collision_export_window.window.visible = False

        # Dropping the menu items is not enough, they have to be removed.
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
