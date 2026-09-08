from __future__ import annotations

import asyncio
import weakref
from typing import Callable, Optional

import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
from omni.kit.async_engine import run_coroutine
from pxr import Usd

from wandelbots.omni.instances.events import push_ui_busy_changed
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import (
    NOVAControllerData,
    NOVAInstance,
    NOVAMotionGroupData,
)
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.manufacturers import manufacturers_from_controller_types
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.widgets.form_row import labeled_row
from wandelbots.omni.ui.widgets.progress_status_bar import ProgressStatusBar
from wandelbots.omni.ui.widgets.styled_checkbox import styled_checkbox
from wandelbots.omni.ui.wb_theme import (
    BUTTON_HEIGHT,
    BUTTON_PRIMARY_STYLE,
    BUTTON_STYLE,
    COMBOBOX_STYLE,
    FIELD_STYLE,
    TOOLTIP_RESET,
    build_tooltip,
)
from wandelbots.omni.usd.schema_utils import SchemaUtils
from wandelbots.omni.utils.prims import PrimUtils

from wandelbots.omni.ui.instances.articulations.motion_group_widget import (
    MotionGroupWidget,
)
from wandelbots.omni.ui.widgets.type_icon import TypeIcon
from wandelbots.omni.ui.instances.articulations.virtual_controller_service import (
    ControllerAlreadyExistsError,
    collect_tcps_from_prim,
    create_virtual_controller,
    fetch_cells,
    fetch_configuration_for_motion_group,
    fetch_robot_configurations,
    normalize_name,
)

_LABEL_WIDTH = 150

# Virtual controller creation can involve several sequential NOVA API calls, so
# cap the whole flow rather than letting it hang on a stalled backend.
_CREATE_TIMEOUT_S = 60.0
# The motion group only appears a moment after the controller is created, so the
# first connect attempts can legitimately fail; retry a few times before giving up.
_CONNECT_RETRIES = 5
_CONNECT_RETRY_DELAY_S = 2.0


class UnassignedArticulationRow(ui.VStack):
    """Level 5 widget for a single unassigned articulation.

    Shows a collapsible row with the articulation name. When expanded,
    displays an inline form to create a virtual controller in NOVA.
    Only one row can be expanded at a time (controlled by parent).
    """

    def __init__(
        self,
        prim: Usd.Prim,
        instances: list[NOVAInstance],
        instances_service: NOVAInstancesService,
        on_expand: Optional[Callable[["UnassignedArticulationRow"], None]] = None,
        on_created: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._prim = prim
        # The target instance is chosen per row via the instance combo; until then
        # there is no instance, so cells/configs/pairs are deferred (see _instance).
        self._instances = instances
        self._selected_instance_idx = 0
        self._instances_service = instances_service
        # Existing-but-unconnected NOVA (controller, motion group) pairs on the
        # selected instance that the user can connect to instead of creating a new
        # controller. Recomputed whenever the selected instance changes.
        self._available_pairs: list[tuple[NOVAControllerData, NOVAMotionGroupData]] = []
        self._instance_combo_sub = None
        self._controller_frame: Optional[ui.Frame] = None
        self._on_expand = on_expand
        self._on_created = on_created

        custom_data = prim.GetCustomData()
        # Same key chain as stage_discovery._get_prim_model_name: downloaded
        # robots store their model under motionGroupModel, older prims used
        # motion_group_name, and the prim name is the last resort. Reading the
        # wrong key here sends the prim name into the configuration lookup and
        # the kinematics (type icon) request, both of which then miss.
        self._model_name = (
            custom_data.get("motionGroupModel")
            or custom_data.get("motion_group_name")
            or prim.GetName()
        )
        self._prim_path = prim.GetPrimPath().pathString

        # Manufacturer/type can be provided via prim custom data. When both are
        # present the controller can be created without user input, so the combos
        # are hidden; otherwise the user must pick them before creating.
        self._preset_manufacturer = custom_data.get("manufacturer")
        self._preset_type = custom_data.get("robot_configuration_name")
        self._has_presets = bool(self._preset_manufacturer and self._preset_type)

        self._cells: list[str] = []
        self._selected_cell_idx = 0
        self._controller_name = self._model_name.lower().replace("_", "-")
        self._cells_task: Optional[asyncio.Task] = None
        self._create_task: Optional[asyncio.Task] = None
        self._connect_task: Optional[asyncio.Task] = None
        self._type_icon: Optional[TypeIcon] = None

        # All robot configuration type strings fetched from NOVA (e.g.
        # "universalrobots-ur10e"). The manufacturer combo lists friendly labels and
        # the type combo lists the types whose prefix matches the selected
        # manufacturer. Both selection indices are combo indices where 0 is the
        # "Please select..." placeholder.
        self._all_types: list[str] = []
        self._selected_manufacturer_idx = 0
        self._selected_type_idx = 0
        self._manufacturer_frame: Optional[ui.Frame] = None
        self._type_frame: Optional[ui.Frame] = None
        self._manufacturer_combo_sub = None
        self._type_combo_sub = None
        self._configs_task: Optional[asyncio.Task] = None

        # Configuration auto-resolved from the model name via
        # getConfigurationForMotionGroup, tried before falling back to the
        # manual manufacturer/type combos (see _resolve_configuration). None
        # while unresolved, or when the lookup misses / the endpoint isn't
        # available on this NOVA instance.
        self._auto_resolved_config: Optional[str] = None
        self._auto_resolve_pending = False
        self._auto_resolve_attempted = False
        self._config_frame: Optional[ui.Frame] = None

        # Index into the controller picker: 0 == placeholder (no content shown yet),
        # 1 == "Create new virtual controller", 2..N map to
        # self._available_pairs[idx - 2].
        self._selected_pair_idx = 0
        self._controller_combo_sub = None
        self._content_frame: Optional[ui.Frame] = None
        self._mg_widget: Optional[MotionGroupWidget] = None

        self._section: Optional[CollapsibleSection] = None
        self._cell_frame: Optional[ui.Frame] = None
        self._cell_combo_sub = None
        self._name_field_model: Optional[ui.SimpleStringModel] = None
        # When checked, the robot's mounting pose is defined in NOVA for the new
        # controller. Defaults to on, preserving the previous always-on behaviour.
        self._mounting_in_nova = True
        self._mounting_checkbox_model: Optional[ui.SimpleBoolModel] = None
        self._progress_container: Optional[ui.VStack] = None
        self._form_stack: Optional[ui.VStack] = None
        self._progress = ProgressStatusBar(name=self._model_name)

        self._build()

    @property
    def _instance(self) -> Optional[NOVAInstance]:
        # Index 0 is the "Select an option..." placeholder; instances start at 1.
        if self._selected_instance_idx <= 0:
            return None
        return self._instances[
            min(self._selected_instance_idx - 1, len(self._instances) - 1)
        ]

    @property
    def collapsed(self) -> bool:
        return self._section.collapsed if self._section else True

    @collapsed.setter
    def collapsed(self, value: bool):
        if self._section:
            self._section.collapsed = value

    def collapse(self):
        self.collapsed = True
        self._cancel_ui_tasks()

    def _build(self):
        with self:
            self._section = CollapsibleSection(
                title=self._model_name,
                collapsed=True,
                title_color=NOVAColor.TEXT_PRIMARY_CONTRAST,
                build_leading_fn=lambda sec, _self=self: _self._build_type_icon(),
                on_collapsed_changed=lambda collapsed, _self=self: (
                    _self._on_collapsed_changed(collapsed)
                ),
            )
            with self._section.body:
                self._build_body()

    def _build_type_icon(self):
        # Kinematics are model-based, so any reachable instance can resolve the
        # glyph; the row's target instance is not chosen yet at build time. Prefer
        # a confirmed-reachable instance so the lookup is not fired at a dead host
        # during the is_reachable=True probe window, where it would otherwise waste
        # the full per-call timeout on an icon that can never resolve.
        instance = next(
            (inst for inst in (self._instances or []) if inst.is_reachable),
            None,
        )
        self._type_icon = TypeIcon(instance, self._model_name)

    def _build_body(self):
        # Breathing room between the section header and the first row.
        ui.Spacer(height=8)
        # The instance must be chosen first; the controller picker (and its
        # available pairs) and the rest of the form depend on it.
        self._build_instance_picker()
        ui.Spacer(height=6)
        self._controller_frame = ui.Frame()
        ui.Spacer(height=6)
        self._content_frame = ui.Frame()
        self._rebuild_controller_picker()
        self._rebuild_content()

    def _build_instance_picker(self):
        with ui.HStack(height=24):
            ui.Spacer(width=15)
            ui.Label("Instance", width=_LABEL_WIDTH)
            if not self._instances:
                combo = ui.ComboBox(0, "No reachable instances", style=COMBOBOX_STYLE)
                combo.enabled = False
                ui.Spacer(width=10)
                return
            labels = ["Select an option..."] + [
                inst.display_name for inst in self._instances
            ]
            combo = ui.ComboBox(
                self._selected_instance_idx,
                *labels,
                style=COMBOBOX_STYLE,
                tooltip_fn=lambda: build_tooltip(
                    "Select the NOVA instance to assign this articulation to."
                ),
            )
            self._instance_combo_sub = combo.model.subscribe_item_changed_fn(
                lambda model, _, ws=weakref.ref(self): (
                    ws()._on_instance_changed(model) if ws() else None
                )
            )
            ui.Spacer(width=10)

    def _on_instance_changed(self, model):
        idx = model.get_item_value_model().as_int
        if idx == self._selected_instance_idx:
            return
        self._selected_instance_idx = idx
        # Cells, configs and available pairs all belong to the chosen instance, so
        # reset the downstream selections before refetching.
        self._selected_pair_idx = 0
        self._cells = []
        self._selected_cell_idx = 0
        self._all_types = []
        self._selected_manufacturer_idx = 0
        self._selected_type_idx = 0
        self._auto_resolved_config = None
        self._auto_resolve_pending = False
        self._auto_resolve_attempted = False
        self._compute_available_pairs()
        self._rebuild_controller_picker()
        self._rebuild_content()
        if self._instance is not None:
            self._cells_task = run_coroutine(self._fetch_cells())
            if not self._has_presets:
                self._configs_task = run_coroutine(self._resolve_configuration())

    def _compute_available_pairs(self):
        instance = self._instance
        pairs: list[tuple[NOVAControllerData, NOVAMotionGroupData]] = []
        if instance is None:
            self._available_pairs = []
            return
        for cell in instance.cells or []:
            for controller in cell.controllers or []:
                for mg in controller.motion_groups or []:
                    if self._instances_service.find_connected_motion_group_by(
                        host=instance.host,
                        cell=controller.cell_name,
                        controller=controller.name,
                        motion_group=mg.name,
                    ):
                        continue
                    pairs.append((controller, mg))
        self._available_pairs = pairs

    def _rebuild_controller_picker(self):
        if not self._controller_frame:
            return
        self._controller_combo_sub = None
        self._controller_frame.clear()
        # No controller choices make sense until an instance is selected.
        if self._instance is None:
            return
        with self._controller_frame:
            self._build_controller_picker()

    def _build_controller_picker(self):
        with ui.HStack(height=24):
            ui.Spacer(width=15)
            ui.Label("Controller", width=_LABEL_WIDTH)
            labels = [
                "Select an option...",
                "Create new virtual controller",
            ] + [
                f"{controller.name} ({mg.name})"
                for controller, mg in self._available_pairs
            ]
            combo = ui.ComboBox(
                self._selected_pair_idx,
                *labels,
                style=COMBOBOX_STYLE,
                tooltip_fn=lambda: build_tooltip(
                    "Connect to an existing NOVA controller, or create a new one."
                ),
            )
            self._controller_combo_sub = combo.model.subscribe_item_changed_fn(
                lambda model, _, ws=weakref.ref(self): (
                    ws()._on_controller_changed(model) if ws() else None
                )
            )
            ui.Spacer(width=10)

    def _on_controller_changed(self, model):
        idx = model.get_item_value_model().as_int
        if idx == self._selected_pair_idx:
            return
        self._selected_pair_idx = idx
        self._rebuild_content()

    def _rebuild_content(self):
        if not self._content_frame:
            return
        # The form's cell row is recreated by _build_form; drop the stale handle so
        # a pending cell fetch does not write into a cleared frame.
        self._cell_frame = None
        self._manufacturer_frame = None
        self._type_frame = None
        self._config_frame = None
        self._mg_widget = None
        self._content_frame.clear()
        with self._content_frame:
            # Placeholder selected: show only the combo box until a real choice.
            if self._selected_pair_idx == 0:
                return
            if self._selected_pair_idx == 1:
                self._build_form()
                return
            controller, mg = self._available_pairs[self._selected_pair_idx - 2]
            self._mg_widget = MotionGroupWidget(
                instances_service=self._instances_service,
                instance=self._instance,
                controller=controller,
                motion_group=mg,
                fixed_prim_path=self._prim_path,
                on_connection_changed=self._on_created,
            )

    def _on_collapsed_changed(self, collapsed: bool):
        # Cells and configs are fetched when an instance is picked (see
        # _on_instance_changed), not on expand, since there is no target instance
        # until the user selects one.
        if not collapsed and self._on_expand:
            self._on_expand(self)

    def _build_form(self):
        with ui.VStack(spacing=4):
            self._form_stack = ui.VStack(spacing=4)
            with self._form_stack:
                with labeled_row("Prim", label_width=_LABEL_WIDTH):
                    ui.Label(
                        self._prim_path,
                        style={"color": NOVAColor.TEXT_PRIMARY.color},
                    )

                with labeled_row("Cell", label_width=_LABEL_WIDTH):
                    self._cell_frame = ui.Frame()

                if not self._has_presets:
                    self._config_frame = ui.Frame()

                with labeled_row("Controller Name", label_width=_LABEL_WIDTH):
                    field = ui.StringField(
                        height=24,
                        style={**FIELD_STYLE, **TOOLTIP_RESET},
                        tooltip_fn=lambda: build_tooltip(
                            "Name for the virtual controller created in NOVA."
                        ),
                    )
                    field.model.set_value(self._controller_name)
                    self._name_field_model = field.model

                # Built manually rather than via labeled_row so the tooltip can
                # live on the label: the checkbox carries flat background/color
                # style keys that would otherwise bleed into the self-drawn
                # build_tooltip popup (see ExternalJointStreamCheckbox).
                with ui.HStack(height=24):
                    ui.Spacer(width=15)
                    ui.Label(
                        "Mounting in NOVA",
                        width=_LABEL_WIDTH,
                        alignment=ui.Alignment.LEFT_CENTER,
                        style=TOOLTIP_RESET,
                        tooltip_fn=lambda: build_tooltip(
                            "When enabled, the robot's mounting pose is "
                            "defined in NOVA for this controller."
                        ),
                    )
                    ui.Spacer()
                    # Wrap the fixed-height checkbox in a VStack with spacers so it
                    # centers vertically against the label.
                    with ui.VStack(width=20):
                        ui.Spacer()
                        checkbox = styled_checkbox(
                            width=20,
                            height=20,
                        )
                        ui.Spacer()
                    checkbox.model.set_value(self._mounting_in_nova)
                    self._mounting_checkbox_model = checkbox.model
                    ui.Spacer(width=10)

                ui.Spacer(height=2)

                with ui.HStack(height=BUTTON_HEIGHT, spacing=0):
                    ui.Spacer()
                    ui.Button(
                        "Cancel",
                        width=0,
                        height=BUTTON_HEIGHT,
                        style={**BUTTON_STYLE, **TOOLTIP_RESET},
                        clicked_fn=lambda _self=self: _self._on_cancel(),
                        tooltip_fn=lambda: build_tooltip(
                            "Discard this controller setup."
                        ),
                    )
                    ui.Spacer(width=8)
                    ui.Button(
                        "Add to NOVA",
                        width=0,
                        height=BUTTON_HEIGHT,
                        clicked_fn=lambda _self=self: _self._on_confirm(),
                        style={**BUTTON_PRIMARY_STYLE, **TOOLTIP_RESET},
                        tooltip_fn=lambda: build_tooltip(
                            "Create a virtual controller for this articulation in NOVA."
                        ),
                    )
                    ui.Spacer(width=10)
                ui.Spacer(height=2)

            self._progress_container = ui.VStack(visible=False, spacing=4)
            with self._progress_container:
                ui.Spacer(height=4)
                # Inset the progress block so its bar lines up with the form
                # labels on the left (15) and the combo boxes on the right (10).
                with ui.HStack(height=0):
                    ui.Spacer(width=15)
                    with ui.VStack(height=0, spacing=4):
                        self._progress.build()
                    ui.Spacer(width=10)
                ui.Spacer(height=4)

            ui.Spacer(height=4)

        self._rebuild_cell_row()
        if not self._has_presets:
            self._rebuild_config_frame()

    def _rebuild_cell_row(self):
        if not self._cell_frame:
            return
        self._cell_combo_sub = None
        self._cell_frame.clear()

        with self._cell_frame:
            if not self._cells:
                combo = ui.ComboBox(0, "Loading...", style=COMBOBOX_STYLE)
                combo.enabled = False
            else:
                idx = min(self._selected_cell_idx, len(self._cells) - 1)
                combo = ui.ComboBox(
                    idx,
                    *self._cells,
                    style=COMBOBOX_STYLE,
                    tooltip_fn=lambda: build_tooltip(
                        "Select the NOVA cell to create the controller in."
                    ),
                )

                def _on_cell_changed(model, _, ws=weakref.ref(self)):
                    obj = ws()
                    if obj is None:
                        return
                    obj._selected_cell_idx = model.get_item_value_model().as_int

                self._cell_combo_sub = combo.model.subscribe_item_changed_fn(
                    _on_cell_changed
                )

    async def _fetch_cells(self):
        self._cells = await fetch_cells(self._instance)
        self._rebuild_cell_row()

    def _manufacturer_options(self) -> dict[str, str]:
        """Display label -> controller-type prefix, derived from the types the
        instance actually offers - a manufacturer NOVA adds later shows up
        here without a code change."""
        return manufacturers_from_controller_types(self._all_types)

    def _available_manufacturers(self) -> list[str]:
        return list(self._manufacturer_options())

    def _filtered_types(self) -> list[str]:
        options = self._manufacturer_options()
        manufacturers = list(options)
        if self._selected_manufacturer_idx <= 0 or not manufacturers:
            return []
        label = manufacturers[
            min(self._selected_manufacturer_idx - 1, len(manufacturers) - 1)
        ]
        prefix = options[label]
        return sorted(
            t for t in self._all_types if t.split("-", 1)[0].lower() == prefix
        )

    async def _resolve_configuration(self):
        """Try the model-name lookup first, falling back to manual combos.

        The manual combos (built by _rebuild_config_frame) only need the
        full type list when the lookup misses - whether because the model
        isn't mapped to a configuration, or because the connected NOVA
        instance predates this endpoint entirely.
        """
        self._auto_resolve_pending = True
        config = await fetch_configuration_for_motion_group(
            self._instance, self._model_name
        )
        self._auto_resolve_pending = False
        self._auto_resolve_attempted = True
        if config:
            self._auto_resolved_config = config
        else:
            self._all_types = await fetch_robot_configurations(self._instance)
        self._rebuild_config_frame()

    def _rebuild_config_frame(self):
        if not self._config_frame:
            return
        self._config_frame.clear()
        if self._auto_resolved_config or not self._auto_resolve_attempted:
            # Resolved automatically (or still resolving/not started yet):
            # stay hidden, same as the existing preset path.
            self._manufacturer_frame = None
            self._type_frame = None
            return
        with self._config_frame:
            # A ui.Frame keeps only its last child, so both rows must live in a
            # shared VStack - building them directly into the frame silently
            # drops the Manufacturer row (leaving the type combo forever empty,
            # since no manufacturer can ever be selected to filter the types).
            with ui.VStack(spacing=4):
                with labeled_row("Manufacturer", label_width=_LABEL_WIDTH):
                    self._manufacturer_frame = ui.Frame()
                with labeled_row("Controller Type", label_width=_LABEL_WIDTH):
                    self._type_frame = ui.Frame()
        self._preselect_from_custom_data()
        self._rebuild_manufacturer_row()
        self._rebuild_type_row()

    def _preselect_from_custom_data(self):
        # Preselect the manufacturer combo from the prim's custom data, and the
        # type combo when the model name normalizes to exactly one offered
        # configuration - so the fallback combos usually need no manual input.
        # Runs only while nothing is selected yet, so it never overrides a
        # user's choice on rebuild.
        if self._selected_manufacturer_idx != 0 or not self._all_types:
            return
        preset_prefix = (self._preset_manufacturer or "").strip().lower()
        if not preset_prefix:
            return
        for index, prefix in enumerate(self._manufacturer_options().values()):
            if prefix == preset_prefix:
                self._selected_manufacturer_idx = index + 1
                break
        else:
            return
        if self._selected_type_idx == 0:
            normalized_model = normalize_name(self._model_name)
            matches = [
                index
                for index, type_str in enumerate(self._filtered_types())
                if normalize_name(type_str) == normalized_model
            ]
            if len(matches) == 1:
                self._selected_type_idx = matches[0] + 1

    def _rebuild_manufacturer_row(self):
        if not self._manufacturer_frame:
            return
        self._manufacturer_combo_sub = None
        self._manufacturer_frame.clear()

        manufacturers = self._available_manufacturers()
        with self._manufacturer_frame:
            if not manufacturers:
                combo = ui.ComboBox(0, "Loading...", style=COMBOBOX_STYLE)
                combo.enabled = False
                return

            items = ["Please select the manufacturer"] + manufacturers
            idx = min(self._selected_manufacturer_idx, len(items) - 1)
            combo = ui.ComboBox(
                idx,
                *items,
                style=COMBOBOX_STYLE,
                tooltip_fn=lambda: build_tooltip("Select the robot manufacturer."),
            )

            def _on_manufacturer_changed(model, _, ws=weakref.ref(self)):
                obj = ws()
                if obj is None:
                    return
                new_idx = model.get_item_value_model().as_int
                if new_idx == obj._selected_manufacturer_idx:
                    return
                obj._selected_manufacturer_idx = new_idx
                obj._selected_type_idx = 0
                obj._rebuild_type_row()

            self._manufacturer_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_manufacturer_changed
            )

    def _rebuild_type_row(self):
        if not self._type_frame:
            return
        self._type_combo_sub = None
        self._type_frame.clear()

        types = self._filtered_types()
        with self._type_frame:
            if not self._all_types:
                combo = ui.ComboBox(0, "Loading...", style=COMBOBOX_STYLE)
                combo.enabled = False
                return

            items = ["Please select the controller type"] + types
            idx = min(self._selected_type_idx, len(items) - 1)
            combo = ui.ComboBox(
                idx,
                *items,
                style=COMBOBOX_STYLE,
                tooltip_fn=lambda: build_tooltip(
                    "Select the controller type to create."
                ),
            )
            # Disabled until a manufacturer is chosen so the user picks in order.
            combo.enabled = bool(types)

            def _on_type_changed(model, _, ws=weakref.ref(self)):
                obj = ws()
                if obj is None:
                    return
                obj._selected_type_idx = model.get_item_value_model().as_int

            self._type_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_type_changed
            )

    def _on_cancel(self):
        # Explicit user cancel, so the create/connect sequence goes too.
        self._cancel_all_tasks()
        self.collapsed = True

    def _on_confirm(self):
        if self._instance is None:
            nm.post_notification(
                "Select an instance first.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        if not self._cells:
            nm.post_notification(
                "No cells available yet. Please wait for cells to load.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        if self._name_field_model:
            self._controller_name = self._name_field_model.as_string

        if self._mounting_checkbox_model:
            self._mounting_in_nova = self._mounting_checkbox_model.as_bool

        self._form_stack.visible = False
        self._progress_container.visible = True
        self._progress.show(0.0)
        self._progress.set_hint("Creating controller...")
        self._create_task = run_coroutine(self._do_create())

    def _on_progress(self, value: float, text: str):
        self._progress.update(value)
        if text:
            self._progress.set_hint(text)

    async def _do_create(self):
        push_ui_busy_changed(True, "Creating virtual controller in NOVA...")
        try:
            await asyncio.wait_for(
                self._create_virtual_controller(), timeout=_CREATE_TIMEOUT_S
            )
            # _create_virtual_controller only schedules _connect_task; hold the busy
            # gate until it finishes, or the panel rebuild tears this row down
            # mid-retry. shield: cancellation must not abort the server-side connect.
            if self._connect_task is not None:
                await asyncio.shield(self._connect_task)
        except asyncio.TimeoutError:
            carb.log_error(
                f"Virtual controller creation timed out after {_CREATE_TIMEOUT_S:.0f}s."
            )
            nm.post_notification(
                "Virtual controller creation timed out.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
        except Exception as e:
            carb.log_error(f"Virtual controller creation failed: {e}")
            nm.post_notification(
                f"Virtual controller creation failed: {e}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
        finally:
            push_ui_busy_changed(False)
            self._form_stack.visible = True
            self._progress_container.visible = False
            self._progress.hide()

    async def _create_virtual_controller(self):
        prim = self._prim
        if not prim or not prim.IsValid():
            nm.post_notification(
                "Selected motion group prim is no longer valid.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        if self._has_presets:
            manufacturer_str = self._preset_manufacturer
            model_name = self._preset_type
        elif self._auto_resolved_config:
            manufacturer_str = self._auto_resolved_config.split("-", 1)[0]
            model_name = self._auto_resolved_config
        else:
            if self._auto_resolve_pending:
                nm.post_notification(
                    "Still resolving robot configuration, please wait...",
                    duration=4.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return
            options = self._manufacturer_options()
            manufacturers = list(options)
            types = self._filtered_types()
            if (
                self._selected_manufacturer_idx <= 0
                or self._selected_type_idx <= 0
                or not types
            ):
                nm.post_notification(
                    "Select a manufacturer and controller type first.",
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return
            manufacturer_label = manufacturers[
                min(self._selected_manufacturer_idx - 1, len(manufacturers) - 1)
            ]
            manufacturer_str = options[manufacturer_label]
            model_name = types[min(self._selected_type_idx - 1, len(types) - 1)]

        # Unassigned articulations are discovered via ArticulationRootAPI and only
        # carry MotionGroupAPI once configured, so apply it now to make the TCP
        # lookup (which requires the schema) succeed.
        SchemaUtils.ensure_motion_group_api(prim)

        robot_tcp = SchemaUtils.find_motion_group_tcp(prim)
        tcps = []
        if robot_tcp:
            flange_path = robot_tcp.GetPath().pathString
            tcps = collect_tcps_from_prim(prim, flange_path)
        else:
            carb.log_warn("No TCP found. Skipping TCP creation.")

        prim_pose_world = PrimUtils.get_prim_pose(prim.GetPath())
        cell = self._cells[self._selected_cell_idx]

        try:
            new_motion_group_id = await create_virtual_controller(
                instance=self._instance,
                cell=cell,
                controller_name=self._controller_name,
                model_name=model_name,
                manufacturer_str=manufacturer_str,
                mounting_position=list(prim_pose_world.pose[:3]),
                mounting_orientation=list(prim_pose_world.pose[3:]),
                mounting_coordinate_system=prim.GetPath().pathString,
                define_mounting=self._mounting_in_nova,
                tcps=tcps,
                on_progress=self._on_progress,
            )
        except ControllerAlreadyExistsError as e:
            carb.log_warn(str(e))
            nm.post_notification(
                f"A controller named '{self._controller_name}' already exists. "
                "Choose a different name.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return
        except Exception as e:
            carb.log_error(f"Failed to create virtual controller: {e}")
            nm.post_notification(
                f"Failed to create virtual controller: {e}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        nm.post_notification(
            f"Virtual controller '{self._controller_name}' created successfully.",
            duration=3.0,
            status=nm.NotificationStatus.INFO,
        )

        self._connect_motion_group(cell, new_motion_group_id)

    def _connect_motion_group(self, cell: str, motion_group_name: str):
        self._connect_task = run_coroutine(
            self._connect_with_retry(cell, motion_group_name)
        )

    async def _connect_with_retry(
        self,
        cell: str,
        motion_group_name: str,
        attempts: int = _CONNECT_RETRIES,
        delay: float = _CONNECT_RETRY_DELAY_S,
    ):
        controller_data = NOVAControllerData(name=self._controller_name, cell_name=cell)
        last_message = ""

        for attempt in range(attempts):
            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()

            def _cb(success: bool, message: str = "", _future=future):
                if not _future.done():
                    _future.set_result((success, message))

            self._instances_service.create_motion_group_from_nova(
                instance=self._instance,
                controller=controller_data,
                motion_group_name=motion_group_name,
                prim_path=self._prim_path,
                use_external_joint_stream=False,
                callback=_cb,
            )

            success, last_message = await future
            if success:
                if self._on_created:
                    self._on_created()
                return

            if attempt < attempts - 1:
                await asyncio.sleep(delay)

        nm.post_notification(
            last_message or "Failed to connect motion group.",
            duration=5.0,
            status=nm.NotificationStatus.WARNING,
        )
        if self._on_created:
            self._on_created()

    def _cancel_ui_tasks(self):
        """Cancel the lookups that only feed this row's widgets.

        Leaves ``_create_task`` / ``_connect_task`` running: unrelated events
        rebuild this row while they are in flight, and cancelling them left the
        virtual controller created server-side but the prim unconfigured.
        """
        if self._cells_task is not None:
            self._cells_task.cancel()
            self._cells_task = None
        if self._configs_task is not None:
            self._configs_task.cancel()
            self._configs_task = None

    def _cancel_all_tasks(self):
        """Also abort the create/connect sequence - only on explicit user cancel."""
        self._cancel_ui_tasks()
        if self._create_task is not None:
            self._create_task.cancel()
            self._create_task = None
        if self._connect_task is not None:
            self._connect_task.cancel()
            self._connect_task = None

    def destroy(self):
        # The type icon keeps running its kinematics lookup independently, so
        # cancel it here (not in _cancel_ui_tasks, which runs on mere collapse
        # where the icon must persist in the collapsed header).
        if self._type_icon is not None:
            self._type_icon.destroy()
            self._type_icon = None
        self._instance_combo_sub = None
        self._controller_combo_sub = None
        self._cell_combo_sub = None
        self._manufacturer_combo_sub = None
        self._type_combo_sub = None
        self._mg_widget = None
        self._cancel_ui_tasks()
