from __future__ import annotations

import weakref
from typing import Callable, Optional

import carb
import omni.ui as ui
from omni.kit.async_engine import run_coroutine

from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import (
    NOVACloudInstance,
    NOVACustomInstance,
    NOVAInstance,
    MIN_VERSION_DISPLAY as MIN_NOVA_VERSION,
    is_cloud_host,
)
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.wb_theme import TOOLTIP_RESET, build_tooltip

from wandelbots.omni.ui.instances.articulations.assigned_articulations import (
    AssignedArticulations,
)
from wandelbots.omni.ui.utils import weak_cb, get_icon
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.widgets.icon_button import IconButton


class InstanceWidget(ui.VStack):
    """Level 3 collapsible section for a single NOVA instance.

    Nested inside a Level 2 "Custom Instances"/"Cloud Instances" group, which in
    turn lives in the top-level "Assigned Articulations" section. Its body lists
    the instance's connected motion groups.
    """

    def __init__(
        self,
        instance: NOVAInstance,
        instances_service: NOVAInstancesService,
        on_remove: Optional[Callable[[NOVAInstance], None]] = None,
        on_toggle_status: Optional[Callable[[], None]] = None,
        on_connection_changed: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        kwargs.setdefault("spacing", 5)
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._instance = instance
        self._instances_service = instances_service
        self._on_remove = on_remove
        self._on_toggle_status = on_toggle_status
        self._on_connection_changed = on_connection_changed
        self._assigned: Optional[AssignedArticulations] = None
        self._section: Optional[CollapsibleSection] = None
        self._collapsed = True
        self._fetch_cells()
        self.rebuild()

    @property
    def host(self) -> str:
        return self._instance.host

    def rebuild(self):
        if self._section is not None:
            self._collapsed = self._section.collapsed
        # Cells must be loaded before the title so the header count is correct.
        self._load_stage_cells_if_unreachable()
        # Release child async tasks and subscriptions before clear() drops them.
        if self._assigned is not None:
            self._assigned.destroy()
        self._assigned = None
        self._section = None
        self.clear()
        with self:
            self._section = CollapsibleSection(
                title=(
                    f"{self._instance.display_name} "
                    f"({self._instance.version or 'n/a'}) "
                    f"[{self._motion_group_count()}]"
                ),
                collapsed=self._collapsed,
                build_leading_fn=lambda sec, _self=self: _self._build_status_dot(),
                build_header_fn=lambda sec, _self=self: _self._build_header_actions(),
            )
            with self._section.body:
                ui.Spacer(height=8)
                self._build_status_and_content()
                ui.Spacer(height=3)

    def _motion_group_count(self) -> int:
        # Match the number of motion-group sections rendered in the body.
        if not self._instance.cells:
            return 0
        return len(
            AssignedArticulations.collect_all_groups(
                self._instances_service, self._instance
            )
        )

    def _build_status_dot(self):
        with ui.HStack(width=0, spacing=8):
            self._build_type_icon()
            with ui.VStack(width=10):
                ui.Spacer()
                ui.Circle(
                    radius=5,
                    width=10,
                    height=10,
                    size_policy=ui.CircleSizePolicy.FIXED,
                    alignment=ui.Alignment.CENTER,
                    style={
                        "background_color": self._instance.status_color,
                        **TOOLTIP_RESET,
                    },
                    tooltip_fn=lambda s=self._instance.status: build_tooltip(
                        f"Status: {s}"
                    ),
                )
                ui.Spacer()

    def _is_cloud_instance(self) -> bool:
        # A stage-discovered cloud instance the user isn't signed into surfaces as
        # a NOVACustomInstance, so fall back to the host to classify it correctly.
        return isinstance(self._instance, NOVACloudInstance) or is_cloud_host(
            self._instance.host
        )

    def _build_type_icon(self):
        icon, tooltip = (
            ("cloud.svg", "Cloud instance.")
            if self._is_cloud_instance()
            else ("Devices.svg", "Custom instance.")
        )
        with ui.VStack(width=16):
            ui.Spacer()
            ui.Image(
                get_icon(icon),
                width=16,
                height=16,
                style={
                    "color": NOVAColor.TEXT_PRIMARY_CONTRAST.color,
                    **TOOLTIP_RESET,
                },
                tooltip_fn=lambda t=tooltip: build_tooltip(t),
            )
            ui.Spacer()

    def _build_header_actions(self):
        instance = self._instance
        self._header_button(
            "external_link.svg",
            f"Open instance {instance.host} in browser.",
            lambda _self=self: _self._on_open_in_browser(),
        )
        if isinstance(instance, NOVACustomInstance) and self._on_remove:
            self._header_button(
                "delete.svg",
                "Remove this instance from the list.",
                lambda _self=self: _self._on_remove(_self._instance),
            )
        elif isinstance(instance, NOVACloudInstance):
            self._build_toggle_status_button(instance.auth_config_id)

    def _header_button(
        self, icon: str, tooltip: str, clicked_fn: Callable, enabled: bool = True
    ):
        with ui.VStack(width=0):
            ui.Spacer()
            IconButton(
                icon=icon,
                tooltip=tooltip,
                enabled=enabled,
                clicked_fn=clicked_fn,
            )
            ui.Spacer()

    def _build_status_and_content(self):
        if not self._instance.is_reachable:
            if self._instance.cells:
                self._label(
                    self._unreachable_hint(),
                    color=NOVAColor.WARNING_LIGHT,
                    bottom_margin=8,
                )
                self._build_assigned()
            else:
                self._label(self._unreachable_hint())
            return

        message = self._unavailable_message()
        if message:
            self._label(message)
            return

        self._sync_connected_motion_groups()
        self._build_assigned()

    def _unreachable_hint(self) -> str:
        # A signed-out cloud instance only needs the user to authenticate, whereas
        # a genuine on-prem host points at a connectivity/instance problem.
        if not isinstance(self._instance, NOVACloudInstance) and is_cloud_host(
            self._instance.host
        ):
            return (
                "Instance is not reachable. Sign in to your NOVA cloud account "
                "to reconnect the articulation."
            )
        return (
            "Instance is not reachable. Restart the instance to reconnect the "
            "articulation."
        )

    def _unavailable_message(self) -> Optional[str]:
        if not self._instance.is_running:
            return "Instance is not running. Press play to start it."
        if self._instance.cells is None:
            return "Loading instance data..."
        if not self._instance.is_compatible:
            return f"Please update your Wandelbots NOVA instance to at least {MIN_NOVA_VERSION}."
        if not self._instance.cells:
            return "No cells available."
        return None

    def _build_assigned(self):
        self._assigned = AssignedArticulations(
            instance=self._instance,
            instances_service=self._instances_service,
            on_connection_changed=weak_cb(self, "_on_assigned_connection_changed"),
        )

    def _on_assigned_connection_changed(self):
        # Refresh this instance's list and count, then bubble up so the global
        # unassigned section re-derives a just-freed (or newly-claimed) articulation.
        self.rebuild()
        if self._on_connection_changed:
            self._on_connection_changed()

    def _build_toggle_status_button(self, auth_config_id: str):
        inst = self._instance
        if inst.status == "running":
            tooltip, icon, enabled = "Stop this instance.", "stop.svg", True
        elif inst.status == "stopped":
            tooltip, icon, enabled = "Start this instance.", "play.svg", True
        else:
            tooltip, icon, enabled = "Loading...", "pending.svg", False

        self._header_button(
            icon,
            tooltip,
            lambda _self=self: _self._instances_service.toggle_instance_status(
                auth_config_id,
                _self._instance,
                callback=_self._on_toggle_status,
            ),
            enabled=enabled,
        )

    def _sync_connected_motion_groups(self):
        self._instances_service.sync_connected_motion_groups_from_stage()

    def _load_stage_cells_if_unreachable(self):
        # When the instance can't be reached, fall back to the motion groups
        # configured on the stage so the header count and body agree. This must
        # run before the title is built so the count reflects them.
        if self._instance.is_reachable:
            return
        stage_cells = self._instances_service.list_cells_from_stage(self._instance.host)
        if stage_cells:
            self._instance.cells = stage_cells
            self._sync_connected_motion_groups()

    def _fetch_cells(self):
        self._instance.cells = None
        weak_self = weakref.ref(self)

        async def _load():
            ref = weak_self()
            if ref is None:
                return
            try:
                if (
                    isinstance(ref._instance, NOVACloudInstance)
                    and ref._instance.status
                    and ref._instance.status.lower() != "running"
                ):
                    ref._instance.cells = []
                    return
                cells = (
                    await ref._instances_service.instances_api.fetch_cells_for_instance(
                        ref._instance
                    )
                )
                carb.log_info(
                    f"Loaded {len(cells)} cells for instance "
                    f"{ref._instance.display_name}"
                )
                ref._instance.cells = cells if cells is not None else []
            except Exception as e:
                carb.log_warn(
                    f"Failed to load cells for {ref._instance.display_name}: {e}"
                )
                ref._instance.cells = []
            finally:
                ref = weak_self()
                if ref is not None:
                    ref.rebuild()
                    # The global unassigned section derives its quick-connect
                    # suggestions from each instance's cells, which only become
                    # available here (async). Notify so it re-renders with the now
                    # populated controllers instead of an empty list.
                    if ref._on_connection_changed:
                        ref._on_connection_changed()

        try:
            run_coroutine(_load())
        except Exception as e:
            carb.log_error(f"Error scheduling cell load: {e}")
            self._instance.cells = []

    def _on_open_in_browser(self):
        import webbrowser

        try:
            carb.log_info(f"Opening instance {self._instance.host} in browser")
            webbrowser.open(self._instance.host)
        except Exception as e:
            carb.log_error(f"Failed to open instance in browser: {e}")

    @staticmethod
    def _label(
        text: str,
        color=NOVAColor.WARNING_LIGHT,
        bottom_margin: int = 0,
    ):
        with ui.HStack(height=0):
            ui.Spacer(width=15)
            ui.Label(
                text,
                width=ui.Fraction(1),
                word_wrap=True,
                style={"color": color.color},
            )
            ui.Spacer(width=10)
        if bottom_margin:
            ui.Spacer(height=bottom_margin)
