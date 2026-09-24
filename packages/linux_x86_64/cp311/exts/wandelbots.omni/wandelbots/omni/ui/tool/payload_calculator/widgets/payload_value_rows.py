"""Read-only rows that show a payload's mass, centre of mass and inertia."""

from __future__ import annotations

from typing import Sequence

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.widgets import CoordinateInputFieldModel, CoordinatesInput
from wandelbots.omni.ui.widgets.form_row import form_row
from wandelbots.omni.ui.wb_theme import FIELD_STYLE, TOOLTIP_RESET, build_tooltip

_AXIS_LABELS = ("X", "Y", "Z")
_MASS_PRECISION = 3
_MILLIMETER_PRECISION = 2
# Inertia of a hand-held part is a few 1e-4 kg m2; three digits show 0.000.
_INERTIA_PRECISION = 6

_MASS_TOOLTIP = "Mass of the payload in kg."
_CENTER_OF_MASS_TOOLTIP = "Centre of mass in mm, in the reference frame."
_MOMENTS_TOOLTIP = (
    "Moments of inertia Ixx, Iyy, Izz about the centre of mass along the "
    "reference frame axes, in kg m2. These three values are what the "
    "payload stores."
)
_PRODUCTS_TOOLTIP = (
    "Products of inertia Ixy, Iyz, Ixz about the centre of mass, in kg m2. "
    "Shown for reference only; the planner's payload has no field for them."
)


def _build_vector_row(
    label: str, values: Sequence[float] | None, tooltip: str, precision: int
) -> None:
    with form_row(label, tooltip=tooltip):
        if values is None:
            ui.Label("not set", style={"color": NOVAColor.TEXT_SECONDARY.color})
            return
        CoordinatesInput(
            [
                CoordinateInputFieldModel(
                    model=ui.SimpleFloatModel(float(value)),
                    label=axis,
                    tooltip=tooltip,
                    precision=precision,
                )
                for value, axis in zip(values, _AXIS_LABELS)
            ],
            readonly=True,
        )


def build_payload_value_rows(
    mass: float,
    center_of_mass: Sequence[float] | None,
    moment_of_inertia: Sequence[float] | None,
    products_of_inertia: Sequence[float] | None = None,
) -> None:
    with form_row("Mass [kg]", tooltip=_MASS_TOOLTIP):
        ui.FloatDrag(
            model=ui.SimpleFloatModel(float(mass)),
            enabled=False,
            height=20,
            precision=_MASS_PRECISION,
            style={**FIELD_STYLE, **TOOLTIP_RESET},
            tooltip_fn=lambda: build_tooltip(_MASS_TOOLTIP),
        )
    _build_vector_row(
        "Center of mass [mm]",
        center_of_mass,
        _CENTER_OF_MASS_TOOLTIP,
        _MILLIMETER_PRECISION,
    )
    _build_vector_row(
        "Moments of inertia [kg m2]",
        moment_of_inertia,
        _MOMENTS_TOOLTIP,
        _INERTIA_PRECISION,
    )
    if products_of_inertia is None:
        return
    with form_row("Products of inertia [kg m2]", tooltip=_PRODUCTS_TOOLTIP):
        ui.Label(
            "  ".join(
                f"{name} {value:.3e}"
                for name, value in zip(("Ixy", "Iyz", "Ixz"), products_of_inertia)
            ),
            style={"color": NOVAColor.TEXT_SECONDARY.color},
            tooltip_fn=lambda: build_tooltip(_PRODUCTS_TOOLTIP),
        )
