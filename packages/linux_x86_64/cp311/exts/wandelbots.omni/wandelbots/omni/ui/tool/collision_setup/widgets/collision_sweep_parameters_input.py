from typing import Callable, Literal
from pxr import Usd
import omni
import weakref
import omni.ui as ui
from wandelbots.omni.core.collision.collision_export_service import (
    SphereSweepParameters,
    TreeSweepParameters,
    SweepParameters,
)

import omni.usd
from wandelbots.omni.ui.wb_theme import (
    TOOLTIP_RESET,
    COMBOBOX_STYLE,
    FIELD_STYLE,
    SPACING_SM,
    build_tooltip,
)
from wandelbots.omni.ui.widgets import (
    PrimPicker,
    PrimPickerDialogProperties,
    CoordinatesInput,
    CoordinateInputFieldModel,
)
from wandelbots.omni.ui.utils import defer_call
from wandelbots.omni.ui.widgets.form_row import form_row

SweepTypes = Literal["sphere", "tree"]


class CollisionSweepParametersInput:
    def __init__(self, default_parameters: SweepParameters = None):
        self._stage: Usd.Stage = omni.usd.get_context().get_stage()

        self._sweep_types: list[SweepTypes] = ["sphere", "tree"]
        self._selected_sweep_type_model: SweepTypes = (
            default_parameters.sweep_type if default_parameters else "tree"
        )

        self._sphere_sweep_arguments = (
            default_parameters
            if isinstance(default_parameters, SphereSweepParameters)
            else SphereSweepParameters(
                sweep_type="sphere",
                radius=10.0,
                position=[0.0, 0.0, 0.0],
                direction=[0.0, 0.0, 1.0],
                max_distance=0,
            )
        )
        self._tree_sweep_arguments = (
            default_parameters
            if isinstance(default_parameters, TreeSweepParameters)
            else TreeSweepParameters(sweep_type="tree", base_prim_path="/")
        )

        self.frame = ui.Frame(height=0)
        self._build_ui()

    def _build_ui(self):
        self.frame.clear()
        if self._stage is None:
            with self.frame:
                ui.Label("No stage loaded.", height=30)
            return

        with self.frame:
            with ui.VStack(height=0, spacing=SPACING_SM):
                with form_row(
                    "Sweep type",
                    tooltip="Shape used for sweep collision detection",
                ):
                    sweep_type_model = ui.ComboBox(
                        self._sweep_types.index(self._selected_sweep_type_model),
                        *self._sweep_types,
                        height=20,
                        style=COMBOBOX_STYLE,
                        tooltip_fn=lambda: build_tooltip(
                            "Shape used for sweep collision detection"
                        ),
                    ).model

                    def _on_sweep_type_changed(
                        model: ui.AbstractItemModel,
                        item: ui.AbstractItem,
                        weak_self=weakref.ref(self),
                    ):
                        input_widget = weak_self()
                        if not input_widget:
                            return
                        input_widget._selected_sweep_type_model = (
                            input_widget._sweep_types[
                                model.get_item_value_model(item).as_int
                            ]
                        )
                        input_widget._deferred_build_ui()

                    sweep_type_model.add_item_changed_fn(_on_sweep_type_changed)

                if self._selected_sweep_type_model == "tree":

                    def _on_tree_sweep_parameters_changed(
                        parameters: TreeSweepParameters,
                        weak_self=weakref.ref(self),
                    ):
                        input_widget = weak_self()
                        if not input_widget:
                            return
                        input_widget._tree_sweep_arguments = parameters

                    self._tree_sweep_form = TreeSweepForm(
                        self._tree_sweep_arguments,
                        self._stage,
                        _on_tree_sweep_parameters_changed,
                    )

                elif self._selected_sweep_type_model == "sphere":

                    def _on_sphere_sweep_parameters_changed(
                        parameters: SphereSweepParameters,
                        weak_self=weakref.ref(self),
                    ):
                        input_widget = weak_self()
                        if not input_widget:
                            return
                        input_widget._sphere_sweep_arguments = parameters

                    self._sphere_sweep_form = SphereSweepForm(
                        self._sphere_sweep_arguments,
                        _on_sphere_sweep_parameters_changed,
                    )
                else:
                    raise ValueError(
                        f"Unknown sweep type: {self._selected_sweep_type_model}"
                    )

    def _deferred_build_ui(self):
        defer_call(self._build_ui)

    @property
    def parameters(self) -> SweepParameters:
        return (
            self._sphere_sweep_arguments
            if self._selected_sweep_type_model == "sphere"
            else self._tree_sweep_arguments
        )


class SphereSweepForm(ui.Widget):
    def __init__(
        self,
        parameters: SphereSweepParameters,
        on_changed_fn: Callable[[SphereSweepParameters], None],
        **kwargs,
    ):
        self._parameters = parameters

        self._sphere_radius_model = ui.SimpleFloatModel(parameters.radius)
        self._position_models = [
            ui.SimpleFloatModel(parameters.position[0]),
            ui.SimpleFloatModel(parameters.position[1]),
            ui.SimpleFloatModel(parameters.position[2]),
        ]

        self._on_changed_fn = on_changed_fn

        if self._on_changed_fn:

            def on_any_value_changed(weak_self=weakref.ref(self)):
                form = weak_self()
                if not form:
                    return
                form._parameters.radius = form._sphere_radius_model.as_float
                form._parameters.position = [
                    model.as_float for model in form._position_models
                ]
                form._on_changed_fn(form._parameters)

            self._sphere_radius_model.add_value_changed_fn(
                lambda _: on_any_value_changed()
            )
            for model in self._position_models:
                model.add_value_changed_fn(lambda _: on_any_value_changed())

        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        with ui.VStack(height=0, spacing=SPACING_SM):
            with form_row(
                "Sweep radius [m]",
                tooltip="Radius for sphere sweep collision detection, in meters",
            ):
                ui.FloatDrag(
                    model=self._sphere_radius_model,
                    min=0,
                    step=0.01,
                    height=20,
                    style={**FIELD_STYLE, **TOOLTIP_RESET},
                    tooltip_fn=lambda: build_tooltip(
                        "Radius for sphere sweep collision detection"
                    ),
                )
            with form_row(
                "Position [m]", tooltip="World position of the sweep, in meters"
            ):
                CoordinatesInput(
                    fields=[
                        CoordinateInputFieldModel(
                            model=self._position_models[0],
                            label="X",
                            tooltip="X position",
                        ),
                        CoordinateInputFieldModel(
                            model=self._position_models[1],
                            label="Y",
                            tooltip="Y position",
                        ),
                        CoordinateInputFieldModel(
                            model=self._position_models[2],
                            label="Z",
                            tooltip="Z position",
                        ),
                    ]
                )


class TreeSweepForm(ui.Widget):
    def __init__(
        self,
        parameters: TreeSweepParameters,
        stage: Usd.Stage,
        on_changed_fn: Callable[[TreeSweepParameters], None],
        **kwargs,
    ):
        self._parameters = parameters
        self._stage = stage
        self._base_prim_path_model = ui.SimpleStringModel(parameters.base_prim_path)
        self._on_changed_fn = on_changed_fn

        if self._on_changed_fn:

            def on_any_value_changed(weak_self=weakref.ref(self)):
                form = weak_self()
                if not form:
                    return
                form._parameters.base_prim_path = form._base_prim_path_model.as_string
                form._on_changed_fn(form._parameters)

            self._base_prim_path_model.add_value_changed_fn(
                lambda _: on_any_value_changed()
            )

        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        with form_row(
            "Base Prim Path",
            tooltip="The base prim path for the tree sweep",
        ):

            def assign_prim(
                prim: Usd.Prim,
                weak_self: TreeSweepForm = weakref.proxy(self),
            ):
                weak_self._parameters.base_prim_path = prim.GetPath().pathString

            self._base_prim_path_picker = PrimPicker(
                stage=self._stage,
                prim_picked_fn=assign_prim,
                prim=self._stage.GetPrimAtPath(self._parameters.base_prim_path),
                dialog_properties=PrimPickerDialogProperties(
                    title="Base Prim",
                ),
            )
