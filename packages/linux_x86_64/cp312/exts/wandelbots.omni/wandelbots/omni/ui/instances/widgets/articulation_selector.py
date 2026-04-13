import carb
import omni.ui as ui
from typing import Callable, Optional


class ArticulationSelector(ui.HStack):
    """Dropdown selector for choosing an articulation prim path.

    When a motion group is already connected, displays the current prim path
    as a read-only field. Otherwise shows a ComboBox with available
    articulations and optional pre-selected suggestion.
    """

    def __init__(
        self,
        articulations: list[str],
        on_selection_changed: Optional[Callable[[Optional[str]], None]] = None,
        connected_prim_path: Optional[str] = None,
        initial_selection: Optional[str] = None,
        read_only: bool = False,
        **kwargs,
    ):
        kwargs.setdefault("height", 25)
        super().__init__(**kwargs)

        self._articulations = articulations
        self._on_selection_changed = on_selection_changed
        self._selected: Optional[str] = None

        with self:
            ui.Spacer(width=15)
            ui.Label("Articulation:", width=150)

            if connected_prim_path or read_only:
                self._selected = connected_prim_path or initial_selection
                ui.StringField(
                    ui.SimpleStringModel(self._selected or "--"),
                    read_only=True,
                )
            else:
                self._build_combo(initial_selection)

            ui.Spacer(width=10)

    @property
    def selected(self) -> Optional[str]:
        """Currently selected articulation prim path, or ``None``."""
        return self._selected

    def _build_combo(self, initial_selection: Optional[str]):
        dropdown_items = ["-- Select Articulation --"] + self._articulations
        combo = ui.ComboBox(0, *dropdown_items, alignment=ui.Alignment.CENTER)

        def _on_changed(model: ui.AbstractItemModel, _):
            try:
                idx = model.get_item_value_model().as_int
                if 0 < idx < len(dropdown_items):
                    self._selected = self._articulations[idx - 1]
                else:
                    self._selected = None
                if self._on_selection_changed:
                    self._on_selection_changed(self._selected)
            except Exception as e:
                carb.log_error(f"Error getting selection from model: {e}")

        combo.model.add_item_changed_fn(_on_changed)

        # Apply initial selection
        if initial_selection and initial_selection in self._articulations:
            self._selected = initial_selection
            combo.model.get_item_value_model().set_value(
                self._articulations.index(initial_selection) + 1
            )
