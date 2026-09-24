"""Load form of the Payload Calculator: list the payloads stored on a NOVA
instance and show the values of the selected one."""

from __future__ import annotations

import asyncio

import carb
import omni.ui as ui
from omni.kit.async_engine import run_coroutine
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.core.payload.payload_store import (
    STORE_ERRORS,
    list_payload_names_on_instance,
    load_payload_from_instance,
)
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.tool.payload_calculator.widgets.payload_value_rows import (
    build_payload_value_rows,
)
from wandelbots.omni.ui.utils import defer_call, weak_cb
from wandelbots.omni.ui.widgets.form_row import form_row, message_row
from wandelbots.omni.ui.widgets.instance_picker import InstancePicker
from wandelbots.omni.ui.wb_theme import (
    BUTTON_HEIGHT,
    BUTTON_PRIMARY_STYLE,
    COMBOBOX_STYLE,
    FIELD_STYLE,
    FORM_HEADER_GAP,
    FORM_SIDE_MARGIN,
    SPACING_MD,
    SPACING_SM,
    TOOLTIP_RESET,
    build_tooltip,
)
from wandelbots.omni.utils.api import describe_api_error

_PAYLOAD_TOOLTIP = "Stored payload on the selected NOVA instance."


class PayloadLoadForm:
    def __init__(self):
        self.frame = ui.Frame(height=0)
        self._payload_names: list[str] = []
        self._selected_name: str | None = None
        self._loaded_payload: wb_v2_models.Payload | None = None
        self._error: str | None = None
        self._list_task: asyncio.Future | None = None
        self._load_task: asyncio.Future | None = None
        self._combo_subscription = None

        self._instance_picker = InstancePicker(
            tooltip="NOVA instance to load a stored payload from.",
            instance_changed_fn=weak_cb(self, "_on_instance_changed"),
            instances_refreshed_fn=weak_cb(self, "refresh_payload_names"),
        )

        self._build_ui()
        self._instance_picker.refresh()

    @property
    def loaded_payload(self) -> wb_v2_models.Payload | None:
        return self._loaded_payload

    @property
    def listing(self) -> bool:
        return self._list_task is not None and not self._list_task.done()

    @property
    def loading(self) -> bool:
        return self._load_task is not None and not self._load_task.done()

    def _build_ui(self):
        self.frame.clear()
        with self.frame:
            with ui.VStack(spacing=SPACING_SM):
                ui.Spacer(height=FORM_HEADER_GAP)
                self._instance_picker.build_row()
                if self._instance_picker.instance is None:
                    message_row(
                        "Select a NOVA instance to load a payload.",
                        NOVAColor.TEXT_SECONDARY.color,
                    )
                    return
                self._build_payload_row()
                if self._error:
                    message_row(self._error, NOVAColor.ERROR_MAIN.color)
                if self._loaded_payload is not None:
                    self._build_loaded_rows()
                ui.Spacer(height=SPACING_SM)
                self._build_action_row()
                ui.Spacer(height=SPACING_SM)

    def _build_payload_row(self):
        with form_row("Payload", tooltip=_PAYLOAD_TOOLTIP):
            if self.listing:
                ui.Label(
                    "Loading payloads...",
                    style={"color": NOVAColor.TEXT_SECONDARY.color},
                )
                return
            if not self._payload_names:
                ui.Label(
                    "No payloads found",
                    style={"color": NOVAColor.TEXT_SECONDARY.color},
                )
                return
            selected_index = (
                self._payload_names.index(self._selected_name)
                if self._selected_name in self._payload_names
                else 0
            )
            combo = ui.ComboBox(
                selected_index,
                *self._payload_names,
                height=20,
                style=COMBOBOX_STYLE,
                tooltip_fn=lambda: build_tooltip(_PAYLOAD_TOOLTIP),
            )
            self._combo_subscription = combo.model.subscribe_item_changed_fn(
                weak_cb(self, "_on_payload_selected")
            )

    def _build_loaded_rows(self):
        payload = self._loaded_payload
        ui.Spacer(height=SPACING_SM)
        with form_row("Name", tooltip="Name stored inside the payload"):
            ui.StringField(
                model=ui.SimpleStringModel(payload.name),
                read_only=True,
                height=20,
                style={**FIELD_STYLE, **TOOLTIP_RESET},
            )
        build_payload_value_rows(
            mass=payload.payload,
            center_of_mass=payload.center_of_mass,
            moment_of_inertia=payload.moment_of_inertia,
        )

    def _build_action_row(self):
        with ui.HStack(height=BUTTON_HEIGHT, spacing=SPACING_MD):
            ui.Spacer(width=ui.Fraction(1))
            ui.Button(
                "Load Payload",
                width=0,
                height=BUTTON_HEIGHT,
                style={
                    **BUTTON_PRIMARY_STYLE,
                    "Button:disabled": {
                        "background_color": NOVAColor.ACTION_DISABLED_BACKGROUND.color
                    },
                },
                enabled=self._selected_name is not None
                and not self.listing
                and not self.loading,
                clicked_fn=weak_cb(self, "request_load"),
            )
            ui.Spacer(width=FORM_SIDE_MARGIN)

    def _deferred_build_ui(self):
        defer_call(self._build_ui)

    def refresh_instances(self) -> None:
        """Refetch the instance list in the background. The owning window calls
        this whenever it becomes visible, so instances added or removed via
        Connect-to-NOVA since the constructor's fetch show up."""
        self._instance_picker.refresh()

    @property
    def selected_name(self) -> str | None:
        return self._selected_name

    def _on_instance_changed(self) -> None:
        # Another instance serves other payloads, so neither the selection nor
        # a load still running for the old one may survive.
        self._cancel_load()
        self._selected_name = None
        self._loaded_payload = None
        self.refresh_payload_names()

    def refresh_payload_names(self) -> None:
        self._error = None
        # A refresh started while one is running takes over; the superseded
        # request must not report into the form later.
        if self.listing:
            self._list_task.cancel()
        instance = self._instance_picker.instance
        if instance is None:
            self._payload_names = []
            self._deferred_build_ui()
            return
        self._list_task = run_coroutine(list_payload_names_on_instance(instance))
        self._list_task.add_done_callback(weak_cb(self, "_names_listed"))
        self._deferred_build_ui()

    def _names_listed(self, future: asyncio.Future) -> None:
        if future is not self._list_task:
            return
        try:
            self._payload_names = future.result()
        except asyncio.CancelledError:
            pass
        except STORE_ERRORS as error:
            self._payload_names = []
            self._error = f"Listing the payloads failed: {describe_api_error(error)}"
            carb.log_warn(self._error)
        finally:
            self._list_task = None
            if self._selected_name not in self._payload_names:
                self._cancel_load()
                self._selected_name = (
                    self._payload_names[0] if self._payload_names else None
                )
                self._loaded_payload = None
            self._deferred_build_ui()

    def _on_payload_selected(self, model: ui.AbstractItemModel, _item) -> None:
        index = model.get_item_value_model().as_int
        if 0 <= index < len(self._payload_names):
            self.select_payload(self._payload_names[index])

    def select_payload(self, name: str) -> None:
        if name == self._selected_name:
            return
        # A load still running fetches the previous selection.
        self._cancel_load()
        self._selected_name = name
        self._loaded_payload = None
        self._error = None
        self._deferred_build_ui()

    def _cancel_load(self) -> None:
        if self.loading:
            self._load_task.cancel()
        # The loaded callback drops a future that is no longer the current
        # task, so a late completion cannot show a stale payload.
        self._load_task = None

    def request_load(self) -> None:
        instance = self._instance_picker.instance
        if instance is None or self._selected_name is None:
            return
        self._cancel_load()
        self._error = None
        self._load_task = run_coroutine(
            load_payload_from_instance(instance, self._selected_name)
        )
        self._load_task.add_done_callback(weak_cb(self, "_payload_loaded"))
        self._deferred_build_ui()

    def _payload_loaded(self, future: asyncio.Future) -> None:
        if future is not self._load_task:
            return
        try:
            self._loaded_payload = future.result()
        except asyncio.CancelledError:
            pass
        except STORE_ERRORS as error:
            self._loaded_payload = None
            self._error = f"Loading the payload failed: {describe_api_error(error)}"
            carb.log_warn(self._error)
        finally:
            self._load_task = None
            self._deferred_build_ui()
