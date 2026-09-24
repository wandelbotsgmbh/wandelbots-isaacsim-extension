from wandelbots.omni.ui.tool.tabbed_tool_window import (
    TabbedToolWindow,
    TabPage,
    ToolWindowSubscription,
    register_tool_window,
)
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection

from .widgets.collision_export_form import CollisionExportForm
from .widgets.collision_load_setup_form import CollisionLoadSetupForm

WINDOW_TITLE = "Collision Setup"


class CollisionSetupWindow(TabbedToolWindow):
    def __init__(self):
        super().__init__(
            WINDOW_TITLE,
            [
                TabPage("Export to NOVA", CollisionExportForm),
                TabPage("Load from NOVA", self._build_load_page),
            ],
        )

    @staticmethod
    def _build_load_page() -> CollisionLoadSetupForm:
        load_section = CollapsibleSection("Load Setup", collapsed=False)
        with load_section.body:
            return CollisionLoadSetupForm()


def register_collision_setup_window() -> ToolWindowSubscription:
    return register_tool_window(
        CollisionSetupWindow(), WINDOW_TITLE, "toggle_collision_setup_window"
    )
