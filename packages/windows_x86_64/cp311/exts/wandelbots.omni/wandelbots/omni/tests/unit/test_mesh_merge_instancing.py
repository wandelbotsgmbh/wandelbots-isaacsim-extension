"""Merging the meshes of an instanced tool.

``Usd.Prim.GetChildren`` stops at an instance, so the meshes of an instanced tool
live out of reach in its prototype. The ORCA hand of CSI-2980 is built that way
and merged to a ghost object with no geometry at all.
"""

from contextlib import contextmanager

import omni.kit.test
from pxr import Gf, Usd, UsdGeom, Vt

from wandelbots.omni.tests.stage_utils import use_stage
from wandelbots.omni.utils.mesh import MeshUtils

CUBE_POINTS = [
    (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (1.0, 1.0, 0.0),
    (0.0, 1.0, 0.0),
]
CUBE_INDICES = [0, 1, 2, 3]
CUBE_FACE_COUNTS = [4]


@contextmanager
def test_stage():
    stage = Usd.Stage.CreateInMemory("TestMeshMergeInstancing")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    with use_stage(stage):
        yield stage


def define_quad(stage: Usd.Stage, path: str) -> None:
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*p) for p in CUBE_POINTS]))
    mesh.CreateFaceVertexIndicesAttr().Set(Vt.IntArray(CUBE_INDICES))
    mesh.CreateFaceVertexCountsAttr().Set(Vt.IntArray(CUBE_FACE_COUNTS))


def build_tool_with_instanced_visuals(stage: Usd.Stage) -> Usd.Prim:
    """A tool whose geometry is only reachable through an instance."""
    define_quad(stage, "/Prototype/visuals/quad")
    tool = UsdGeom.Xform.Define(stage, "/Tool").GetPrim()
    visuals = UsdGeom.Xform.Define(stage, "/Tool/visuals").GetPrim()
    visuals.GetReferences().AddInternalReference("/Prototype")
    visuals.SetInstanceable(True)
    return tool


class TestMergeInstancedToolMeshes(omni.kit.test.AsyncTestCase):
    async def test_instanced_visuals_contribute_geometry(self):
        with test_stage() as stage:
            tool = build_tool_with_instanced_visuals(stage)
            self.assertTrue(
                stage.GetPrimAtPath("/Tool/visuals").IsInstance(),
                "fixture must instance the visuals for this test to mean anything",
            )

            merged = MeshUtils.merge_prim_meshes(tool, "/Merged")

            points = UsdGeom.Mesh(merged).GetPointsAttr().Get()
            self.assertEqual(len(CUBE_POINTS), len(points))

    async def test_non_instanced_tool_still_merges(self):
        with test_stage() as stage:
            tool = UsdGeom.Xform.Define(stage, "/Tool").GetPrim()
            define_quad(stage, "/Tool/visuals/quad")

            merged = MeshUtils.merge_prim_meshes(tool, "/Merged")

            points = UsdGeom.Mesh(merged).GetPointsAttr().Get()
            self.assertEqual(len(CUBE_POINTS), len(points))

    async def test_face_counts_come_from_the_source_mesh(self):
        with test_stage() as stage:
            tool = build_tool_with_instanced_visuals(stage)

            merged = MeshUtils.merge_prim_meshes(tool, "/Merged")

            self.assertEqual(
                CUBE_FACE_COUNTS,
                list(UsdGeom.Mesh(merged).GetFaceVertexCountsAttr().Get()),
            )

    async def test_tool_without_meshes_yields_an_empty_mesh(self):
        with test_stage() as stage:
            tool = UsdGeom.Xform.Define(stage, "/Tool").GetPrim()
            UsdGeom.Xform.Define(stage, "/Tool/visuals")

            merged = MeshUtils.merge_prim_meshes(tool, "/Merged")

            self.assertIsNone(UsdGeom.Mesh(merged).GetPointsAttr().Get())
