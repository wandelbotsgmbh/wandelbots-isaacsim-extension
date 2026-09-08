"""Unit test for the semantic label round-trip.

Runs against the real isaacsim.core.utils.semantics module on both SDK test
targets (107.3 / Isaac Sim 5.1 and 110.1 / Isaac Sim 6.0), so API breaks like
the 6.0 removal of add_update_semantics fail the suite instead of the
endpoints. Uses the usd context stage because the label utilities resolve
prims through it.
"""

import omni.kit.test
import omni.usd
from pxr import UsdGeom

from wandelbots.omni.utils.synthetic_data import (
    _HAS_DEPRECATED_SEMANTICS,
    SyntheticDataUtils,
)

_TEST_PRIM_PATH = "/World/SemanticLabelTestPrim"


class TestSemanticLabels(omni.kit.test.AsyncTestCase):
    async def test_set_get_and_remove_label_roundtrip(self):
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, _TEST_PRIM_PATH)
        try:
            SyntheticDataUtils.set_semantic_label(_TEST_PRIM_PATH, "test_label")
            self.assertEqual(
                SyntheticDataUtils.get_semantic_label(_TEST_PRIM_PATH),
                ["test_label"],
            )
            self.assertEqual(
                SyntheticDataUtils.get_all_semantic_labels().get(_TEST_PRIM_PATH),
                ["test_label"],
            )

            SyntheticDataUtils.remove_all_semantic_labels()
            self.assertEqual(SyntheticDataUtils.get_semantic_label(_TEST_PRIM_PATH), [])
        finally:
            stage.RemovePrim(_TEST_PRIM_PATH)

    async def test_labels_written_with_deprecated_schema_are_read_and_removed(self):
        """Scenes labeled by older extension versions must keep working."""
        if not _HAS_DEPRECATED_SEMANTICS:
            self.skipTest("deprecated SemanticsAPI schema not available")
        import Semantics

        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        prim = UsdGeom.Xform.Define(stage, _TEST_PRIM_PATH).GetPrim()
        try:
            semantics_api = Semantics.SemanticsAPI.Apply(prim, "Semantics")
            semantics_api.CreateSemanticTypeAttr().Set("class")
            semantics_api.CreateSemanticDataAttr().Set("legacy_label")

            self.assertEqual(
                SyntheticDataUtils.get_semantic_label(_TEST_PRIM_PATH),
                ["legacy_label"],
            )
            self.assertEqual(
                SyntheticDataUtils.get_all_semantic_labels().get(_TEST_PRIM_PATH),
                ["legacy_label"],
            )

            SyntheticDataUtils.remove_all_semantic_labels()
            self.assertEqual(SyntheticDataUtils.get_semantic_label(_TEST_PRIM_PATH), [])
            self.assertFalse(
                SyntheticDataUtils._deprecated_class_semantics(prim),
                "deprecated semantics must not be left behind",
            )
        finally:
            stage.RemovePrim(_TEST_PRIM_PATH)

    async def test_setting_a_class_label_keeps_other_label_instances(self):
        """add_labels replaces only the "class" instance; a prim may carry
        other LabelsAPI instances that must survive."""
        import isaacsim.core.utils.semantics as semantic_utils

        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        prim = UsdGeom.Xform.Define(stage, _TEST_PRIM_PATH).GetPrim()
        try:
            semantic_utils.add_labels(prim, ["shiny"], instance_name="material")

            SyntheticDataUtils.set_semantic_label(_TEST_PRIM_PATH, "test_label")

            labels = semantic_utils.get_labels(prim)
            self.assertEqual(list(labels.get("class", [])), ["test_label"])
            self.assertEqual(list(labels.get("material", [])), ["shiny"])
        finally:
            stage.RemovePrim(_TEST_PRIM_PATH)

    async def test_clearing_labels_keeps_other_label_instances(self):
        """Clearing is scoped to the class instance: labels another tool
        stored under a different instance name must survive."""
        import isaacsim.core.utils.semantics as semantic_utils

        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        prim = UsdGeom.Xform.Define(stage, _TEST_PRIM_PATH).GetPrim()
        try:
            semantic_utils.add_labels(prim, ["shiny"], instance_name="material")
            SyntheticDataUtils.set_semantic_label(_TEST_PRIM_PATH, "test_label")

            SyntheticDataUtils.remove_all_semantic_labels()

            labels = semantic_utils.get_labels(prim)
            self.assertEqual(list(labels.get("class", [])), [])
            self.assertEqual(list(labels.get("material", [])), ["shiny"])
        finally:
            stage.RemovePrim(_TEST_PRIM_PATH)

    async def test_clearing_keeps_deprecated_tags_of_other_namespaces(self):
        """The deprecated schema keeps the namespace in semanticType, so only
        type "class" tags belong to this API."""
        if not _HAS_DEPRECATED_SEMANTICS:
            self.skipTest("deprecated SemanticsAPI schema not available")
        import Semantics

        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        prim = UsdGeom.Xform.Define(stage, _TEST_PRIM_PATH).GetPrim()
        try:
            class_api = Semantics.SemanticsAPI.Apply(prim, "Semantics")
            class_api.CreateSemanticTypeAttr().Set("class")
            class_api.CreateSemanticDataAttr().Set("legacy_label")
            other_api = Semantics.SemanticsAPI.Apply(prim, "Semantics_material")
            other_api.CreateSemanticTypeAttr().Set("material")
            other_api.CreateSemanticDataAttr().Set("shiny")

            SyntheticDataUtils.remove_all_semantic_labels()

            self.assertEqual(SyntheticDataUtils.get_semantic_label(_TEST_PRIM_PATH), [])
            surviving = Semantics.SemanticsAPI.Get(prim, "Semantics_material")
            self.assertEqual(surviving.GetSemanticDataAttr().Get(), "shiny")
        finally:
            stage.RemovePrim(_TEST_PRIM_PATH)
