from fastapi import status, Body
import carb.settings

from fastapi import APIRouter

ui_router = APIRouter(prefix="/ui", tags=["ui"])


@ui_router.get(path="/visibility", operation_id="is_ui_visible", response_model=bool)
async def is_ui_visible() -> bool:
    """
    Test if ui is visible
    Returns:
        True if ui is visible
    """
    return not carb.settings.get_settings().get_as_bool("/app/window/hideUi")


@ui_router.put(
    path="/visibility",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="toggle_ui",
    response_model=None,
)
async def toggle_ui(visibility: bool = Body(...)) -> None:
    """
    Toggles between full screen mode of viewport and the entire UI view
    Args:
        visibility: a bool variable which tells if only viewport should be the visible

    Returns:
        None
    """
    settings = carb.settings.get_settings()
    settings.set("/app/window/hideUi", not visibility)
