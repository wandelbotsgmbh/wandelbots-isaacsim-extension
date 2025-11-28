from pydantic import BaseModel, Field, conlist, field_validator
from typing import Annotated, Optional, Literal, Union

# Type aliases for better readability and reusability
ColorRGB = tuple[int, int, int]
ColorInput = Union[ColorRGB, list[ColorRGB]]
WidthInput = Union[float, list[float]]


class TrajectoryOptions(BaseModel):
    color: ColorInput = Field(
        default=[(206, 0, 88), (144, 59, 128), (83, 118, 167)],
        description="Color of the trajectory. Either a single RGB tuple for uniform color, or a list of RGB tuples for per-segment colors",
        example=[[206, 0, 88], [144, 59, 128], [83, 118, 167]],
    )
    width: WidthInput = Field(
        default=[10.0, 30.0, 10.0],
        description="Width of the trajectory in mm. Either a single value for uniform width, or a list of values for per-segment widths",
        example=[10.0, 30.0, 10.0],
    )

    @field_validator("color")
    def validate_color(cls, color_input: ColorInput) -> ColorInput:
        # Early return for invalid types - reduces nesting
        if not isinstance(color_input, (tuple, list)):
            raise ValueError(
                f"Color must be either a single RGB tuple or a list of RGB tuples, got {type(color_input).__name__}"
            )

        # Handle single RGB color case
        if isinstance(color_input, tuple):
            if len(color_input) != 3:
                raise ValueError(
                    f"Single color must be an RGB tuple with 3 values, got {len(color_input)}: {color_input}"
                )
            for i, val in enumerate(color_input):
                if not isinstance(val, int) or not (0 <= val <= 255):
                    raise ValueError(
                        f"RGB value at position {i} must be integer 0-255, got {type(val).__name__}({val})"
                    )
            return color_input

        # Handle list of colors case (we know it's a list due to early return above)
        if len(color_input) == 0:
            raise ValueError("Color list cannot be empty")

        for color_idx, color in enumerate(color_input):
            if not isinstance(color, tuple) or len(color) != 3:
                raise ValueError(
                    f"Color at index {color_idx} must be an RGB tuple with 3 values, got {type(color).__name__}: {color}"
                )
            for val_idx, val in enumerate(color):
                if not isinstance(val, int) or not (0 <= val <= 255):
                    raise ValueError(
                        f"Color at index {color_idx}, position {val_idx} must be integer 0-255, got {type(val).__name__}({val})"
                    )

        return color_input

    @field_validator("width")
    def validate_width(cls, width_input: WidthInput) -> WidthInput:
        # Early return for invalid types
        if not isinstance(width_input, (int, float, list)):
            raise ValueError(
                f"Width must be either a number or a list of numbers, got {type(width_input).__name__}"
            )

        # Handle single width value
        if isinstance(width_input, (int, float)):
            if width_input <= 0:
                raise ValueError(f"Width must be positive, got {width_input}")
            return width_input

        # Handle list of widths (we know it's a list due to early return above)
        if len(width_input) == 0:
            raise ValueError("Width list cannot be empty")

        for i, width in enumerate(width_input):
            if not isinstance(width, (int, float)) or width <= 0:
                raise ValueError(
                    f"Width at index {i} must be positive number, got {type(width).__name__}({width})"
                )

        return width_input


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
    name: str = Field(
        "triangle_example", description="Name of the trajectory to create"
    )
    parent_prim_path: str = Field(
        "/World",
        description="Parent prim path where the trajectories have to be created",
    )
    poses: list[Annotated[list[float], conlist(float, min_length=6, max_length=6)]] = (
        Field(
            default=[
                # Triangle example showing per-segment colors and widths
                [-112, -400, 523, 0, 0, 0],  # Start point (magenta)
                [500, 212, 236, 0, 0, 0],  # Corner (purple, thick)
                [-276, 586, 530, 0, 0, 0],  # Corner (blue)
                [-112, -400, 523, 0, 0, 0],  # Close triangle (cyan)
            ],
            description="List of cartesian poses to traverse (example: colored triangle)",
        )
    )
    options: TrajectoryOptions = Field(
        default_factory=lambda: TrajectoryOptions(
            color=[(206, 0, 88), (144, 59, 128), (83, 118, 167)],
            width=[10.0, 30.0, 10.0],
        ),
        description="Additional options for the trajectory",
        example={
            "color": [[206, 0, 88], [144, 59, 128], [83, 118, 167]],
            "width": [10.0, 30.0, 10.0],
        },
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
                # Triangle marker points
                [-112, -400, 523, 0, 0, 0],  # Start point
                [500, 212, 236, 0, 0, 0],  # Corner 1
                [-276, 586, 530, 0, 0, 0],  # Corner 2
            ],
            description="List of cartesian poses to places markers at (example: triangle corners)",
        )
    )
