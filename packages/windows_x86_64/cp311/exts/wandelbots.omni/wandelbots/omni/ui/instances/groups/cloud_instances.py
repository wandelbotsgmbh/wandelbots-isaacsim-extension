from __future__ import annotations

from typing import Callable, Optional

import omni.ui as ui
from omni.kit.window.property.templates.header_context_menu import (
    GroupHeaderContextMenu,
    GroupHeaderContextMenuEvent,
)

from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import NOVACloudInstance
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.widgets.icon_button import IconButton

from wandelbots.omni.ui.instances.instance.instance_widget import InstanceWidget


class CloudInstances:
    """Cloud instances for a single auth provider, rendered inside the main-window
    "Cloud Instances" group section.

    A single provider renders its instances flat; multiple providers each get a
    per-provider sub-section.
    """

    def __init__(
        self,
        auth_config_id: str,
        auth_config_name: str,
        instances_service: NOVAInstancesService,
        on_sign_out_fn: Optional[Callable] = None,
        on_connection_changed: Optional[Callable[[], None]] = None,
        cloud_instances: Optional[list[NOVACloudInstance]] = None,
        multi_provider: Optional[bool] = None,
    ):
        self.container: Optional[ui.VStack] = None
        self._auth_config_id = auth_config_id
        self._auth_config_name = auth_config_name
        self._instances_service = instances_service
        # Reuse the caller's already-fetched data when provided (the main window
        # fetches every provider once per refresh); fall back to a fetch only for
        # standalone construction. Avoids re-issuing the blocking portal request
        # per provider on every window open.
        self._cloud_instances: list[NOVACloudInstance] = (
            cloud_instances
            if cloud_instances is not None
            else self._instances_service.instances_api.get_cloud_instances_by_auth(
                auth_config_id
            )
        )
        self._multi_provider = (
            multi_provider
            if multi_provider is not None
            else len(self._instances_service.instances_api.get_cloud_instances().keys())
            > 1
        )
        self._on_sign_out_fn = on_sign_out_fn
        self._on_connection_changed = on_connection_changed
        self._instance_widgets: list[InstanceWidget] = []

    def build_ui(self):
        if not self._instances_service.is_signed_in(self._auth_config_id):
            return

        self._instance_widgets.clear()
        self.container = ui.VStack(height=0)
        with self.container:
            if not self._cloud_instances:
                self._display_empty_message()
                return
            if self._multi_provider:
                self._display_provider_section()
            else:
                self._display_instances()

    def refresh_cloud_instances(self):
        self._cloud_instances = (
            self._instances_service.instances_api.get_cloud_instances_by_auth(
                self._auth_config_id
            )
        )

    def set_cloud_instances(self, instances: list[NOVACloudInstance]) -> None:
        # Used by the main window's async cloud load to fill in this provider's
        # instances after they are fetched off the main thread.
        self._cloud_instances = instances

    def _display_instances(self):
        with ui.VStack(spacing=5, height=0):
            for instance in self._cloud_instances:
                widget = InstanceWidget(
                    instance=instance,
                    instances_service=self._instances_service,
                    on_connection_changed=self._on_connection_changed,
                )
                self._instance_widgets.append(widget)

    def _display_provider_section(self):
        section = CollapsibleSection(
            title=self._auth_config_name,
            collapsed=False,
            # The provider grouping is one nesting level above the instance card;
            # it should stay transparent so only the instance card adds the 8%
            # white surface (otherwise the two overlays stack and read too light).
            header_color=NOVAColor.SURFACE_TRANSPARENT,
            header_hover_color=NOVAColor.SURFACE_OVERLAY,
            build_header_fn=lambda sec, _self=self: _self._build_header(),
        )
        section.set_header_mouse_pressed_fn(
            lambda x, y, b, _, _self=self, _gid=self._auth_config_name: (
                _self._show_context_menu(b, _gid)
            )
        )
        with section.body:
            self._display_instances()

    def _display_empty_message(self):
        with ui.HStack(height=0):
            ui.Spacer(width=15)
            title = "No instances available. Please create one."
            if self._multi_provider:
                title = f"[{self._auth_config_name}] {title}"
            ui.Label(title, width=ui.Fraction(1))
            if self._multi_provider:
                IconButton(
                    icon="sign_out.svg",
                    tooltip="Click to sign out of your account.",
                    clicked_fn=self._on_sign_out_fn,
                )
                ui.Spacer(width=6)

    def _build_header(self):
        with ui.HStack(content_clipping=True, width=0):
            IconButton(
                icon="sign_out.svg",
                clicked_fn=self._on_sign_out_fn,
                tooltip="Click to sign out of your account.",
                identifier="sign_out_button",
            )

    @staticmethod
    def _show_context_menu(b: int, group_id: str):
        if b != 1:
            return
        event = GroupHeaderContextMenuEvent(group_id=group_id, payload=[])
        GroupHeaderContextMenu.on_mouse_event(event)
