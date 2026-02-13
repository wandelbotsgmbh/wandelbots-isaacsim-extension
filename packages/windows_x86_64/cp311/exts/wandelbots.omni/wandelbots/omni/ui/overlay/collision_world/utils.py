from typing import Literal
from wandelbots.omni.core.collision.utils import CARB_SETTINGS_PREFIX
import carb.settings

CARB_OVERLAY_COLOR = f"{CARB_SETTINGS_PREFIX}/overlay_color"
CARB_OVERLAY_RENDER_MODE = f"{CARB_SETTINGS_PREFIX}/overlay_render_mode"

RenderMode = Literal["None", "Selected", "All"]


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


def get_overlay_render_mode(
    settings: carb.settings.ISettings | None = None,
) -> RenderMode:
    if settings is None:
        settings = carb.settings.get_settings()
    render_mode = settings.get_as_string(f"{CARB_SETTINGS_PREFIX}/overlay_render_mode")
    if not render_mode or render_mode == "":
        return "All"
    return render_mode


def set_overlay_render_mode(
    mode: RenderMode, settings: carb.settings.ISettings | None = None
) -> None:
    if settings is None:
        settings = carb.settings.get_settings()
    settings.set_string(f"{CARB_SETTINGS_PREFIX}/overlay_render_mode", mode)
