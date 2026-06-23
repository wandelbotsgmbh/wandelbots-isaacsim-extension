from dataclasses import dataclass
import omni.ui as ui
from isaacsim.gui.components.style import COLOR_X, COLOR_Y, COLOR_Z, COLOR_W
from omni.kit.window.property.templates import LABEL_HEIGHT


@dataclass
class CoordinateInputFieldModel:
    model: ui.SimpleFloatModel
    label: str
    tooltip: str
    min: float = float("-inf")
    max: float = float("inf")
    step: float = 0.1


class CoordinatesInput:
    colors = [COLOR_X, COLOR_Y, COLOR_Z, COLOR_W]

    def __init__(
        self,
        fields: list[CoordinateInputFieldModel],
        height=LABEL_HEIGHT + 6,
        readonly: bool = False,
    ):
        self._fields = fields
        self._height = height
        self._readonly = readonly
        self._build_ui()

    def _build_ui(self) -> None:
        RECT_WIDTH = 13

        with ui.HStack(height=self._height, spacing=4):
            for field_index, field in enumerate(self._fields):
                with ui.HStack(spacing=0, height=LABEL_HEIGHT):
                    with ui.ZStack(width=RECT_WIDTH):
                        ui.Rectangle(
                            name="vector_label",
                            style={
                                "background_color": CoordinatesInput.colors[
                                    field_index % len(CoordinatesInput.colors)
                                ],
                                "border_radius": 3,
                                "corner_flag": ui.CornerFlag.LEFT,
                            },
                        )
                        ui.Label(
                            field.label,
                            name="vector_label",
                            alignment=ui.Alignment.CENTER,
                        )

                    ui.FloatDrag(
                        model=field.model,
                        name=f"Field_{field.label}",
                        height=LABEL_HEIGHT,
                        min=field.min,
                        max=field.max,
                        step=field.step,
                        alignment=ui.Alignment.LEFT_CENTER,
                        tooltip=field.tooltip,
                        enabled=not self._readonly,
                        style={"border_radius": 0},
                    )
