from fastapi import status, Body, HTTPException
import carb.settings

from fastapi import APIRouter

ui_router = APIRouter(prefix="/ui", tags=["UI"])


@ui_router.get(
    path="/visibility",
    operation_id="get_visibility",
    response_model=bool,
    responses={
        200: {"description": "Successfully fetched if viewport is visible"},
        500: {"description": "Unable to fetch if ui is visible"},
    },
)
async def get_visibility() -> bool:
    """
    Fetches if ui is visible and returns a boolean
    """
    try:
        return not carb.settings.get_settings().get_as_bool("/app/window/hideUi")
    except Exception as e:
        raise HTTPException(500, "Unable to fetch if ui is visible") from e


@ui_router.patch(
    path="/visibility",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="set_visibility",
    response_model=None,
    responses={
        204: {"description": "Successfully toggled ui visibility"},
        500: {"description": "Unable to fetch if ui is visible"},
    },
)
async def set_visibility(
    hide: bool = Body(
        False, description="If True, hides UI otherwise makes it visible"
    ),
) -> None:
    """
    Updates the UI visibility state. If `hide=True`, the full UI is hidden and only the viewport is shown.
    """
    try:
        settings = carb.settings.get_settings()
        settings.set("/app/window/hideUi", not hide)
    except Exception as e:
        raise HTTPException(500, f"Unable to fetch if ui is visible :{e}")
