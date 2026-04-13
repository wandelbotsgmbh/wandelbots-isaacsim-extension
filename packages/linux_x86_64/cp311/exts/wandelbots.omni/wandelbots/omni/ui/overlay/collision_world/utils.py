from typing import Literal
from wandelbots.omni.core.collision.utils import CARB_SETTINGS_PREFIX
import carb.settings

CARB_OVERLAY_COLOR = f"{CARB_SETTINGS_PREFIX}/overlay_color"
CARB_OVERLAY_RENDER_MODE = f"{CARB_SETTINGS_PREFIX}/overlay_render_mode"
CARB_OVERLAY_RENDER_LINK_CHAIN = f"{CARB_SETTINGS_PREFIX}/overlay_render_link_chain"

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
    render_mode = settings.get_as_string(CARB_OVERLAY_RENDER_MODE)
    if not render_mode or render_mode == "":
        return "All"
    return render_mode


def set_overlay_render_mode(
    mode: RenderMode, settings: carb.settings.ISettings | None = None
) -> None:
    if settings is None:
        settings = carb.settings.get_settings()
    settings.set_string(CARB_OVERLAY_RENDER_MODE, mode)


def get_overlay_render_link_chain(
    settings: carb.settings.ISettings | None = None,
) -> bool:
    if settings is None:
        settings = carb.settings.get_settings()
    render_link_chain = settings.get_as_bool(CARB_OVERLAY_RENDER_LINK_CHAIN)
    if render_link_chain is None:
        return False
    return render_link_chain


def set_overlay_render_link_chain(
    link_chain: bool, settings: carb.settings.ISettings | None = None
) -> None:
    if settings is None:
        settings = carb.settings.get_settings()
    settings.set_bool(CARB_OVERLAY_RENDER_LINK_CHAIN, link_chain)
