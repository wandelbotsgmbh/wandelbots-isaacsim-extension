"""Export form of the Payload Calculator: pick the reference frame, calculate
the payload from the stage's rigid bodies and store it on a NOVA instance."""

from __future__ import annotations

import asyncio
import weakref
from typing import cast

import carb
import carb.events
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine
from pxr import Usd, UsdGeom
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.core.payload.payload_properties import (
    CALCULATION_ERRORS,
    PayloadProperties,
    compute_stage_payload_properties,
    plausibility_warnings,
)
from wandelbots.omni.core.payload.payload_store import (
    STORE_ERRORS,
    store_payload_on_instance,
)
from wandelbots.omni.instances.models import NOVAInstance
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.tool.payload_calculator.widgets.payload_value_rows import (
    build_payload_value_rows,
)
from wandelbots.omni.ui.utils import defer_call, weak_cb
from wandelbots.omni.ui.widgets import (
    CollapsibleSection,
    PrimPicker,
    PrimPickerDialogProperties,
)
from wandelbots.omni.ui.widgets.form_row import form_row, message_row
from wandelbots.omni.ui.widgets.instance_picker import InstancePicker
from wandelbots.omni.ui.wb_theme import (
    BUTTON_HEIGHT,
    BUTTON_PRIMARY_STYLE,
    BUTTON_STYLE,
    FIELD_STYLE,
    FORM_HEADER_GAP,
    FORM_SIDE_MARGIN,
    SECTION_GAP,
    SPACING_MD,
    SPACING_SM,
    TOOLTIP_RESET,
    build_tooltip,
)
from wandelbots.omni.utils.api import describe_api_error

DEFAULT_PAYLOAD_NAME = "payload"

_NAME_TOOLTIP = (
    "Name of the payload in NOVA. It is stored under the key payload/<name>."
)
_REFERENCE_TOOLTIP = (
    "Prim whose frame the payload is expressed in, usually the flange or a "
    "tool frame. The centre of mass and the inertia are given relative to "
    "this prim; a scale on it is ignored."
)
_BODIES_HINT = (
    "Every prim with a Rigid Body API in the stage counts. Mass, centre of "
    "mass and inertia come from PhysX, from the colliders and the Mass API "
    "attributes, like in the mass distribution manipulator."
)


def is_xformable(prim: Usd.Prim) -> bool:
    return prim.IsA(UsdGeom.Xformable)


def default_reference_prim(stage: Usd.Stage | None) -> Usd.Prim | None:
    """The stage's default prim, or /World, as the frame a payload is expressed
    in until the user picks another one. Tool assets carry their frame there."""
    if stage is None:
        return None
    for prim in (stage.GetDefaultPrim(), stage.GetPrimAtPath("/World")):
        if prim and prim.IsValid() and is_xformable(prim):
            return prim
    return None


class PayloadExportForm:
    def __init__(self):
        self._stage: Usd.Stage | None = omni.usd.get_context().get_stage()
        self._payload_name = ui.SimpleStringModel(DEFAULT_PAYLOAD_NAME)
        self._reference_prim: Usd.Prim | None = default_reference_prim(self._stage)
        # The picker's buttons only hold a weak proxy of it, so the form has
        # to keep it alive.
        self._reference_prim_picker: PrimPicker | None = None
        self._properties: PayloadProperties | None = None
        self._input_errors: list[str] = []
        self._calculate_task: asyncio.Future | None = None
        self._store_task: asyncio.Future | None = None
        # Collapsed state per section title, persisted across the
        # frame.clear() rebuilds this form performs on every state change.
        self._section_collapsed: dict[str, bool] = {}

        self._instance_picker = InstancePicker(
            tooltip="NOVA instance the payload will be stored to.",
            instance_changed_fn=weak_cb(self, "_deferred_build_ui"),
            instances_refreshed_fn=weak_cb(self, "_deferred_build_ui"),
        )

        self.frame = ui.Frame(height=0)
        self._stage_event_subscription = (
            cast(omni.usd.UsdContext, omni.usd.get_context())
            .get_stage_event_stream()
            .create_subscription_to_pop(
                lambda event, weak_self=weakref.proxy(self): weak_self._on_stage_event(
                    event
                ),
                name="payload_export_form_stage_event",
            )
        )

        self._build_ui()
        self._instance_picker.refresh()

    @property
    def properties(self) -> PayloadProperties | None:
        return self._properties

    @property
    def reference_prim(self) -> Usd.Prim | None:
        return self._reference_prim

    @property
    def calculating(self) -> bool:
        return self._calculate_task is not None and not self._calculate_task.done()

    @property
    def storing(self) -> bool:
        return self._store_task is not None and not self._store_task.done()

    def _make_section(self, title: str, default_collapsed: bool) -> CollapsibleSection:
        def _on_collapsed_changed(
            value: bool, section_title=title, weak_self=weakref.proxy(self)
        ):
            weak_self._section_collapsed[section_title] = value

        return CollapsibleSection(
            title,
            collapsed=self._section_collapsed.get(title, default_collapsed),
            on_collapsed_changed=_on_collapsed_changed,
        )

    def _build_ui(self):
        self.frame.clear()
        if self._stage is None:
            with self.frame:
                ui.Label("No stage loaded.", height=30)
            return

        with self.frame:
            with ui.VStack(spacing=SECTION_GAP):
                self._build_target_section()
                self._build_payload_section()
                if self._properties is not None:
                    self._build_result_section()
                if self.calculating:
                    message_row(
                        "Calculating the mass properties...",
                        NOVAColor.TEXT_SECONDARY.color,
                    )
                for error in self._input_errors:
                    message_row(error, NOVAColor.ERROR_MAIN.color)
                ui.Spacer(height=SPACING_SM)
                self._build_action_row()
                ui.Spacer(height=ui.Fraction(1))

    def _build_target_section(self):
        section = self._make_section("Target", default_collapsed=False)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            self._instance_picker.build_row()
            with form_row("Payload name", tooltip=_NAME_TOOLTIP):
                ui.StringField(
                    model=self._payload_name,
                    height=20,
                    style={**FIELD_STYLE, **TOOLTIP_RESET},
                    tooltip_fn=lambda: build_tooltip(_NAME_TOOLTIP),
                )
            ui.Spacer(height=SPACING_SM)

    def _build_payload_section(self):
        section = self._make_section("Payload", default_collapsed=False)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            with form_row("Reference Xform", tooltip=_REFERENCE_TOOLTIP):
                self._reference_prim_picker = PrimPicker(
                    stage=self._stage,
                    prim_picked_fn=weak_cb(self, "pick_reference_prim"),
                    prim=self._reference_prim,
                    dialog_properties=PrimPickerDialogProperties(
                        filter_fn=is_xformable, title="Select Reference Xform"
                    ),
                )
            message_row(_BODIES_HINT, NOVAColor.TEXT_SECONDARY.color)
            ui.Spacer(height=SPACING_SM)

    def _build_result_section(self):
        properties = self._properties
        section = self._make_section("Result", default_collapsed=False)
        with section.body:
            ui.Spacer(height=FORM_HEADER_GAP)
            build_payload_value_rows(
                mass=properties.mass,
                center_of_mass=properties.center_of_mass,
                moment_of_inertia=properties.moment_of_inertia,
                products_of_inertia=properties.products_of_inertia,
            )
            message_row(
                f"{len(properties.body_paths)} rigid bodies: "
                + ", ".join(properties.body_paths),
                NOVAColor.TEXT_SECONDARY.color,
            )
            ui.Spacer(height=SPACING_SM)

    def _build_action_row(self):
        busy = self.calculating or self.storing
        with ui.HStack(height=BUTTON_HEIGHT, spacing=SPACING_MD):
            ui.Spacer(width=ui.Fraction(1))
            ui.Button(
                "Calculate",
                width=0,
                height=BUTTON_HEIGHT,
                style=BUTTON_STYLE,
                enabled=not busy,
                clicked_fn=weak_cb(self, "request_calculate"),
            )
            ui.Button(
                "Store Payload",
                width=0,
                height=BUTTON_HEIGHT,
                style={
                    **BUTTON_PRIMARY_STYLE,
                    "Button:disabled": {
                        "background_color": NOVAColor.ACTION_DISABLED_BACKGROUND.color
                    },
                },
                enabled=self._properties is not None and not busy,
                clicked_fn=weak_cb(self, "_request_store"),
            )
            ui.Spacer(width=FORM_SIDE_MARGIN)

    def _deferred_build_ui(self):
        defer_call(self._build_ui)

    def refresh_instances(self) -> None:
        """Refetch the instance list in the background. The owning window calls
        this whenever it becomes visible, so instances added or removed via
        Connect-to-NOVA since the constructor's fetch show up."""
        self._instance_picker.refresh()

    def pick_reference_prim(self, prim: Usd.Prim | None) -> None:
        self._reference_prim = prim
        # Calculated values, finished or still running, belong to the
        # previous frame.
        self._cancel_calculation()
        self._properties = None
        self._input_errors = []
        self._deferred_build_ui()

    def _cancel_calculation(self) -> None:
        if self.calculating:
            self._calculate_task.cancel()
        # The finished callback drops a future that is no longer the current
        # task, so a late completion cannot publish a stale result.
        self._calculate_task = None

    def get_calculation_errors(self) -> list[str]:
        if self._reference_prim is None or not self._reference_prim.IsValid():
            return ["No reference Xform selected"]
        return []

    def get_store_errors(self) -> list[str]:
        errors = []
        if self._instance_picker.instance is None:
            errors.append("No NOVA instance selected")
        if self._payload_name.as_string.strip() == "":
            errors.append("Payload name is empty")
        if self._properties is None:
            errors.append("Calculate the payload first")
        return errors

    def request_calculate(self) -> None:
        self._cancel_calculation()
        self._properties = None
        self._input_errors = self.get_calculation_errors()
        if self._input_errors:
            self._deferred_build_ui()
            return
        self._calculate_task = run_coroutine(
            compute_stage_payload_properties(self._stage, self._reference_prim)
        )
        self._calculate_task.add_done_callback(weak_cb(self, "_calculation_finished"))
        self._deferred_build_ui()

    def _calculation_finished(self, future: asyncio.Future) -> None:
        if future is not self._calculate_task:
            return
        try:
            self._properties = future.result()
            self._warn_when_implausible(self._properties)
        except asyncio.CancelledError:
            pass
        except CALCULATION_ERRORS as error:
            carb.log_warn(f"Payload calculation failed: {error}")
            self._input_errors = [f"Calculation failed: {error}"]
        finally:
            self._calculate_task = None
            self._deferred_build_ui()

    @staticmethod
    def _warn_when_implausible(properties: PayloadProperties) -> None:
        warnings = plausibility_warnings(properties)
        if not warnings:
            return
        text = "The payload looks implausible:\n" + "\n".join(warnings)
        carb.log_warn(text)
        nm.post_notification(
            text=text, status=nm.NotificationStatus.WARNING, duration=10.0
        )

    def _request_store(self) -> None:
        self._input_errors = self.get_store_errors()
        if self._input_errors:
            self._deferred_build_ui()
            return
        # Taken at click time, so an input edited while the request runs does
        # not change what gets stored.
        payload = self._properties.to_payload(self._payload_name.as_string.strip())
        self._store_task = run_coroutine(
            self._store_payload(self._instance_picker.instance, payload)
        )
        self._store_task.add_done_callback(weak_cb(self, "_store_finished"))
        self._deferred_build_ui()

    async def _store_payload(
        self, instance: NOVAInstance, payload: wb_v2_models.Payload
    ) -> None:
        cell = await store_payload_on_instance(instance, payload)
        nm.post_notification(
            text=(
                f"Payload '{payload.name}' stored on {instance.display_name} "
                f"in cell '{cell}'."
            ),
            duration=5.0,
        )

    def _store_finished(self, future: asyncio.Future) -> None:
        try:
            future.result()
        except asyncio.CancelledError:
            carb.log_info("Storing the payload was cancelled.")
        except STORE_ERRORS as error:
            carb.log_warn(f"Storing the payload failed: {error}")
            nm.post_notification(
                text=f"Storing the payload failed: {describe_api_error(error)}",
                status=nm.NotificationStatus.WARNING,
            )
        finally:
            self._store_task = None
            self._deferred_build_ui()

    def reset(self) -> None:
        self._cancel_calculation()
        if self.storing:
            self._store_task.cancel()
        self._reference_prim = default_reference_prim(self._stage)
        self._properties = None
        self._input_errors = []
        self._payload_name.set_value(DEFAULT_PAYLOAD_NAME)
        self._instance_picker.reset_selection()
        self._deferred_build_ui()

    def _on_stage_event(self, event: carb.events.IEvent) -> None:
        if event.type == int(omni.usd.StageEventType.OPENED):
            self._stage = omni.usd.get_context().get_stage()
            self.reset()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._stage = None
            self.reset()
