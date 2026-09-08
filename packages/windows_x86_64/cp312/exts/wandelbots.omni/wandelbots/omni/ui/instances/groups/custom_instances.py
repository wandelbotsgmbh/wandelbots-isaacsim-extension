from __future__ import annotations

from typing import Callable, Optional

import omni.ui as ui

from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import NOVACustomInstance, NOVAInstance

from wandelbots.omni.ui.instances.instance.instance_widget import InstanceWidget


class CustomInstances(ui.VStack):
    """Level 2 "Custom Instances" group section, inside the top-level
    "Assigned Articulations" section, listing manually-added instances."""

    def __init__(
        self,
        instances: list[NOVACustomInstance],
        instances_service: NOVAInstancesService,
        orphan_hosts: set[str],
        on_remove: Optional[Callable[[NOVACustomInstance], None]] = None,
        on_refresh: Optional[Callable[[], None]] = None,
        on_connection_changed: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._instances = instances
        self._instances_service = instances_service
        self._orphan_hosts = orphan_hosts
        self._on_remove = on_remove
        self._on_refresh = on_refresh
        self._on_connection_changed = on_connection_changed
        self._instance_widgets: list[InstanceWidget] = []

        self._build()

    def _build(self):
        if not self._instances:
            return

        self._instance_widgets.clear()
        with self:
            with ui.VStack(spacing=5, height=0):
                for instance in self._instances:
                    self._build_instance(instance)
                ui.Spacer(height=10)

    def _build_instance(self, instance: NOVAInstance):
        widget = InstanceWidget(
            instance=instance,
            instances_service=self._instances_service,
            on_remove=self._remove_callback_for(instance),
            on_toggle_status=self._on_refresh,
            on_connection_changed=self._on_connection_changed,
        )
        self._instance_widgets.append(widget)

    def _remove_callback_for(self, instance: NOVAInstance):
        # Stage-only (orphan) instances are not removable from the panel.
        if instance.host in self._orphan_hosts or not self._on_remove:
            return None
        return lambda inst, _self=self: _self._on_remove(inst)
