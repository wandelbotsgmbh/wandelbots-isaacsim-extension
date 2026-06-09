import omni.ui as ui
from enum import Enum

ColorRGBA = list[float]


def hex_to_float_array(hex_string: str) -> list[float]:
    hex_string = hex_string.lstrip("#")
    if len(hex_string) == 6:
        hex_string += "FF"
    r = int(hex_string[0:2], 16) / 255.0
    g = int(hex_string[2:4], 16) / 255.0
    b = int(hex_string[4:6], 16) / 255.0
    a = int(hex_string[6:8], 16) / 255.0
    return [r, g, b, a]


def float_array_to_hex(rgba: list[float]) -> str:
    if len(rgba) == 3:
        rgba = rgba + [1.0]
    r = int(rgba[0] * 255)
    g = int(rgba[1] * 255)
    b = int(rgba[2] * 255)
    a = int(rgba[3] * 255)
    return f"#{r:02X}{g:02X}{b:02X}{a:02X}"


class NOVAColor(Enum):
    """Color palette namespace for NOVA UI components."""

    # Text Colors
    TEXT_PRIMARY_CONTRAST = "#FFFFFF"
    TEXT_PRIMARY = "#FFFFFFcc"
    TEXT_SECONDARY = "#FFFFFFB3"
    TEXT_DISABLED = "#FFFFFF61"

    # Primary Colors
    PRIMARY_MAIN = "#8E56FC"
    PRIMARY_DARK = "#883AFF"
    PRIMARY_LIGHT = "#9D83F6"
    PRIMARY_CONTRAST_TEXT = "#FFFFFFDE"

    # Primary Extended (Interaction States)
    PRIMARY_HOVER = "#8E56FC14"
    PRIMARY_SELECTED = "#8E56FC29"
    PRIMARY_FOCUS = "#8E56FC1F"
    PRIMARY_FOCUS_VISIBLE = "#8E56FC4D"
    PRIMARY_OUTLINE_BORDER = "#8E56FC80"

    # Secondary Colors
    SECONDARY_MAIN = "#FFFFFF"
    SECONDARY_DARK = "#FFFFFF0F"
    SECONDARY_CONTRAST_TEXT = "#FFFFFFDE"
    SECONDARY_TONAL = "#FFFFFF1A"

    # Tertiary Colors
    TERTIARY_MAIN = "#64FFDA"
    TERTIARY_DARK = "#26A69A"
    TERTIARY_LIGHT = "#A7FFEB"
    TERTIARY_CONTRAST_TEXT = "#000000"

    # Button Colors
    BUTTON_STOP = "#EF5350"
    BUTTON_HOVER = "#3A3A3A"

    # Delete
    DELETE_MAIN = "#EF5350"

    # Error Colors
    ERROR_MAIN = "#EF5350"
    ERROR_DARK = "#E53935"
    ERROR_LIGHT = "#EF9A9A"
    ERROR_CONTRAST_TEXT = "#FFFFFF"

    # Warning Colors
    WARNING_MAIN = "#FFAB40"
    WARNING_DARK = "#FF9100"
    WARNING_LIGHT = "#FFD180"
    WARNING_CONTRAST_TEXT = "#000000DE"

    # Success Colors
    SUCCESS_MAIN = "#26A69A"
    SUCCESS_DARK = "#00796B"
    SUCCESS_LIGHT = "#80CBC4"
    SUCCESS_CONTRAST_TEXT = "#FFFFFFDE"

    COLLAPSIBLE_SECTION_HEADER = "#2A2A2A"
    COLLAPSIBLE_SECTION_HEADER_ICON = "#FFFFFFB6"
    COLLAPSIBLE_SECTION_HEADER_HOVER = "#272727"
    COLLAPSIBLE_SECTION_BODY = "#393939"

    # TreeView Colors
    TREEVIEW_BACKGROUND = "#23221F"
    TREEVIEW_SELECTED = "#3D3D33"
    TREEVIEW_HOVERED = "#2E2E28"

    # Tooltip Colors
    TOOLTIP_BACKGROUND = "#2A2A2A"
    TOOLTIP_TEXT = "#FFFFFFDE"
    TOOLTIP_BORDER = "#4A4A4A"

    # Progress Bar Colors
    PROGRESS_BAR_BACKGROUND = "#1A1A1A"

    # Background Colors
    BACKGROUND_PAPER = "#343434"
    BACKGROUND_DEFAULT = "#505050"
    BACKGROUND_PAPER_DARK = "#2C2C2C"

    # Background Paper Elevation Levels
    BACKGROUND_ELEVATION_0 = "#11131F"
    BACKGROUND_ELEVATION_1 = "#141623"
    BACKGROUND_ELEVATION_2 = "#171927"
    BACKGROUND_ELEVATION_3 = "#1A1C2B"
    BACKGROUND_ELEVATION_4 = "#1D1F2F"
    BACKGROUND_ELEVATION_5 = "#202233"
    BACKGROUND_ELEVATION_6 = "#232537"
    BACKGROUND_ELEVATION_7 = "#26283B"
    BACKGROUND_ELEVATION_8 = "#292B3F"
    BACKGROUND_ELEVATION_9 = "#2C2E43"
    BACKGROUND_ELEVATION_10 = "#2F3147"
    BACKGROUND_ELEVATION_11 = "#32344B"
    BACKGROUND_ELEVATION_12 = "#35374F"
    BACKGROUND_ELEVATION_13 = "#383A53"
    BACKGROUND_ELEVATION_14 = "#3B3D57"
    BACKGROUND_ELEVATION_15 = "#3E405B"
    BACKGROUND_ELEVATION_16 = "#393F57"

    # Action Colors
    ACTION_ACTIVE = "#FFFFFF"
    ACTION_HOVER = "#FFFFFF14"
    ACTION_SELECTED = "#FFFFFF29"
    ACTION_DISABLED_BACKGROUND = "#FFFFFF1F"
    ACTION_FOCUS = "#FFFFFF1F"
    ACTION_DISABLED = "#FFFFFF61"

    # Common Colors
    COMMON_WHITE = "#FFFFFF"
    COMMON_BLACK = "#000000"

    # Divider
    DIVIDER = "#FFFFFF1F"

    # ============================================================================
    # Jogging Panel Colors
    # ============================================================================

    # X Axis (Red)
    X_AXIS_BACKGROUND = "#D74238"
    X_AXIS_BORDER = "#D74238"
    X_AXIS_BUTTON_DEFAULT = "#F14D42"
    X_AXIS_BUTTON_PRESSED = "#8A2923"
    X_AXIS_BUTTON_HOVERED = "#F14D42"
    X_AXIS_BUTTON_DISABLED = "#F14D42"
    X_AXIS_COLOR = "#FFC6C6"

    # Y Axis (Green)
    Y_AXIS_BACKGROUND = "#14976C"
    Y_AXIS_BORDER = "#14976C"
    Y_AXIS_BUTTON_DEFAULT = "#1CBC87"
    Y_AXIS_BUTTON_PRESSED = "#0B593F"
    Y_AXIS_BUTTON_DISABLED = "#1CBC87"
    Y_AXIS_BUTTON_HOVERED = "#1CBC87"
    Y_AXIS_COLOR = "#D7FFF2"

    # Z Axis (Blue)
    Z_AXIS_BACKGROUND = "#01579B"
    Z_AXIS_BORDER = "#01579B"
    Z_AXIS_BUTTON_DEFAULT = "#0288D1"
    Z_AXIS_BUTTON_PRESSED = "#024072"
    Z_AXIS_BUTTON_DISABLED = "#0288D1"
    Z_AXIS_BUTTON_HOVERED = "#0288D1"
    Z_AXIS_COLOR = "#D2EFFF"

    @property
    def color(self):
        """Get the ui.color object for this color."""
        return ui.color(self.value)
