"""The Payload Calculator window builds and its tabs switch pages.

Unit tests never call a widget's build otherwise, and a build error inside a
section silently hides everything below it.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import numpy as np
import omni.kit.app
import omni.kit.test
import omni.ui as ui
import omni.usd
from pxr import Usd, UsdGeom
import wandelbots_api_client.v2.models as wb_v2_models

import wandelbots.omni.ui.tool.payload_calculator.widgets.payload_export_form as export_form_module
import wandelbots.omni.ui.tool.payload_calculator.widgets.payload_load_form as load_form_module
from wandelbots.omni.core.payload.payload_properties import PayloadProperties
from wandelbots.omni.ui.tool.payload_calculator.payload_calculator_window import (
    EXPORT_TAB,
    LOAD_TAB,
    PayloadCalculatorWindow,
)
from wandelbots.omni.ui.tool.payload_calculator.widgets.payload_export_form import (
    default_reference_prim,
)
from wandelbots.omni.ui.tool.payload_calculator.widgets.payload_value_rows import (
    build_payload_value_rows,
)
from wandelbots.omni.ui.widgets.instance_picker import InstancePicker


class TestDefaultReferencePrim(omni.kit.test.AsyncTestCase):
    async def test_prefers_the_default_prim(self):
        stage = Usd.Stage.CreateInMemory("TestDefaultReferencePrim")
        UsdGeom.Xform.Define(stage, "/World")
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/Tool").GetPrim())
        self.assertEqual("/Tool", default_reference_prim(stage).GetPath().pathString)

    async def test_falls_back_to_world(self):
        stage = Usd.Stage.CreateInMemory("TestDefaultReferencePrim")
        UsdGeom.Xform.Define(stage, "/World")
        self.assertEqual("/World", default_reference_prim(stage).GetPath().pathString)

    async def test_skips_a_default_prim_without_a_frame(self):
        stage = Usd.Stage.CreateInMemory("TestDefaultReferencePrim")
        UsdGeom.Xform.Define(stage, "/World")
        stage.SetDefaultPrim(UsdGeom.Scope.Define(stage, "/Looks").GetPrim())
        self.assertEqual("/World", default_reference_prim(stage).GetPath().pathString)

    async def test_none_without_a_candidate(self):
        stage = Usd.Stage.CreateInMemory("TestDefaultReferencePrim")
        UsdGeom.Xform.Define(stage, "/Robot")
        self.assertIsNone(default_reference_prim(stage))
        self.assertIsNone(default_reference_prim(None))


class TestPayloadCalculatorWindow(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        # A fresh stage with the /World default prim Isaac Sim's stage template
        # provides; the bare test app creates an empty stage.
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())
        # The forms refetch the NOVA instances on construction; that is a
        # network round trip the widget test must not depend on.
        self._refresh_patch = patch.object(InstancePicker, "refresh")
        self._refresh_patch.start()
        # Own title, so the test never aliases the extension's registered window.
        self._window = PayloadCalculatorWindow(title="TestPayloadCalculatorWindow")
        await omni.kit.app.get_app().next_update_async()

    async def tearDown(self):
        self._window.window.destroy()
        self._window = None
        self._refresh_patch.stop()

    async def test_builds_both_forms(self):
        self.assertIsNotNone(self._window.export_form)
        self.assertIsNotNone(self._window.load_form)
        self.assertIsNone(self._window.export_form.properties)
        self.assertIsNone(self._window.load_form.loaded_payload)

    async def test_starts_on_the_export_tab(self):
        self.assertTrue(self._window.is_page_visible(EXPORT_TAB))
        self.assertFalse(self._window.is_page_visible(LOAD_TAB))

    async def test_tabs_switch_the_pages(self):
        self._window.select_tab(LOAD_TAB)
        self.assertFalse(self._window.is_page_visible(EXPORT_TAB))
        self.assertTrue(self._window.is_page_visible(LOAD_TAB))

        self._window.select_tab(EXPORT_TAB)
        self.assertTrue(self._window.is_page_visible(EXPORT_TAB))
        self.assertFalse(self._window.is_page_visible(LOAD_TAB))

    async def test_fresh_stage_preselects_the_world_prim(self):
        export_form = self._window.export_form
        self.assertEqual("/World", export_form.reference_prim.GetPath().pathString)
        self.assertEqual([], export_form.get_calculation_errors())
        self.assertIn("Calculate the payload first", export_form.get_store_errors())


class TestPayloadValueRows(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._window = ui.Window("TestPayloadValueRows", width=400, height=300)

    async def tearDown(self):
        self._window.destroy()
        self._window = None

    async def test_builds_with_all_values(self):
        with self._window.frame:
            with ui.VStack():
                build_payload_value_rows(
                    mass=2.0,
                    center_of_mass=(1.0, 2.0, 3.0),
                    moment_of_inertia=(0.1, 0.2, 0.3),
                    products_of_inertia=(0.0, 0.0, 0.0),
                )
        await omni.kit.app.get_app().next_update_async()

    async def test_builds_with_missing_optional_values(self):
        with self._window.frame:
            with ui.VStack():
                build_payload_value_rows(
                    mass=2.0, center_of_mass=None, moment_of_inertia=None
                )
        await omni.kit.app.get_app().next_update_async()


class TestStaleResultsAreDropped(omni.kit.test.AsyncTestCase):
    """An input change while a request runs must keep that request's result
    out of the form, or a payload could be shown or stored for another
    reference frame or another selection than the one on screen."""

    async def setUp(self):
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())
        self._other_frame = UsdGeom.Xform.Define(stage, "/World/Tool").GetPrim()
        self._refresh_patch = patch.object(InstancePicker, "refresh")
        self._refresh_patch.start()
        self._window = None

    async def tearDown(self):
        if self._window is not None:
            self._window.window.destroy()
            self._window = None
        self._refresh_patch.stop()

    async def _settle(self, updates: int = 3) -> None:
        for _ in range(updates):
            await omni.kit.app.get_app().next_update_async()

    async def test_reference_change_cancels_the_running_calculation(self):
        release = asyncio.Event()

        async def slow_compute(stage, reference_prim):
            await release.wait()
            return PayloadProperties(
                mass=1.0, center_of_mass=(0.0, 0.0, 0.0), inertia_tensor=np.eye(3)
            )

        with patch.object(
            export_form_module, "compute_stage_payload_properties", slow_compute
        ):
            self._window = PayloadCalculatorWindow(title="TestStaleResultsAreDropped")
            form = self._window.export_form
            form.request_calculate()
            await self._settle()
            self.assertTrue(form.calculating)

            form.pick_reference_prim(self._other_frame)
            self.assertFalse(form.calculating)
            release.set()
            await self._settle()

            self.assertIsNone(form.properties)
            self.assertEqual("/World/Tool", form.reference_prim.GetPath().pathString)

    async def test_selecting_another_payload_cancels_the_running_load(self):
        release = asyncio.Event()

        async def list_names(instance):
            return ["a", "b"]

        async def slow_load(instance, name):
            await release.wait()
            return wb_v2_models.Payload(name=name, payload=1.0)

        instance = SimpleNamespace(display_name="test", host="test")
        with (
            patch.object(
                InstancePicker,
                "instance",
                new_callable=PropertyMock,
                return_value=instance,
            ),
            patch.object(
                load_form_module, "list_payload_names_on_instance", list_names
            ),
            patch.object(load_form_module, "load_payload_from_instance", slow_load),
        ):
            self._window = PayloadCalculatorWindow(title="TestStaleResultsAreDropped")
            form = self._window.load_form
            form.refresh_payload_names()
            await self._settle()
            self.assertEqual("a", form.selected_name)

            form.request_load()
            await self._settle()
            self.assertTrue(form.loading)

            form.select_payload("b")
            self.assertFalse(form.loading)
            release.set()
            await self._settle()

            self.assertIsNone(form.loaded_payload)
            self.assertEqual("b", form.selected_name)
