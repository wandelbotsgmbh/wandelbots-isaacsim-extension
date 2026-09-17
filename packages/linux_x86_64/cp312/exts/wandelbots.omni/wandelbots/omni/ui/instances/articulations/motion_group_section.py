import weakref
from typing import Callable, Optional

import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine
from omni.kit.window.filepicker import FilePickerDialog

from wandelbots.omni.instances.events import (
    push_ui_busy_changed,
    subscribe_to_motion_group_connection_changed,
)
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import (
    NOVAControllerData,
    NOVAInstance,
    NOVAMotionGroupData,
)
from wandelbots.omni.instances.stage_discovery import list_motion_group_prim_suggestions
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.create_context_menu.robot.robot_download import (
    download_and_add_robot,
)
from wandelbots.omni.ui.instances.models.motion_group_enabled_model import (
    MotionGroupEnabledModel,
)
from wandelbots.omni.ui.instances.articulations.motion_group_widget import (
    MotionGroupWidget,
)
from wandelbots.omni.ui.instances.articulations.virtual_controller_service import (
    create_and_connect_virtual_controller,
    notify_warning,
    resolve_robot_configuration,
)
from wandelbots.omni.ui.widgets import CollapsibleSection
from wandelbots.omni.ui.widgets.progress_status_bar import ProgressStatusBar
from wandelbots.omni.ui.wb_theme import (
    BUTTON_HEIGHT,
    BUTTON_PRIMARY_STYLE,
    TOOLTIP_RESET,
    build_tooltip,
)
from wandelbots.omni.ui.widgets.switch import Switch, WARNING_SWITCH_STYLE
from wandelbots.omni.ui.widgets.type_icon import TypeIcon
from wandelbots.omni.utils.locations import join_location, parent_location


class MotionGroupSection(ui.VStack):
    """Collapsible section for a single NOVA motion group.

    Renders a ``CollapsibleSection`` with an optional quick-connect
    button and enable/disable switch in the header, and a full
    ``MotionGroupWidget`` in the body.
    """

    def __init__(
        self,
        instances_service: NOVAInstancesService,
        instance: NOVAInstance,
        controller: NOVAControllerData,
        motion_group: NOVAMotionGroupData,
        on_connection_changed: Optional[Callable] = None,
        **kwargs,
    ):
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._instances_service = instances_service
        self._instance = instance
        self._controller = controller
        self._motion_group = motion_group
        self._on_connection_changed = on_connection_changed
        self._connect_btn_container: Optional[ui.VStack] = None
        self._download_btn_container: Optional[ui.VStack] = None
        self._switch_container: Optional[ui.VStack] = None
        self._create_button: Optional[ui.Button] = None
        self._progress_container: Optional[ui.VStack] = None
        self._progress: Optional[ProgressStatusBar] = None
        self._create_task = None
        self._connected_prim_path: Optional[str] = None
        # The connected prim, or the one this section offers to connect. Only
        # used to route global connection events.
        self._referenced_prim_path: Optional[str] = None
        self._type_icon: Optional[TypeIcon] = None
        self._file_picker: Optional[FilePickerDialog] = None

        self._event_sub = subscribe_to_motion_group_connection_changed(
            lambda payload, weak_self=weakref.ref(self): (
                weak_self()._on_global_connection_changed(payload)
                if weak_self()
                else None
            )
        )

        self._build()

    def _build(self):
        title = (
            f"{self._motion_group.motion_group_model_name} ({self._motion_group.name})"
        )

        # Match by cell/controller/motion group (not host) so a robot connected via
        # another instance still renders as connected here when this instance exposes
        # the same controller.
        connected = self._instances_service.find_connected_motion_group_by(
            cell=self._controller.cell_name,
            controller=self._controller.name,
            motion_group=self._motion_group.name,
        )
        if len(connected) > 1:
            carb.log_warn(
                f"Multiple connected motion groups found for "
                f"{self._motion_group.name} in controller "
                f"{self._controller.name} on instance "
                f"{self._instance.display_name}"
            )

        # Only a configuration this instance owns counts as connected here. A
        # foreign one - same cell/controller/motion group on another host - stays
        # connectable so one click migrates it.
        owned: list = []
        foreign: list = []
        for config in connected:
            (owned if self._instance.owns_connection(config) else foreign).append(
                config
            )

        prim_path = owned[0].prim_path if owned else None
        self._connected_prim_path = prim_path

        can_connect = self._instance.is_reachable
        foreign_host = ""
        matched = None
        if can_connect and not prim_path:
            if foreign:
                matched = foreign[0].prim_path
                foreign_host = foreign[0].motion_stream_configuration.host
            else:
                matched = self._find_matching_articulation()
        connect_fn = self._make_connect_fn(matched) if matched else None
        self._referenced_prim_path = prim_path or matched
        controller_missing = prim_path is not None and self._is_controller_missing()
        self._create_button = None
        self._progress_container = None
        self._progress = None

        with self:
            self._section = CollapsibleSection(
                title=title,
                collapsed=True,
                title_color=NOVAColor.TEXT_PRIMARY_CONTRAST,
                build_leading_fn=lambda sec, _self=self: _self._build_type_icon(),
                build_header_fn=lambda sec, _prim=prim_path, _fn=connect_fn, _matched=matched, _foreign=foreign_host, _missing=controller_missing, _self=self: (
                    _self._build_header(_prim, _fn, _matched, _foreign, _missing)
                ),
                on_collapsed_changed=lambda collapsed, _self=self: (
                    _self._on_collapsed_changed(collapsed)
                ),
            )
            with self._section.body:
                # Breathing room between the section header and the first
                # (Articulation) row inside the motion group widget.
                ui.Spacer(height=8)
                if controller_missing:
                    self._build_missing_controller_hint()
                self._motion_group_widget = MotionGroupWidget(
                    instances_service=self._instances_service,
                    instance=self._instance,
                    controller=self._controller,
                    motion_group=self._motion_group,
                    matched_prim_path=matched,
                    on_connection_changed=self._on_connection_changed,
                    on_geometry_mismatch=self._on_geometry_mismatch,
                )

    def _build_type_icon(self):
        self._type_icon = TypeIcon(
            self._instance, self._motion_group.motion_group_model_name
        )

    def _on_geometry_mismatch(self, message: str):
        """Mark the section itself, which is collapsed until someone opens it."""
        if self._type_icon is not None:
            self._type_icon.show_warning(message)

    def _build_header(
        self,
        prim_path: Optional[str],
        connect_fn: Optional[Callable],
        matched_prim: Optional[str] = None,
        foreign_host: str = "",
        controller_missing: bool = False,
    ):
        if connect_fn:
            self._connect_btn_container = ui.VStack(width=0)
            with self._connect_btn_container:
                ui.Spacer()
                if foreign_host:
                    # Already configured, just against another host - say so, so
                    # the click reads as "move it here".
                    connect_tooltip = (
                        f"{matched_prim}\nis currently assigned to '{foreign_host}'.\n"
                        f"Connect it to {self._instance.display_name} instead."
                    )
                elif matched_prim:
                    connect_tooltip = f"Connect to:\n{matched_prim}"
                else:
                    connect_tooltip = "Connect to the matching articulation on stage"
                ui.Button(
                    "Connect",
                    width=0,
                    height=BUTTON_HEIGHT,
                    style={**BUTTON_PRIMARY_STYLE, **TOOLTIP_RESET},
                    clicked_fn=lambda: connect_fn(),
                    tooltip_fn=lambda t=connect_tooltip: build_tooltip(t),
                )
                ui.Spacer()

        # Reachable virtual controller with no matching articulation on stage yet:
        # offer to download the matching robot model from this instance.
        if not connect_fn and not prim_path and self._instance.is_reachable:
            self._download_btn_container = ui.VStack(width=0)
            with self._download_btn_container:
                ui.Spacer()
                ui.Button(
                    "Download Robot",
                    width=0,
                    height=BUTTON_HEIGHT,
                    style={**BUTTON_PRIMARY_STYLE, **TOOLTIP_RESET},
                    clicked_fn=lambda _self=self: _self._on_download_robot(),
                    tooltip_fn=lambda: build_tooltip(
                        "Download the matching robot model from this instance"
                    ),
                )
                ui.Spacer()

        if controller_missing:
            self._build_create_controller_button()
            return

        if prim_path:
            # Center the fixed-height switch vertically in the header row,
            # mirroring the connect button wrapper above. A bare Switch (fixed
            # height) would otherwise top-align inside the header HStack. The switch
            # is hidden while expanded, where it moves into the body next to the
            # Disconnect button (see MotionGroupWidget).
            # Reserve the button height: the header shows either a Connect /
            # Download Robot button (BUTTON_HEIGHT) or this switch, and a 17px
            # switch would make the row visibly shorter the moment a motion group
            # gets connected. The buttons set the row height, not the switch.
            self._switch_container = ui.VStack(width=0, height=BUTTON_HEIGHT)
            model = MotionGroupEnabledModel(prim_path)
            with self._switch_container:
                ui.Spacer()
                Switch(
                    height=17,
                    model=model,
                    # An unbacked model (ill-formed stored prim path) never
                    # writes to USD; render the switch disabled instead of a
                    # live no-op.
                    enabled=model.is_backed,
                    style=(
                        WARNING_SWITCH_STYLE
                        if not self._instance.is_reachable
                        else None
                    ),
                    tooltip=(
                        "Toggle motion group for simulation"
                        if model.is_backed
                        else "Unavailable: the stored motion group reference "
                        f"'{prim_path}' is not a valid prim path"
                    ),
                )
                ui.Spacer()

    def _is_controller_missing(self) -> bool:
        # Reachable with its cells loaded, yet the stored controller is not among
        # them: deleted server-side, or never created on this host.
        return (
            self._instance.is_reachable
            and self._instance.cells is not None
            and not self._instance.has_live_motion_group(
                self._controller.cell_name,
                self._controller.name,
                self._motion_group.name,
            )
        )

    def _build_create_controller_button(self):
        with ui.VStack(width=0):
            ui.Spacer()
            self._create_button = ui.Button(
                "Create Virtual Controller",
                width=0,
                height=BUTTON_HEIGHT,
                style={**BUTTON_PRIMARY_STYLE, **TOOLTIP_RESET},
                clicked_fn=lambda _self=self: _self._on_create_virtual_controller(),
                tooltip_fn=lambda _self=self: build_tooltip(
                    f"Controller '{_self._controller.name}' was not found on this "
                    "instance.\nCreate it again and reconnect the articulation."
                ),
            )
            ui.Spacer()

    def _build_missing_controller_hint(self):
        with ui.HStack(height=0):
            ui.Spacer(width=15)
            ui.Label(
                f"Controller '{self._controller.name}' was not found on this "
                "instance. Create it again or disconnect the articulation.",
                width=ui.Fraction(1),
                word_wrap=True,
                style={"color": NOVAColor.WARNING_LIGHT.color},
            )
            ui.Spacer(width=10)
        self._progress = ProgressStatusBar(
            name=self._motion_group.motion_group_model_name
        )
        self._progress_container = ui.VStack(visible=False, height=0, spacing=4)
        with self._progress_container:
            ui.Spacer(height=4)
            with ui.HStack(height=0):
                ui.Spacer(width=15)
                with ui.VStack(height=0, spacing=4):
                    self._progress.build()
                ui.Spacer(width=10)
        ui.Spacer(height=8)

    def _set_creating(self, creating: bool):
        if self._create_button is not None:
            self._create_button.enabled = not creating
            self._create_button.text = (
                "Creating..." if creating else "Create Virtual Controller"
            )
        if self._progress_container is not None:
            self._progress_container.visible = creating
        if self._progress is None:
            return
        if creating:
            self._progress.show(0.0)
            self._progress.set_hint("Creating controller...")
        else:
            self._progress.hide()

    def _on_create_progress(self, value: float, text: str):
        if self._progress is None:
            return
        self._progress.update(value)
        if text:
            self._progress.set_hint(text)

    def _on_create_virtual_controller(self):
        if self._create_task is not None:
            return
        self._create_task = run_coroutine(self._create_virtual_controller_async())

    async def _create_virtual_controller_async(self):
        push_ui_busy_changed(True, "Creating virtual controller in NOVA...")
        self._set_creating(True)
        created = False
        try:
            created = await self._create_and_connect()
        finally:
            self._create_task = None
            push_ui_busy_changed(False)
            self._set_creating(False)
        if created and self._on_connection_changed:
            self._on_connection_changed()

    async def _create_and_connect(self) -> bool:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(self._connected_prim_path) if stage else None
        if prim is None or not prim.IsValid():
            notify_warning("Selected motion group prim is no longer valid.")
            return False
        cell = self._cell_for_new_controller()
        if cell is None:
            notify_warning("No cells available on this instance.")
            return False
        model_name = self._motion_group.motion_group_model_name
        configuration = await resolve_robot_configuration(
            self._instance, prim, model_name
        )
        if configuration is None:
            notify_warning(
                f"Could not resolve the robot configuration for '{model_name}'. "
                "Disconnect the articulation and assign it from the Unassigned "
                "section."
            )
            return False
        manufacturer_str, robot_configuration = configuration
        return await create_and_connect_virtual_controller(
            instances_service=self._instances_service,
            instance=self._instance,
            prim=prim,
            cell=cell,
            controller_name=self._controller.name,
            robot_configuration=robot_configuration,
            manufacturer_str=manufacturer_str,
            define_mounting=True,
            on_progress=self._on_create_progress,
        )

    def _cell_for_new_controller(self) -> Optional[str]:
        # The stored cell when the instance still has it, else its first cell.
        cell_names = [cell.name for cell in self._instance.cells or []]
        if self._controller.cell_name in cell_names:
            return self._controller.cell_name
        return cell_names[0] if cell_names else None

    def _on_collapsed_changed(self, collapsed: bool):
        if self._connect_btn_container:
            self._connect_btn_container.visible = collapsed
        if self._download_btn_container:
            self._download_btn_container.visible = collapsed
        # The enable/disable switch lives in the header while collapsed and moves
        # into the body (next to Disconnect) while expanded.
        if self._switch_container:
            self._switch_container.visible = collapsed

    def _on_download_robot(self):
        model_name = self._motion_group.motion_group_model_name

        def _on_apply(filename: str, path: str, weak_self=weakref.proxy(self)) -> None:
            weak_self._file_picker.hide()
            download_path = join_location(path, filename) if filename else path
            run_coroutine(weak_self._download_robot_async(download_path))

        def _on_cancel(filename: str, path: str, weak_self=weakref.proxy(self)) -> None:
            weak_self._file_picker.hide()

        # URL-aware; a plain "://" check mishandles ``file:`` urls.
        default_dir = parent_location(omni.usd.get_context().get_stage_url())

        self._file_picker = FilePickerDialog(
            "Select location for robot download...",
            apply_button_label="Download Here",
            click_apply_handler=_on_apply,
            click_cancel_handler=_on_cancel,
        )
        self._file_picker.set_filename(f"{model_name}.usd")
        # The modal picker steals input, so the header never receives its hover
        # leave event; reset hover tracking so the highlight doesn't get stranded.
        CollapsibleSection.clear_hover_state()
        self._file_picker.show(default_dir)

    async def _download_robot_async(self, download_path: str):
        prim_path = await download_and_add_robot(
            self._instance,
            self._motion_group.motion_group_model_name,
            download_path,
            resolve_name=True,
        )
        if not prim_path:
            return
        # The new articulation can now be matched/connected, so rebuild.
        if self._on_connection_changed:
            self._on_connection_changed()
        else:
            self.rebuild()

    def _on_global_connection_changed(self, payload):
        """Rebuild this section when the event affects it."""
        event_host = payload.get("host", "")
        event_prim = payload.get("prim_path", "")
        event_mg = payload.get("motion_group", "")

        is_this_section = (
            event_host == self._instance.host and event_mg == self._motion_group.name
        )
        prim_was_stolen = (
            self._referenced_prim_path
            and event_prim == self._referenced_prim_path
            and not is_this_section
        )
        if prim_was_stolen:
            # The articulation was connected to another instance, so it no longer
            # belongs under this one. Rebuild the whole instance (not just this
            # section) so an unreachable instance re-derives its motion groups from
            # the stage and drops this now-migrated one entirely.
            if self._on_connection_changed:
                self._on_connection_changed()
            else:
                self.rebuild()
            return
        if is_this_section:
            self.rebuild()

    def rebuild(self):
        if self._type_icon is not None:
            self._type_icon.destroy()
            self._type_icon = None
        self.clear()
        self._build()

    def destroy(self):
        if self._type_icon is not None:
            self._type_icon.destroy()
            self._type_icon = None
        # Releasing the carb event observer guard unsubscribes it.
        self._event_sub = None

    def _find_matching_articulation(self) -> Optional[str]:
        from wandelbots.omni.manipulators.utils import get_scene_motion_group_prim_paths

        configs = self._instances_service._get_stage_motion_group_configurations()
        articulations = get_scene_motion_group_prim_paths()
        suggestions = list_motion_group_prim_suggestions(
            configs,
            cell=self._controller.cell_name,
            controller=self._controller.name,
            motion_group=self._motion_group.name,
            scene_articulations=articulations,
            motion_group_model_name=self._motion_group.motion_group_model_name,
        )
        if suggestions:
            return suggestions[0]
        return None

    def _make_connect_fn(self, prim_path: str) -> Callable:
        weak_self = weakref.ref(self)

        def _connect():
            self_ref = weak_self()
            if not self_ref:
                return

            def on_complete(success, message=""):
                if not success:
                    # Otherwise a failed quick-connect is silent and the click
                    # looks like it did nothing.
                    carb.log_warn(
                        f"Failed to connect {self_ref._motion_group.name} to "
                        f"{prim_path}: {message}"
                    )
                    nm.post_notification(
                        message or f"Failed to connect {self_ref._motion_group.name}.",
                        duration=5.0,
                        status=nm.NotificationStatus.WARNING,
                    )
                    self_ref.rebuild()
                    return
                if self_ref._on_connection_changed:
                    self_ref._on_connection_changed()

            # No use_external_joint_stream: a prim that is already configured,
            # for instance against another host, keeps its joint stream source.
            self_ref._instances_service.create_motion_group_from_nova(
                instance=self_ref._instance,
                controller=self_ref._controller,
                motion_group_name=self_ref._motion_group.name,
                prim_path=prim_path,
                callback=on_complete,
            )

        return _connect
