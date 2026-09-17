import carb
import omni.ui as ui
import weakref
from omni.kit.async_engine import run_coroutine
from typing import Callable, Optional
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.manipulators.utils import get_scene_motion_group_prim_paths
from wandelbots.omni.manipulators import MotionGroupConfiguration
from wandelbots.omni.instances.models import (
    NOVAInstance,
    NOVAControllerData,
    NOVAMotionGroupData,
)
from wandelbots.omni.instances.stage_discovery import list_motion_group_prim_suggestions
from wandelbots.omni.ui.widgets.articulation_selector import (
    ArticulationSelector,
)
from wandelbots.omni.ui.widgets.connect_button import ConnectButton
from wandelbots.omni.ui.widgets.external_joint_stream_checkbox import (
    ExternalJointStreamCheckbox,
)
from wandelbots.omni.ui.instances.models.motion_group_enabled_model import (
    MotionGroupEnabledModel,
)
from wandelbots.omni.ui.widgets.switch import Switch, WARNING_SWITCH_STYLE
from wandelbots.omni.ui.wb_theme import TOOLTIP_RESET, build_tooltip
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.utils import get_icon
from wandelbots.omni.manipulators.geometry_check import check_motion_group_geometry


class MotionGroupWidget(ui.VStack):
    """Self-contained widget for a single motion-group row.

    Composes :class:`ArticulationSelector`, :class:`ConnectButton`, and
    :class:`ExternalJointStreamCheckbox` into a coherent unit that can
    connect / disconnect a NOVA motion group to an Isaac Sim articulation.
    """

    def __init__(
        self,
        instances_service: NOVAInstancesService,
        instance: NOVAInstance,
        controller: NOVAControllerData,
        motion_group: NOVAMotionGroupData,
        on_connection_changed: Optional[Callable] = None,
        matched_prim_path: Optional[str] = None,
        fixed_prim_path: Optional[str] = None,
        on_geometry_mismatch: Optional[Callable[[str], None]] = None,
        **kwargs,
    ):
        kwargs.setdefault("spacing", 10)
        super().__init__(**kwargs)

        self._instances_service = instances_service
        self._instance = instance
        self._controller = controller
        self._motion_group = motion_group
        self._on_connection_changed = on_connection_changed
        # Called with the warning text when the geometry check fails, so the
        # owner can mark the collapsed row as well.
        self._on_geometry_mismatch = on_geometry_mismatch
        self._matched_prim_path = matched_prim_path
        # When set, the articulation is fixed to this prim (e.g. an unassigned row
        # connecting to an existing controller): the selector is locked read-only.
        self._fixed_prim_path = fixed_prim_path

        self._selector: Optional[ArticulationSelector] = None
        self._connect_btn: Optional[ConnectButton] = None
        self._joint_stream_cb: Optional[ExternalJointStreamCheckbox] = None
        self._geometry_warning: Optional[ui.Frame] = None

        self._build()

    @property
    def motion_group_config(self) -> Optional[MotionGroupConfiguration]:
        # Match by cell/controller/motion group (not host) so the connected config is
        # found even when the robot was connected while pointing at another instance.
        connected = self._instances_service.find_connected_motion_group_by(
            controller=self._controller.name,
            cell=self._controller.cell_name,
            motion_group=self._motion_group.name,
        )
        if len(connected) > 1:
            carb.log_warn(
                f"Multiple connected motion groups found, using the first one. {connected}"
            )
        return connected[0] if connected else None

    @property
    def use_external_joint_stream(self) -> bool:
        if self._joint_stream_cb:
            return self._joint_stream_cb.use_external_joint_stream
        return False

    def rebuild(self):
        """Tear down and re-create all child widgets."""
        self.clear()
        self._build()

    def _build(self):
        articulations = get_scene_motion_group_prim_paths()
        config = self.motion_group_config
        # A config matched by name may belong to another instance (e.g. the robot is
        # still connected to an outdated host). Only treat it as connected *here* when
        # its host matches this instance; otherwise it is a foreign connection that
        # the user can migrate by connecting it to this (live) instance.
        local_connection = self._is_local_connection(config)

        with self:
            with ui.VStack(alignment=ui.Alignment.LEFT, spacing=10):
                if articulations:
                    self._selector = self._create_articulation_selector(
                        articulations, config
                    )
                    if local_connection:
                        self._joint_stream_cb = ExternalJointStreamCheckbox(config)
                    # The enable/disable switch gets its own labeled row beneath the
                    # "Sync with simulation" checkbox whenever the motion group is
                    # connected (has a prim path).
                    if config and config.prim_path:
                        self._build_switch_row(config.prim_path)
                    # Show the button when the instance is reachable (connect or
                    # disconnect) or when a connection already exists. Disconnect is a
                    # local USD operation, so it must stay available on an unreachable
                    # instance to let the user free the articulation and reassign it.
                    if self._instance.is_reachable or local_connection:
                        self._connect_btn = self._create_connect_button(
                            config, local_connection
                        )
                    # Hidden, not zero-height: an empty frame expands to fill
                    # the stack and a pinned height clips a wrapped message.
                    self._geometry_warning = ui.Frame(visible=False)
                    if local_connection and config:
                        self._start_geometry_check(config)
                else:
                    with ui.HStack(height=25):
                        ui.Spacer(width=15)
                        ui.Label("Articulation:", width=150)
                        ui.Label(
                            "No articulation found in the scene.",
                            width=250,
                            height=30,
                        )
                        ui.Spacer()
                ui.Spacer(height=5)

    def _start_geometry_check(self, config: MotionGroupConfiguration) -> None:
        """Warn when the scene robot is not the robot NOVA has configured.

        Runs in the background: the panel must not wait on a round trip. The
        frame is pinned and self held weakly, because the panel is rebuilt on
        every connection change and a late result describes the old config.
        """
        widget_ref = weakref.ref(self)
        frame = self._geometry_warning

        async def _check():
            try:
                result = await check_motion_group_geometry(config)
                if result is None or result.matches:
                    return
                carb.log_warn(result.warning())
                widget = widget_ref()
                if widget is not None and widget._geometry_warning is frame:
                    widget._show_geometry_warning(frame, result.warning())
                    if widget._on_geometry_mismatch:
                        widget._on_geometry_mismatch(result.warning())
            except Exception as exc:
                # A background task, so a failure here has nowhere to surface.
                carb.log_warn(f"Robot geometry check failed: {exc}")

        run_coroutine(_check())

    def _show_geometry_warning(self, frame: ui.Frame, message: str) -> None:
        frame.clear()
        with frame:
            # Same 15 px indent and 10 px right margin as the rows above. The
            # icon hangs from the first line because the message can wrap.
            with ui.HStack(height=0, spacing=6):
                ui.Spacer(width=15)
                with ui.VStack(width=16, height=0):
                    ui.Image(get_icon("warning.svg"), width=16, height=16)
                    ui.Spacer()
                ui.Label(
                    message,
                    style={"color": NOVAColor.WARNING_LIGHT.color},
                    word_wrap=True,
                    alignment=ui.Alignment.LEFT_TOP,
                )
                ui.Spacer(width=10)
        frame.visible = True

    def _is_local_connection(self, config: Optional[MotionGroupConfiguration]) -> bool:
        # Same check as the collapsed header, so the two cannot disagree.
        return self._instance.owns_connection(config)

    def _create_articulation_selector(
        self,
        articulations: list[str],
        config: Optional[MotionGroupConfiguration],
    ) -> ArticulationSelector:
        initial = self._fixed_prim_path or self._resolve_initial_selection(
            articulations
        )

        def _on_selection_changed(prim_path: Optional[str]):
            if prim_path:
                self._instances_service.set_selected_articulation(prim_path, prim_path)
            if self._connect_btn:
                self._connect_btn.prim_path = prim_path
                self._connect_btn.update_state(
                    is_connected=False, can_connect=prim_path is not None
                )
                self._connect_btn.set_error("")

        return ArticulationSelector(
            articulations=articulations,
            on_selection_changed=_on_selection_changed,
            connected_prim_path=config.prim_path if config else None,
            initial_selection=initial,
            read_only=not self._instance.is_reachable,
        )

    def _create_connect_button(
        self,
        config: Optional[MotionGroupConfiguration],
        is_local_connection: bool,
    ) -> ConnectButton:
        def _on_changed():
            self.rebuild()
            if self._on_connection_changed:
                self._on_connection_changed()

        btn = ConnectButton(
            instances_service=self._instances_service,
            instance=self._instance,
            controller=self._controller,
            motion_group=self._motion_group,
            config=config,
            on_connection_changed=_on_changed,
        )

        initial_prim = self._selector.selected if self._selector else None
        btn.prim_path = initial_prim
        # A foreign connection (config set but pointing at another host) is shown as
        # connectable: clicking Connect reassigns the prim to this instance, which
        # overwrites its config and removes it from the previous instance.
        btn.update_state(
            is_connected=is_local_connection,
            can_connect=initial_prim is not None,
        )
        return btn

    def _build_enabled_switch(self, prim_path: str):
        # Vertically center the fixed-height switch so it aligns with the button.
        model = MotionGroupEnabledModel(prim_path)
        with ui.VStack(width=0):
            ui.Spacer()
            Switch(
                height=17,
                model=model,
                # An unbacked model (ill-formed stored prim path) never writes
                # to USD; render the switch disabled instead of a live no-op.
                enabled=model.is_backed,
                style=(
                    WARNING_SWITCH_STYLE if not self._instance.is_reachable else None
                ),
                tooltip=(
                    "Toggle motion group for simulation"
                    if model.is_backed
                    else "Unavailable: the stored motion group reference "
                    f"'{prim_path}' is not a valid prim path"
                ),
            )
            ui.Spacer()

    def _build_switch_row(self, prim_path: str):
        # Labeled row that places the enable/disable switch beneath the
        # "Sync with simulation" checkbox, mirroring that row's label layout.
        with ui.HStack(height=26):
            ui.Spacer(width=15)
            ui.Label(
                "Include in simulation:",
                width=150,
                alignment=ui.Alignment.LEFT_CENTER,
                style=TOOLTIP_RESET,
                tooltip_fn=lambda: build_tooltip(
                    "Activate or deactivate this motion group for simulation."
                ),
            )
            ui.Spacer()
            self._build_enabled_switch(prim_path)
            ui.Spacer(width=10)

    def _resolve_initial_selection(self, articulations: list[str]) -> Optional[str]:
        stage_configs = self._instances_service._get_stage_motion_group_configurations()
        suggestions = list_motion_group_prim_suggestions(
            stage_configs,
            cell=self._controller.cell_name,
            controller=self._controller.name,
            motion_group=self._motion_group.name,
            scene_articulations=articulations,
            motion_group_model_name=self._motion_group.motion_group_model_name,
        )
        if suggestions and suggestions[0] in articulations:
            return suggestions[0]
        stored = self._instances_service.get_selected_articulation(self)
        if stored:
            return stored
        return None
