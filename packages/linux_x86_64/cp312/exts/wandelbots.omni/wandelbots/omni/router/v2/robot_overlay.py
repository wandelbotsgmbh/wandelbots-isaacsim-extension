from fastapi import APIRouter, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field

from wandelbots.omni.ui.overlay import (
    get_overlay_registry,
    ROBOT_OVERLAY_NAME,
    RobotOverlay,
)

robot_overlay_router = APIRouter(
    prefix="/overlays/robot", tags=["Robot Overlay (Experimental)"]
)


class RobotOverlayVisibility(BaseModel):
    motion_group_prim_path: str = Field(
        ...,
        description="USD prim path of the motion group.",
        example="/World/Robot/motion_group_0",
    )
    visible: bool = Field(
        ...,
        description="Whether to show or hide the robot overlay.",
    )
    joint_positions: list[float] | None = Field(
        default=None,
        description="Joint positions in radians. Required when visible is true.",
    )


@robot_overlay_router.put(
    path="/visibility",
    operation_id="set_robot_overlay_visibility",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Overlay not ready"},
        status.HTTP_400_BAD_REQUEST: {
            "description": "joint_positions required when visible is true"
        },
    },
)
async def set_robot_overlay_visibility(
    robot_visibility_data: RobotOverlayVisibility,
) -> None:
    """Show or hide the robot ghost for the given motion group."""
    overlay = get_overlay_registry().get_overlay(ROBOT_OVERLAY_NAME)
    if not isinstance(overlay, RobotOverlay):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Robot overlay not available",
        )
    if robot_visibility_data.visible and robot_visibility_data.joint_positions is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="joint_positions is required when visible is true",
        )

    if robot_visibility_data.visible:
        await overlay.show(
            robot_visibility_data.motion_group_prim_path,
            robot_visibility_data.joint_positions,
        )
    else:
        overlay.hide(robot_visibility_data.motion_group_prim_path)
