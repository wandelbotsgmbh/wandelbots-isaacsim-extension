from pydantic import BaseModel, Field, conlist, conint
from typing import Annotated, Optional, Literal, Union


class TrajectoryOptions(BaseModel):
    color: Annotated[
        tuple[conint(ge=0, le=255), ...],
        Field(
            (255, 255, 255),
            description="Color of the trajectory in RGB format",
            min_items=3,
            max_items=3,
        ),
    ]
    width: Annotated[float, Field(20, description="Width of the trajectory in mm")]


class TrajectoryObject(BaseModel):
    name: str = Field(..., description="Name of the trajectory")
    path: str = Field(..., description="Prim path of the trajectory")
    poses: Annotated[
        list[Annotated[list[float], conlist(float, min_length=6, max_length=6)]],
        Field(..., description="List of cartesian poses to traverse"),
    ]
    options: TrajectoryOptions = Field(
        ..., description="Additional options for the trajectory"
    )


class TrajectoryData(BaseModel):
    name: str = Field("trajectory", description="Name of the trajectory to create")
    parent_prim_path: str = Field(
        "/World",
        description="Parent prim path where the trajectories have to be created",
    )
    poses: list[Annotated[list[float], conlist(float, min_length=6, max_length=6)]] = (
        Field(
            default=[
                [500.0, 0.0, 500.0, 0.0, 0.0, 0.0],
                [525.0, 0.0, 500.0, 0.0, 0.0, 0.0],
                [550.0, 0.0, 500.0, 0.0, 0.0, 0.0],
                [575.0, 0.0, 500.0, 0.0, 0.0, 0.0],
                [600.0, 0.0, 500.0, 0.0, 0.0, 0.0],
                [600.0, 25.0, 500.0, 0.0, 0.0, 0.0],
                [600.0, 50.0, 500.0, 0.0, 0.0, 0.0],
                [600.0, 75.0, 500.0, 0.0, 0.0, 0.0],
                [600.0, 100.0, 500.0, 0.0, 0.0, 0.0],
                [575.0, 100.0, 500.0, 0.0, 0.0, 0.0],
                [550.0, 100.0, 500.0, 0.0, 0.0, 0.0],
                [525.0, 100.0, 500.0, 0.0, 0.0, 0.0],
                [500.0, 100.0, 500.0, 0.0, 0.0, 0.0],
            ],
            description="List of cartesian poses to traverse",
        )
    )
    options: TrajectoryOptions = Field(
        ..., description="Additional options for the trajectory"
    )


class PatchTrajectoryData(BaseModel):
    poses: Optional[
        list[Annotated[list[float], conlist(float, min_length=6, max_length=6)]]
    ] = Field(default=None, description="List of cartesian poses to traverse")
    options: Optional[TrajectoryOptions] = Field(
        default=None, description="Additional options for the trajectory"
    )


class GizmoPrim(BaseModel):
    type: Literal["gizmo"] = Field(..., description="Use gizmo marker")


class CustomPrim(BaseModel):
    type: Literal["custom"] = Field(..., description="Use a custom USD prim as marker")
    custom_prim_path: str = Field(..., description="Path to the custom USD prim")


PrimUnion = Annotated[Union[GizmoPrim, CustomPrim], Field(discriminator="type")]


class TrajectoryMarker(BaseModel):
    prim: PrimUnion
    poses: list[Annotated[list[float], conlist(float, min_length=6, max_length=6)]] = (
        Field(
            default=[
                [500.0, 0.0, 500.0, 0.0, 0.0, 0.0],
                [600.0, 0.0, 500.0, 0.0, 0.0, 0.0],
                [600.0, 100.0, 500.0, 0.0, 0.0, 0.0],
                [500.0, 100.0, 500.0, 0.0, 0.0, 0.0],
            ],
            description="List of cartesian poses to places markers at",
        )
    )
