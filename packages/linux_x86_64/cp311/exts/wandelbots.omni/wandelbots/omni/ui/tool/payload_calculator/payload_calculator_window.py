from wandelbots.omni.ui.tool.payload_calculator.widgets.payload_export_form import (
    PayloadExportForm,
)
from wandelbots.omni.ui.tool.payload_calculator.widgets.payload_load_form import (
    PayloadLoadForm,
)
from wandelbots.omni.ui.tool.tabbed_tool_window import (
    TabbedToolWindow,
    TabPage,
    ToolWindowSubscription,
    register_tool_window,
)
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection

WINDOW_TITLE = "Payload Calculator"

EXPORT_TAB = 0
LOAD_TAB = 1


class PayloadCalculatorWindow(TabbedToolWindow):
    def __init__(self, title: str = WINDOW_TITLE):
        super().__init__(
            title,
            [
                TabPage("Export to NOVA", PayloadExportForm),
                TabPage("Load from NOVA", self._build_load_page),
            ],
        )

    @property
    def export_form(self) -> PayloadExportForm:
        return self.forms[EXPORT_TAB]

    @property
    def load_form(self) -> PayloadLoadForm:
        return self.forms[LOAD_TAB]

    @staticmethod
    def _build_load_page() -> PayloadLoadForm:
        load_section = CollapsibleSection("Load Payload", collapsed=False)
        with load_section.body:
            return PayloadLoadForm()


def register_payload_calculator_window() -> ToolWindowSubscription:
    return register_tool_window(
        PayloadCalculatorWindow(), WINDOW_TITLE, "toggle_payload_calculator_window"
    )
