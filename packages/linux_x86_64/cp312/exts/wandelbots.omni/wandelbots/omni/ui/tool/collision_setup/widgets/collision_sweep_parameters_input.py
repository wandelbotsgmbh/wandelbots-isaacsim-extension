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
from wandelbots.omni.ui.tool.collision_setup.widgets.collision_export_form import (
    NOVAColor,
)
from wandelbots.omni.ui.widgets import (
    PrimPicker,
    PrimPickerDialogProperties,
    CoordinatesInput,
    CoordinateInputFieldModel,
)
from omni.kit.async_engine import run_coroutine

WINDOW_MENU_ROOT = "Tools"


class SphereRadiusModel(ui.SimpleFloatModel):
    def min(self):
        return 0


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
            with ui.ZStack():
                ui.Rectangle(
                    style={
                        "background_color": NOVAColor.BACKGROUND_PAPER.color,
                        "border_radius": 4,
                    }
                )
                with ui.VStack(
                    height=0,
                    spacing=2,
                    style={"VStack": {"margin": 4}},
                ):
                    with ui.VGrid(
                        name="sweep_parameters_grid",
                        column_count=2,
                        row_height=ui.Pixel(24),
                    ):
                        ui.Label(
                            "Sweep type",
                            tooltip="Shape used for sweep collision detection",
                        )
                        sweep_type_model = ui.ComboBox(
                            self._sweep_types.index(self._selected_sweep_type_model),
                            *self._sweep_types,
                        ).model

                        def _on_sweep_type_changed(
                            model: ui.AbstractItemModel,
                            item: ui.AbstractItem,
                            weak_self=weakref.ref(self),
                        ):
                            weak_self = weak_self()
                            if not weak_self:
                                return
                            weak_self._selected_sweep_type_model = (
                                weak_self._sweep_types[
                                    model.get_item_value_model(item).as_int
                                ]
                            )
                            weak_self._deferred_build_ui()

                        sweep_type_model.add_item_changed_fn(_on_sweep_type_changed)

                    if self._selected_sweep_type_model == "tree":

                        def _on_tree_sweep_parameters_changed(
                            parameters: TreeSweepParameters,
                            weak_self=weakref.ref(self),
                        ):
                            weak_self = weak_self()
                            if not weak_self:
                                return
                            weak_self._tree_sweep_arguments = parameters

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
                            weak_self = weak_self()
                            if not weak_self:
                                return
                            weak_self._sphere_sweep_arguments = parameters

                        self._sphere_sweep_form = SphereSweepForm(
                            self._sphere_sweep_arguments,
                            _on_sphere_sweep_parameters_changed,
                        )
                    else:
                        raise ValueError(
                            f"Unknown sweep type: {self._selected_sweep_type_model}"
                        )

    def _deferred_build_ui(self):
        async def wait_one_frame_and_build():
            await omni.kit.app.get_app().next_update_async()
            self._build_ui()

        run_coroutine(wait_one_frame_and_build())

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
                weak_self = weak_self()
                if not weak_self:
                    return
                weak_self._parameters.radius = weak_self._sphere_radius_model.as_float
                weak_self._parameters.position = [
                    model.as_float for model in weak_self._position_models
                ]
                weak_self._on_changed_fn(weak_self._parameters)

            self._sphere_radius_model.add_value_changed_fn(
                lambda _: on_any_value_changed()
            )
            for idx, model in enumerate(self._position_models):
                model.add_value_changed_fn(lambda _, _1: on_any_value_changed())

        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        with ui.VStack(spacing=4):
            # spacing has no effect inside VGrid, so we add an extra VStack
            with ui.VGrid(column_count=2, row_height=ui.Pixel(24)):
                ui.Label(
                    "Sweep radius",
                    tooltip="Radius for sphere sweep collision detection",
                )
                ui.FloatDrag(
                    model=self._sphere_radius_model,
                    min=0,
                    step=0.01,
                    suffix="m",
                    tooltip="Radius for sphere sweep collision detection",
                )
            with ui.VGrid(column_count=2, row_height=ui.Pixel(24)):
                ui.Label("Position", tooltip="World position of the sweep")
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
                weak_self = weak_self()
                if not weak_self:
                    return
                weak_self._parameters.base_prim_path = (
                    weak_self._base_prim_path_model.as_string
                )
                weak_self._on_changed_fn(weak_self._parameters)

            self._base_prim_path_model.add_value_changed_fn(
                lambda _: on_any_value_changed()
            )

        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        with ui.VGrid(
            column_count=2, row_height=ui.Pixel(24), style={"VGrid": {"margin": 4}}
        ):
            ui.Label(
                "Base Prim Path",
                tooltip="The base prim path for the tree sweep",
            )

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
