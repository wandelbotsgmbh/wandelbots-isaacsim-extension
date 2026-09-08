from __future__ import annotations

import asyncio
import weakref

import carb
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine

from wandelbots.omni.instances.events import subscribe_to_ui_busy_changed
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import NOVACustomInstance
from wandelbots.omni.ui.base import BaseUIBuilder
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.wb_theme import (
    BUTTON_HEIGHT,
    BUTTON_PRIMARY_STYLE,
    COMBOBOX_STYLE,
    DIVIDER_GAP_ABOVE,
    DIVIDER_GAP_BELOW,
    DIVIDER_INSET_NARROW,
    FIELD_STYLE,
    FORM_FIELD_WIDTH,
    FORM_HEADER_GAP,
    FORM_SIDE_MARGIN,
    HEADER_LABEL_STYLE,
    SECTION_EDGE_INSET,
    SECTION_GAP,
    SECTION_NEST_INSET,
    SPACING_MD,
    TOOLTIP_RESET,
    build_tooltip,
)
from wandelbots.omni.ui.utils import defer_call, weak_cb
from wandelbots.omni.utils.auth import get_auth_configs

from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.widgets.section_divider import section_divider

from wandelbots.omni.ui.instances.articulations.unassigned_articulations import (
    UnassignedArticulations,
)
from wandelbots.omni.ui.instances.groups.cloud_instances import CloudInstances
from wandelbots.omni.ui.instances.groups.custom_instances import CustomInstances
from wandelbots.omni.ui.widgets.icon_button import IconButton
from wandelbots.omni.ui.widgets.sign_in_widget import SignInWidget


# Instances finish loading their cells at slightly different times during a
# refresh, each firing a connection-changed callback. Collapse that burst into a
# single unassigned-section rebuild instead of one rebuild per instance.
_UNASSIGNED_REFRESH_DEBOUNCE_S = 0.4


class NOVAInstanceListUIBuilder(BaseUIBuilder):
    """Level 1 main window for the Wandelbots NOVA Instances panel."""

    def __init__(self):
        super().__init__(
            title="Connect to NOVA",
            width=600,
            height=600,
        )
        self._instances_service = NOVAInstancesService()
        self._custom_instances: list[NOVACustomInstance] = []
        self._stage_only_hosts: set[str] = set()
        self._cloud_instances: dict[str, CloudInstances] = {}
        self._cloud_instances_container: ui.VStack | None = None
        self._custom_instances_container: ui.VStack | None = None
        self._add_instance_container: ui.VStack | None = None
        self._assigned_container: ui.VStack | None = None
        self._unassigned_container: ui.VStack | None = None
        self._assigned_divider: ui.VStack | None = None
        self._unassigned_divider: ui.VStack | None = None
        self._instances_divider: ui.VStack | None = None
        self._unassigned: UnassignedArticulations | None = None
        self._unassigned_refresh_task: asyncio.Task | None = None
        # Rebuild skipped while the panel was busy; _on_ui_busy_changed retries.
        self._unassigned_refresh_pending = False
        self._cloud_load_task: asyncio.Task | None = None
        # False while the async cloud load for the current refresh is pending.
        # The instance containers stay empty until it completes so every
        # InstanceWidget (and the cell fetch its constructor kicks off) is built
        # exactly once per refresh instead of once before and once after the
        # cloud data arrives.
        self._cloud_data_loaded = False
        self._custom_instances_group: CustomInstances | None = None
        # 0 = none (neutral default), 1 = Cloud, 2 = Custom; selects which input
        # the combined add-instance section shows below the type picker.
        self._instance_type_idx = 0
        self._instance_input_frame: ui.Frame | None = None
        self._host_input: ui.StringField | None = None
        self._host_error_message = ""

        # Busy state: while a blocking operation runs (e.g. creating a virtual
        # controller) the content is disabled so no input reaches it until the
        # operation finishes.
        self._busy = False
        self._content_body: ui.VStack | None = None
        self._busy_sub = subscribe_to_ui_busy_changed(
            lambda payload, weak_self=weakref.ref(self): (
                weak_self()._on_ui_busy_changed(payload) if weak_self() else None
            )
        )

        # The articulation widgets (and their USD-backed models) are bound to the
        # stage they were built from. Rebuild the sections when a new stage opens,
        # otherwise the window keeps a widget tree full of expired-stage models.
        self._stage_event_sub = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(
                lambda event, weak_self=weakref.ref(self): (
                    weak_self()._on_stage_event(event) if weak_self() else None
                )
            )
        )

    def build_ui(self):
        self._load_instances_data()

        # A flat ``background_color`` on the frame would cascade down and override
        # the scoped {"Button": {...}} styles on descendants; a unique style type
        # name targets the frame alone. Zeroing the window padding instead would
        # let content cover the resize grip and break window resizing.
        self._window.frame.style_type_name_override = "RootFrame"
        self._window.frame.style = {
            "RootFrame": {"background_color": NOVAColor.LAYER_BASE.color},
        }
        with self._window.frame:
            with ui.ScrollingFrame(
                vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                width=ui.Percent(100),
                height=ui.Percent(100),
                style={
                    "ScrollingFrame": {
                        "background_color": NOVAColor.LAYER_BASE.color,
                    },
                },
            ):
                # Disabled while busy: ``enabled=False`` on this container blocks
                # input to all of its children, freezing the window during a
                # blocking operation (e.g. creating a virtual controller).
                self._content_body = ui.VStack(spacing=SECTION_GAP)
                with self._content_body:
                    self._header_container = ui.VStack(height=0)
                    # The combined add-instance section is inset to line up with
                    # the Cloud Instances card (nested two levels deep), and
                    # right-inset to match that card's border.
                    with ui.HStack(height=0):
                        ui.Spacer(width=SECTION_NEST_INSET)
                        self._add_instance_container = ui.VStack(height=0)
                        ui.Spacer(width=SECTION_EDGE_INSET)

                    self._assigned_divider = self._build_divider()
                    self._assigned_container = ui.VStack(spacing=10, height=0)
                    self._unassigned_divider = self._build_divider()
                    self._unassigned_container = ui.VStack(spacing=10, height=0)
                    ui.Spacer()

        self._display_header()
        self._display_instances()

    def _on_ui_busy_changed(self, payload):
        busy = bool(payload.get("busy", False))
        was_busy = self._busy
        self._busy = busy
        # Gates standard widgets (fields, comboboxes, plain buttons). Custom
        # widgets (IconButton, CollapsibleSection) ignore ``enabled`` and instead
        # consult the global busy gate set in ``push_ui_busy_changed``.
        if self._content_body:
            self._content_body.enabled = not busy
        if was_busy and not busy and self._unassigned_refresh_pending:
            self._unassigned_refresh_pending = False
            self._on_connection_changed()

    def _on_stage_event(self, event):
        if event.type == int(omni.usd.StageEventType.OPENED):
            # Deferred: the event fires while the stage is still settling, and
            # _refresh_data reads motion-group prims from it.
            defer_call(self._refresh_data)

    def _cleanup(self):
        # Tear down the long-lived async work this window owns; without this,
        # closing the panel leaves the cloud load, the debounced unassigned
        # refresh, and the rows' type-icon lookups running against a dead
        # widget tree.
        if self._cloud_load_task is not None:
            self._cloud_load_task.cancel()
            self._cloud_load_task = None
        if self._unassigned_refresh_task is not None:
            self._unassigned_refresh_task.cancel()
            self._unassigned_refresh_task = None
        if self._unassigned is not None:
            self._unassigned.destroy()
            self._unassigned = None
        self._busy_sub = None
        self._stage_event_sub = None
        super()._cleanup()

    def _build_divider(self):
        # The enclosing layout VStack already inserts SECTION_GAP between siblings,
        # so subtract it to land the visible gap at exactly DIVIDER_GAP_ABOVE/BELOW.
        divider = ui.VStack(height=0)
        with divider:
            ui.Spacer(height=DIVIDER_GAP_ABOVE - SECTION_GAP)
            section_divider(thickness=2)
            ui.Spacer(height=DIVIDER_GAP_BELOW - SECTION_GAP)
        return divider

    def _display_header(self):
        self._header_container.clear()
        with self._header_container:
            with ui.VStack(spacing=0):
                ui.Spacer(height=8)
                # Trailing Spacer(3) lands the refresh icon's right edge on the
                # Cloud Instances card border (nested two levels in from the edge).
                with ui.HStack(height=20, spacing=0):
                    ui.Spacer(width=5)
                    ui.Label("Wandelbots NOVA Instances", style=HEADER_LABEL_STYLE)
                    ui.Spacer()
                    IconButton(
                        icon="refresh.svg",
                        tooltip="Click to refresh instance data",
                        clicked_fn=weak_cb(self, "_refresh_data"),
                    )
                    ui.Spacer(width=3)

    def _display_add_instance(self):
        if self._add_instance_container is None:
            return
        self._add_instance_container.clear()
        with self._add_instance_container:
            section = CollapsibleSection(
                title="Sign in to NOVA | Connect to Instance",
                collapsed=True,
                header_color=NOVAColor.SURFACE_OVERLAY,
                header_hover_color=NOVAColor.SURFACE_OVERLAY_HOVER,
                title_color=NOVAColor.TEXT_PRIMARY_CONTRAST,
            )
            with section.body:
                ui.Spacer(height=FORM_HEADER_GAP)
                self._build_add_instance_form()
            # Extra breathing room below the add-instance section before the
            # first section divider.
            ui.Spacer(height=SPACING_MD)

    def _build_add_instance_form(self):
        with ui.VStack(spacing=8):
            with ui.HStack(height=BUTTON_HEIGHT, spacing=0):
                ui.Spacer(width=FORM_SIDE_MARGIN)
                ui.Label(
                    "Instance Type",
                    width=ui.Fraction(1),
                    alignment=ui.Alignment.LEFT_CENTER,
                )
                with ui.VStack(width=FORM_FIELD_WIDTH):
                    ui.Spacer()
                    self._instance_type_model = ui.ComboBox(
                        self._instance_type_idx,
                        "Please select instance type...",
                        "Cloud",
                        "Custom",
                        height=20,
                        style=COMBOBOX_STYLE,
                        tooltip_fn=lambda: build_tooltip(
                            "Choose whether to add a cloud or a custom NOVA instance."
                        ),
                    ).model
                    ui.Spacer()
                ui.Spacer(width=FORM_SIDE_MARGIN)
            self._instance_type_model.add_item_changed_fn(
                weak_cb(self, "_on_instance_type_changed")
            )
            self._instance_input_frame = ui.Frame(height=0)
            # Uniform bottom margin so the section is equally tall regardless of
            # which (or no) input is shown below the type picker.
            ui.Spacer(height=10)
        self._build_instance_input()

    def _on_instance_type_changed(self, model, _item=None):
        index = model.get_item_value_model().as_int
        if index == self._instance_type_idx:
            return
        self._instance_type_idx = index
        self._build_instance_input()

    def _build_instance_input(self):
        if self._instance_input_frame is None:
            return
        self._instance_input_frame.clear()
        with self._instance_input_frame:
            if self._instance_type_idx == 1:
                self._build_cloud_input()
            elif self._instance_type_idx == 2:
                self._build_custom_input()

    def _build_cloud_input(self):
        any_not_signed_in = any(
            not self._instances_service.is_signed_in(auth_config_id)
            for auth_config_id in self._cloud_instances.keys()
        )
        if not any_not_signed_in:
            with ui.HStack(height=BUTTON_HEIGHT):
                ui.Spacer(width=15)
                ui.Label(
                    "All cloud environments are already signed in.",
                    alignment=ui.Alignment.LEFT_CENTER,
                    style={"color": NOVAColor.TEXT_SECONDARY.color},
                )
            return
        SignInWidget(self._instances_service, weak_cb(self, "_on_sign_in"))

    def _build_custom_input(self):
        with ui.VStack(spacing=5):
            with ui.HStack(spacing=0, height=BUTTON_HEIGHT):
                ui.Spacer(width=FORM_SIDE_MARGIN)
                ui.Label(
                    "Host",
                    width=ui.Fraction(1),
                    alignment=ui.Alignment.LEFT_CENTER,
                )
                # A StringField renders top-aligned and stretches, so wrap it in a
                # VStack with spacers to center it vertically. Fixed width matches
                # the combos; the label stretches so its right border lines up.
                with ui.VStack(width=FORM_FIELD_WIDTH):
                    ui.Spacer()
                    self._host_input = ui.StringField(
                        height=24,
                        placeholder="e.g., https://172.31.10.110",
                        style={**FIELD_STYLE, **TOOLTIP_RESET},
                        tooltip_fn=lambda: build_tooltip(
                            "Network address of the NOVA instance to add."
                        ),
                    )
                    ui.Spacer()
                self._host_input.model.add_end_edit_fn(
                    weak_cb(self, "_on_host_submitted")
                )
                ui.Spacer(width=FORM_SIDE_MARGIN)
            if self._host_error_message:
                with ui.HStack():
                    ui.Spacer(width=FORM_SIDE_MARGIN)
                    ui.Label(
                        self._host_error_message,
                        style={"color": NOVAColor.ERROR_MAIN.color},
                    )
            # Action button on its own row beneath the field, right-aligned to the
            # same margin as the field/combo right borders.
            with ui.HStack(height=BUTTON_HEIGHT):
                ui.Spacer()
                ui.Button(
                    "Connect to Instance",
                    width=0,
                    height=BUTTON_HEIGHT,
                    style={**BUTTON_PRIMARY_STYLE, **TOOLTIP_RESET},
                    clicked_fn=weak_cb(self, "_on_add_custom_instance"),
                    tooltip_fn=lambda: build_tooltip(
                        "Add this NOVA instance to the list."
                    ),
                )
                ui.Spacer(width=FORM_SIDE_MARGIN)

    def _display_instances(self):
        try:
            defer_call(self._display_add_instance)
            defer_call(self._display_assigned)
            defer_call(self._display_unassigned)
        except Exception as e:
            carb.log_error(f"Error displaying instances: {e}")

    def _display_assigned(self):
        # One global "Available Instances" section lists every cloud and custom
        # instance in a single flat list (no per-type subgroups). The group
        # containers are (re)created here so a refresh rebuilds them without
        # touching the surrounding window layout.
        if self._assigned_container is None:
            return
        self._assigned_container.clear()
        with self._assigned_container:
            section = CollapsibleSection(
                title="Added Instances",
                collapsed=False,
                header_color=NOVAColor.LAYER_BASE,
                header_hover_color=NOVAColor.SURFACE_OVERLAY,
                title_color=NOVAColor.TEXT_PRIMARY_CONTRAST,
                build_header_fn=lambda sec, ws=weakref.ref(self): (
                    ws()._build_cloud_instances_header() if ws() else None
                ),
            )
            with section.body:
                # spacing=0: the narrow divider owns its own symmetric 10px gaps
                # (see below), so the outer stack must not add extra spacing.
                with ui.VStack(spacing=0, height=0):
                    self._cloud_instances_container = ui.VStack(spacing=1, height=0)
                    # A narrow, subordinate divider visually separates the cloud
                    # provider groups from the custom instances. Toggled in
                    # _update_section_visibility so it only shows when both exist.
                    self._instances_divider = ui.VStack(height=0)
                    with self._instances_divider:
                        # Own both gaps explicitly (10px above and below the line)
                        # so the divider stays vertically centered regardless of
                        # single- vs multi-provider cloud layout.
                        ui.Spacer(height=10)
                        section_divider(inset=DIVIDER_INSET_NARROW)
                        ui.Spacer(height=10)
                    self._custom_instances_container = ui.VStack(spacing=10, height=0)
        # Custom/on-prem instances come from the local store, so they render
        # immediately - a slow or failing cloud portal fetch must not block
        # local-instance workflows. Only the cloud widgets wait for the async
        # portal fetch (building them earlier would construct everything twice;
        # each InstanceWidget also starts its own cell fetch on construction).
        # _load_cloud_instances_async fills the cloud container on completion
        # and rebuilds the custom list only if orphan discovery changed it.
        self._display_custom_instances()
        if self._cloud_data_loaded:
            self._display_cloud_instances()

    def _display_custom_instances(self):
        self._custom_instances_container.clear()
        with self._custom_instances_container:
            self._custom_instances_group = CustomInstances(
                instances=self._custom_instances,
                instances_service=self._instances_service,
                orphan_hosts=self._stage_only_hosts,
                on_remove=weak_cb(self, "_on_remove_custom_instance"),
                on_refresh=weak_cb(self, "_refresh_data"),
                on_connection_changed=weak_cb(self, "_on_connection_changed"),
            )

    def _display_cloud_instances(self):
        self._cloud_instances_container.clear()
        with self._cloud_instances_container:
            for container in self._cloud_instances.values():
                container.build_ui()

    def _build_cloud_instances_header(self):
        signed_in_ids = [
            auth_config_id
            for auth_config_id in self._cloud_instances.keys()
            if self._instances_service.is_signed_in(auth_config_id)
        ]
        if len(signed_in_ids) != 1:
            return
        # With multiple providers each gets its own section sign-out icon, so the
        # top-level header icon would be a duplicate.
        if len(self._cloud_instances) > 1:
            return
        auth_config_id = signed_in_ids[0]
        IconButton(
            icon="sign_out.svg",
            tooltip="Click to sign out of your account.",
            clicked_fn=weak_cb(self, "_on_sign_out", auth_config_id),
        )

    def _display_unassigned(self):
        if self._unassigned_container is None:
            return
        # Cancel the previous section's in-flight type-icon lookups before
        # replacing it: clearing the container drops the omni.ui widgets but not
        # our coroutines, so without this the tasks (and their aiohttp sessions)
        # leak and pile up on every rebuild.
        if self._unassigned is not None:
            self._unassigned.destroy()
        self._unassigned_container.clear()
        with self._unassigned_container:
            self._unassigned = UnassignedArticulations(
                instances=self._reachable_instances(),
                instances_service=self._instances_service,
                on_created=weak_cb(self, "_refresh_data"),
            )
        self._update_section_visibility()

    def _has_any_instance(self) -> bool:
        # The "Added Instances" section is only shown once at least one cloud or
        # custom instance exists.
        if self._custom_instances:
            return True
        return self._has_cloud_instance()

    def _has_cloud_instance(self) -> bool:
        # True when any signed-in provider currently lists at least one instance.
        return any(
            self._instances_service.is_signed_in(auth_config_id)
            and getattr(container, "_cloud_instances", None)
            for auth_config_id, container in self._cloud_instances.items()
        )

    def _update_section_visibility(self):
        # Hide each section (and its separating divider) when it has no content.
        # The leading divider stays visible whenever either section shows, so the
        # first visible section is always separated from the add-instance form;
        # the middle divider only shows when both sections are present.
        show_assigned = self._has_any_instance()
        show_unassigned = bool(
            self._unassigned is not None and not self._unassigned.is_empty
        )
        if self._assigned_container is not None:
            self._assigned_container.visible = show_assigned
        if self._unassigned_container is not None:
            self._unassigned_container.visible = show_unassigned
        if self._assigned_divider is not None:
            self._assigned_divider.visible = show_assigned or show_unassigned
        if self._unassigned_divider is not None:
            self._unassigned_divider.visible = show_assigned and show_unassigned
        if self._instances_divider is not None:
            # Only separate the two instance groups when both are present.
            self._instances_divider.visible = self._has_cloud_instance() and bool(
                self._custom_instances
            )

    def _on_connection_changed(self):
        # A freed articulation must reappear in (or a newly-claimed one disappear
        # from) the global unassigned section after a connect/disconnect. During a
        # refresh this fires once per instance as each one's cells finish loading,
        # so debounce: collapse the burst into a single rebuild instead of tearing
        # down and rebuilding the whole section once per instance.
        if self._unassigned_refresh_task is not None:
            self._unassigned_refresh_task.cancel()
        self._unassigned_refresh_task = run_coroutine(
            self._debounced_display_unassigned()
        )

    async def _debounced_display_unassigned(self):
        try:
            await asyncio.sleep(_UNASSIGNED_REFRESH_DEBOUNCE_S)
        except asyncio.CancelledError:
            return
        self._unassigned_refresh_task = None
        if self._busy:
            # A row is mid create/connect; rebuilding would replace it.
            self._unassigned_refresh_pending = True
            return
        self._display_unassigned()

    def _reachable_instances(self) -> list:
        # Only instances that can currently accept a controller: reachable custom
        # hosts and signed-in, running cloud instances.
        instances: list = []
        for instance in self._custom_instances:
            if instance.is_reachable:
                instances.append(instance)
        for container in self._cloud_instances.values():
            for cloud_inst in getattr(container, "_cloud_instances", []):
                if (
                    self._instances_service.is_signed_in(cloud_inst.auth_config_id)
                    and cloud_inst.is_running
                ):
                    instances.append(cloud_inst)
        return instances

    def _on_sign_in(self, auth_config_id: str):
        if auth_config_id not in self._cloud_instances:
            carb.log_error(
                f"Auth config {auth_config_id} not found in cloud instances containers."
            )
            return
        defer_call(self._display_add_instance)
        # Fetch the newly signed-in provider's instances off the main thread; the
        # cloud and unassigned sections re-render when the load completes (was a
        # blocking portal request on the UI thread).
        self._start_cloud_load()

    def _on_sign_out(self, auth_config_id: str = None):
        def on_complete():
            defer_call(self._display_header)
            defer_call(self._refresh_data)

        self._instances_service.sign_out(auth_config_id, callback=on_complete)

    def _refresh_data(self):
        if self._busy:
            return
        self._load_instances_data()
        self._display_instances()

    def _load_instances_data(self):
        # Load only local, non-blocking data here so the window can render
        # immediately. Cloud instances need a blocking portal request, which is
        # fetched off the main thread by _start_cloud_load and filled in when it
        # returns - previously that request ran here, freezing the UI before the
        # first paint.
        try:
            self._cloud_instances.clear()
            self._custom_instances.clear()
            self._stage_only_hosts = set()

            # Seed the connection registry from the stage so the unassigned section
            # can exclude already-connected articulations on first render.
            self._instances_service.sync_connected_motion_groups_from_stage()

            # Provider containers come from the (local) auth-config list; each
            # starts empty and is filled in by the async cloud load.
            auth_config_ids = list(get_auth_configs().keys())
            multi_provider = len(auth_config_ids) > 1
            self._cloud_instances = {
                auth_config_id: CloudInstances(
                    auth_config_id=auth_config_id,
                    auth_config_name=self._get_auth_config_name(auth_config_id),
                    instances_service=self._instances_service,
                    on_sign_out_fn=weak_cb(self, "_on_sign_out", auth_config_id),
                    on_connection_changed=weak_cb(self, "_on_connection_changed"),
                    cloud_instances=[],
                    multi_provider=multi_provider,
                )
                for auth_config_id in auth_config_ids
            }

            for (
                instance
            ) in self._instances_service.instances_api.get_custom_instances():
                self._custom_instances.append(instance)
        except Exception as e:
            carb.log_error(f"Failed to load instance data: {e}")

        self._cloud_data_loaded = False
        self._start_cloud_load()

    def _start_cloud_load(self):
        # (Re)start the off-main-thread fetch of cloud instances. Cancels any
        # in-flight load so a rapid refresh/sign-in doesn't populate stale state.
        if self._cloud_load_task is not None:
            self._cloud_load_task.cancel()
        self._cloud_load_task = run_coroutine(self._load_cloud_instances_async())

    async def _load_cloud_instances_async(self):
        api = self._instances_service.instances_api
        try:
            loop = asyncio.get_event_loop()
            cloud_by_auth = await loop.run_in_executor(None, api.get_cloud_instances)
        except asyncio.CancelledError:
            return
        except Exception as e:
            carb.log_error(f"Failed to load cloud instances: {e}")
            # Fall through with empty cloud data: the instance containers are
            # deliberately left unpopulated until this coroutine fills them, so
            # bailing out here would leave the window empty forever.
            cloud_by_auth = {}

        self._cloud_load_task = None

        # Fill each provider container with its fetched instances.
        for auth_config_id, instances in cloud_by_auth.items():
            container = self._cloud_instances.get(auth_config_id)
            if container is not None:
                container.set_cloud_instances(instances)

        # Rebuild the custom list from the store plus freshly-resolved orphans:
        # stage-only (orphan) instances can only be told apart from cloud ones once
        # the cloud host set is known. Rebuilding (rather than appending) keeps a
        # repeated load - e.g. on sign-in - from accumulating duplicates.
        previous_custom_hosts = {inst.host for inst in self._custom_instances}
        custom = list(api.get_custom_instances())
        known_hosts = {inst.host for inst in custom}
        for instances in cloud_by_auth.values():
            for inst in instances:
                known_hosts.add(inst.host)
        orphan_instances = self._instances_service.list_stage_instances(known_hosts)
        self._stage_only_hosts = {inst.host for inst in orphan_instances}
        custom.extend(orphan_instances)
        self._custom_instances = custom

        carb.log_info(
            f"Loaded cloud instances "
            f"{[(auth, len(insts)) for auth, insts in cloud_by_auth.items()]} "
            f"and {len(self._custom_instances)} custom instances"
        )

        self._cloud_data_loaded = True
        # Targeted population now that cloud and orphan data are complete: fill
        # the cloud container, and rebuild the (already displayed) custom
        # container only when orphan discovery actually changed the instance
        # set - a full _display_instances() here would rebuild every
        # InstanceWidget a second time and re-fire all their cell fetches
        # (measured at 12+ s of redundant main-thread work).
        custom_changed = {inst.host for inst in custom} != previous_custom_hosts
        if self._cloud_instances_container is not None:
            self._display_cloud_instances()
        if custom_changed and self._custom_instances_container is not None:
            self._display_custom_instances()
        self._update_section_visibility()
        # The unassigned section derives its instance list from the data loaded
        # above; refresh it through the debounced path.
        self._on_connection_changed()

    def _get_auth_config_name(self, auth_config_id: str) -> str:
        auth_configs = get_auth_configs()
        return auth_configs.get(auth_config_id).name

    def _on_host_submitted(self, model):
        self._on_add_custom_instance()

    def _on_add_custom_instance(self):
        text_input = self._host_input.model.get_value_as_string().strip()

        try:
            host_address = self._instances_service.validate_host(text_input)
            host_address = f"{host_address}".replace("https://", "").replace(
                "http://", ""
            )
        except ValueError as e:
            carb.log_verbose(f"Invalid host address: {e}")
            self._host_error_message = "Invalid host address. Please enter a valid URL."
            defer_call(self._display_add_instance)
            return

        try:
            custom_instance = NOVACustomInstance(host=host_address, name=host_address)
            self._instances_service.add_custom_instance(custom_instance)
            self._host_input.model.set_value("")
            self._host_error_message = ""
            defer_call(self._refresh_data)
        except Exception as e:
            carb.log_verbose(f"Failed to add custom instance: {e}")
            self._host_error_message = f"Failed to add custom instance: {e}"
            self._display_add_instance()

    def _on_remove_custom_instance(self, instance: NOVACustomInstance):
        carb.log_info(f"Removing instance: {instance.display_name}")

        def on_complete():
            carb.log_info(f"Instance {instance.display_name} removed successfully")
            self._refresh_data()

        self._instances_service.remove_instance(instance, on_complete_fn=on_complete)
