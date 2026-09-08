"""Instance selection row shared by the collision setup forms."""

from __future__ import annotations

from typing import Callable

import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
from omni.kit.async_engine import run_coroutine

from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVAInstance
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.utils import weak_cb
from wandelbots.omni.ui.widgets.form_row import form_row
from wandelbots.omni.ui.widgets.icon_button import IconButton
from wandelbots.omni.ui.wb_theme import COMBOBOX_STYLE, SPACING_SM, build_tooltip


class InstancePicker:
    """A combo box of the reachable NOVA instances plus a refresh button.

    The picker is created once by the owning form and survives the frame
    rebuilds the form performs on every state change, so the fetched list and
    the selection outlive them.
    """

    def __init__(
        self,
        tooltip: str,
        instance_changed_fn: Callable[[], None],
        instances_refreshed_fn: Callable[[], None],
    ):
        self._tooltip = tooltip
        self._instance_changed_fn = instance_changed_fn
        self._instances_refreshed_fn = instances_refreshed_fn
        self._instances: list[NOVAInstance] = []
        self._selected_index: int = 0
        self._frame: ui.Frame | None = None
        self._combo_subscription = None

    @property
    def instance(self) -> NOVAInstance | None:
        """The currently selected NOVA instance, or None."""
        if not self._instances:
            return None
        return self._instances[min(self._selected_index, len(self._instances) - 1)]

    def build_row(self) -> None:
        """Build the labelled instance row into the enclosing layout."""
        with form_row("Instance", tooltip=self._tooltip):
            with ui.HStack(height=20, spacing=SPACING_SM):
                self._frame = ui.Frame(width=ui.Fraction(1))
                IconButton(
                    icon="refresh.svg",
                    tooltip="Refetch all available NOVA instances.",
                    clicked_fn=weak_cb(self, "refresh"),
                )
        self._rebuild_combo()

    def refresh(self) -> None:
        """Refetch the reachable instances in the background."""
        run_coroutine(self._refresh_async())

    def reset_selection(self) -> None:
        self._selected_index = 0

    async def _refresh_async(self) -> None:
        previously_selected = self.instance
        try:
            self._instances = (
                await get_instances_api().fetch_reachable_running_instances()
            )
        except OSError as exc:
            carb.log_warn(f"Failed to refresh NOVA instances: {exc}")
            nm.post_notification(
                f"Failed to refresh NOVA instances: {exc}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        self._restore_selection(previously_selected)
        self._rebuild_combo()
        self._instances_refreshed_fn()

    def _restore_selection(self, previously_selected: NOVAInstance | None) -> None:
        if previously_selected is None:
            return
        for index, instance in enumerate(self._instances):
            if instance.host == previously_selected.host:
                self._selected_index = index
                return
        self._selected_index = 0

    def _rebuild_combo(self) -> None:
        if self._frame is None:
            return
        self._combo_subscription = None
        self._frame.clear()
        with self._frame:
            if not self._instances:
                ui.Label(
                    "No reachable instances available",
                    style={"color": NOVAColor.TEXT_SECONDARY.color},
                )
                return
            names = [instance.display_name for instance in self._instances]
            combo = ui.ComboBox(
                min(self._selected_index, len(names) - 1),
                *names,
                height=20,
                style=COMBOBOX_STYLE,
                tooltip_fn=lambda text=self._tooltip: build_tooltip(text),
            )
            self._combo_subscription = combo.model.subscribe_item_changed_fn(
                weak_cb(self, "_on_combo_changed")
            )

    def _on_combo_changed(self, model: ui.AbstractItemModel, _item) -> None:
        selected_index = model.get_item_value_model().as_int
        if selected_index == self._selected_index:
            return
        self._selected_index = selected_index
        self._instance_changed_fn()
