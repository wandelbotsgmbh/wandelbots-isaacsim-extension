from wandelbots.omni.core.collision.utils import CARB_SETTINGS_PREFIX
import carb.settings

CARB_OVERLAY_COLOR = f"{CARB_SETTINGS_PREFIX}/overlay_color"


def get_overlay_color(settings: carb.settings.ISettings | None = None) -> str:
    if settings is None:
        settings = carb.settings.get_settings()
    setting_color = settings.get_as_string(CARB_OVERLAY_COLOR)
    if not setting_color or setting_color == "":
        return "#A936DA16"
    return setting_color


def set_overlay_color(
    color: str, settings: carb.settings.ISettings | None = None
) -> None:
    if settings is None:
        settings = carb.settings.get_settings()
    settings.set_string(CARB_OVERLAY_COLOR, color)
