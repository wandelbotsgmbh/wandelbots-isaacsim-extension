import asyncio

import numpy as np
import omni.kit.test
from pxr import Gf, Usd, UsdGeom, Vt

import wandelbots.usd as wb_schema  # type: ignore
from wandelbots.omni.tests.stage_utils import use_stage
from wandelbots.omni.utils import mesh as mesh_module
from wandelbots.omni.utils.ghost_mesh_cache import (
    GHOST_MESH_PENDING_KEY,
    GhostMeshCache,
    _PendingBuild,
)
from wandelbots.omni.utils.mesh import MeshUtils
from wandelbots.omni.utils.teaching import GhostObjectUtils

TOOL_PATH = "/World/Robot/Tool"
STAMP = "geometry-stamp-a"


class FakeMeshPrim:
    """Stands in for a UsdGeom.Mesh in GhostMeshCache.store_mesh."""

    def GetPointsAttr(self):
        return self

    def GetFaceVertexIndicesAttr(self):
        return self

    def GetFaceVertexCountsAttr(self):
        return self

    def Get(self):
        return "mesh-data"


def make_offset(translation: Gf.Vec3d) -> Gf.Matrix4d:
    rotation = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), 30))
    return rotation * Gf.Matrix4d().SetTranslate(translation)


class _FakeAttr:
    def __init__(self, value):
        self._value = value

    def Get(self):
        return self._value


class _FakeMesh:
    """Stands in for a UsdGeom.Mesh with distinct points/indices/counts."""

    def __init__(self, points, indices, counts):
        self._points, self._indices, self._counts = points, indices, counts

    def GetPointsAttr(self):
        return _FakeAttr(self._points)

    def GetFaceVertexIndicesAttr(self):
        return _FakeAttr(self._indices)

    def GetFaceVertexCountsAttr(self):
        return _FakeAttr(self._counts)


class TestGhostMeshCache(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        GhostMeshCache.clear()
        self.offset = make_offset(Gf.Vec3d(10, 0, 25))
        GhostMeshCache.store_mesh(TOOL_PATH, STAMP, self.offset, FakeMeshPrim())

    async def tearDown(self):
        GhostMeshCache.clear()

    async def test_exact_find(self):
        found = GhostMeshCache.find_mesh(TOOL_PATH, self.offset, STAMP)
        self.assertIsNotNone(found)

    async def test_approximate_find_within_tolerance(self):
        near_offset = make_offset(Gf.Vec3d(10.005, 0, 25))
        self.assertIsNotNone(GhostMeshCache.find_mesh(TOOL_PATH, near_offset, STAMP))

    async def test_miss_on_different_offset(self):
        far_offset = make_offset(Gf.Vec3d(15, 0, 25))
        self.assertIsNone(GhostMeshCache.find_mesh(TOOL_PATH, far_offset, STAMP))

    async def test_miss_on_different_tool(self):
        self.assertIsNone(
            GhostMeshCache.find_mesh("/World/OtherTool", self.offset, STAMP)
        )

    async def test_miss_on_changed_geometry(self):
        # An in-stage edit of the tool produces a different stamp: the stale
        # cached mesh must not be reused.
        self.assertIsNone(
            GhostMeshCache.find_mesh(TOOL_PATH, self.offset, "geometry-stamp-b")
        )

    async def test_clear_empties_cache(self):
        GhostMeshCache.clear()
        self.assertEqual(GhostMeshCache._mesh_cache, [])


class TestGhostMeshRepair(omni.kit.test.AsyncTestCase):
    """Covers repair_pending_ghost_meshes: a ghost mesh left with the pending
    marker set - e.g. a Kit Duplicate made while the original's background
    build was still running."""

    async def setUp(self):
        GhostMeshCache.clear()
        self.stage: Usd.Stage = Usd.Stage.CreateInMemory("TestGhostMeshRepair")

        tool_prim = self.stage.DefinePrim("/World/Tool", "Xform")
        tool_prim.ApplyAPI(wb_schema.ToolAPI)
        UsdGeom.Xform.Define(self.stage, "/World/Tool/TCP")
        self.tool_prim = tool_prim
        self.tcp_path = "/World/Tool/TCP"

        self.ghost_mesh = UsdGeom.Mesh.Define(self.stage, "/World/Ghost")
        self.ghost_prim = self.ghost_mesh.GetPrim()
        self.ghost_prim.ApplyAPI(wb_schema.GhostObjectAPI)
        wb_schema.GhostObjectAPI.Get(
            self.stage, self.ghost_prim.GetPath()
        ).GetSourceTcpRel().AddTarget(self.tcp_path)
        self.ghost_prim.SetCustomDataByKey(GHOST_MESH_PENDING_KEY, True)

    async def tearDown(self):
        GhostMeshCache.clear()

    async def test_repair_uses_cache_without_rebuilding(self):
        relative_tcp_transform, _ = GhostMeshCache.compute_transforms(
            self.tool_prim, self.tcp_path
        )
        source_stamp = GhostMeshCache.compute_source_stamp(self.tool_prim)
        cached_points = Vt.Vec3fArray([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
        cached_indices = Vt.IntArray([0, 1, 2])
        cached_counts = Vt.IntArray([3])
        GhostMeshCache.store_mesh(
            self.tool_prim.GetPath().pathString,
            source_stamp,
            relative_tcp_transform,
            _FakeMesh(cached_points, cached_indices, cached_counts),
        )

        def _fail_if_called(*_args, **_kwargs):
            raise AssertionError("cache hit must not start a background build")

        original_fill = MeshUtils.fill_ghost_mesh_progressively
        MeshUtils.fill_ghost_mesh_progressively = _fail_if_called
        try:
            GhostObjectUtils._repair_ghost_mesh(self.ghost_prim)
        finally:
            MeshUtils.fill_ghost_mesh_progressively = original_fill

        self.assertEqual(
            list(self.ghost_mesh.GetPointsAttr().Get()), list(cached_points)
        )
        self.assertIsNone(self.ghost_prim.GetCustomDataByKey(GHOST_MESH_PENDING_KEY))
        self.assertFalse(
            GhostMeshCache.is_building(self.ghost_prim.GetPath().pathString)
        )

    async def test_repair_pass_skips_paths_already_building(self):
        ghost_path = self.ghost_prim.GetPath().pathString
        GhostMeshCache._pending_builds[ghost_path] = _PendingBuild(
            object(), "", "", np.identity(4)
        )

        def _fail_if_called(_prim):
            raise AssertionError(
                "a ghost already tracked as in-flight must not be repaired again"
            )

        original_repair = GhostObjectUtils._repair_ghost_mesh
        GhostObjectUtils._repair_ghost_mesh = staticmethod(_fail_if_called)
        try:
            with use_stage(self.stage):
                GhostObjectUtils.repair_pending_ghost_meshes()
        finally:
            GhostObjectUtils._repair_ghost_mesh = original_repair
            GhostMeshCache._pending_builds.pop(ghost_path, None)

    async def test_repair_piggybacks_on_in_flight_build_for_same_tool(self):
        # Simulates the common case: the ghost this one was duplicated from
        # (a different prim, same tool+TCP) is still building.
        relative_tcp_transform, _ = GhostMeshCache.compute_transforms(
            self.tool_prim, self.tcp_path
        )
        source_path = self.tool_prim.GetPath().pathString
        source_stamp = GhostMeshCache.compute_source_stamp(self.tool_prim)
        original_path = "/World/OriginalGhost"
        original_build = asyncio.get_event_loop().create_future()
        GhostMeshCache._pending_builds[original_path] = _PendingBuild(
            original_build,
            source_path,
            source_stamp,
            np.asarray(relative_tcp_transform),
        )

        def _fail_if_called(*_args, **_kwargs):
            raise AssertionError("a piggybacked duplicate must not start its own build")

        original_fill = MeshUtils.fill_ghost_mesh_progressively
        MeshUtils.fill_ghost_mesh_progressively = _fail_if_called
        try:
            GhostObjectUtils._repair_ghost_mesh(self.ghost_prim)

            # The original's build finishes and populates the cache, exactly
            # as its own done callback would.
            cached_points = Vt.Vec3fArray([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
            cached_indices = Vt.IntArray([0, 1, 2])
            cached_counts = Vt.IntArray([3])
            GhostMeshCache.store_mesh(
                source_path,
                source_stamp,
                relative_tcp_transform,
                _FakeMesh(cached_points, cached_indices, cached_counts),
            )
            original_build.set_result(None)
            await asyncio.sleep(0)  # let the piggybacked done-callback run
        finally:
            MeshUtils.fill_ghost_mesh_progressively = original_fill
            GhostMeshCache._pending_builds.pop(original_path, None)

        self.assertEqual(
            list(self.ghost_mesh.GetPointsAttr().Get()), list(cached_points)
        )
        self.assertIsNone(self.ghost_prim.GetCustomDataByKey(GHOST_MESH_PENDING_KEY))


TRIANGLE_VERTICES = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
TRIANGLE_INDICES = np.array([[0, 1, 2]], dtype=np.int32)


class TestGhostMeshBuild(omni.kit.test.AsyncTestCase):
    """Covers MeshUtils.fill_ghost_mesh_progressively around its worker
    round trips: the build hands geometry to a worker process and resumes
    later, so it must recheck the target before writing the result back."""

    async def setUp(self):
        GhostMeshCache.clear()
        self.stage: Usd.Stage = Usd.Stage.CreateInMemory("TestGhostMeshBuild")
        self.source_prim = self.stage.DefinePrim("/World/Tool", "Xform")
        source_mesh = UsdGeom.Mesh.Define(self.stage, "/World/Tool/Geometry")
        source_mesh.CreatePointsAttr().Set(
            Vt.Vec3fArray([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
        )
        source_mesh.CreateFaceVertexIndicesAttr().Set(Vt.IntArray([0, 1, 2]))
        source_mesh.CreateFaceVertexCountsAttr().Set(Vt.IntArray([3]))

        self.target_path = "/World/Ghost"
        UsdGeom.Mesh.Define(self.stage, self.target_path)

        # The real build shells out to a worker process for its native mesh
        # libraries, so the test drives that boundary instead: it releases
        # each pass by hand to control what the stage looks like on resume.
        self.build_calls = []
        self.release_build = asyncio.get_event_loop().create_future()
        self.build_started = asyncio.get_event_loop().create_future()
        self._original_build = mesh_module._build_watertight_in_subprocess
        mesh_module._build_watertight_in_subprocess = self._fake_build

    async def tearDown(self):
        mesh_module._build_watertight_in_subprocess = self._original_build
        GhostMeshCache.clear()

    async def _fake_build(self, submeshes, decompose: bool = True):
        self.build_calls.append(decompose)
        if not self.build_started.done():
            self.build_started.set_result(None)
        await self.release_build
        return TRIANGLE_VERTICES, TRIANGLE_INDICES

    def _start_build(self) -> asyncio.Task:
        return MeshUtils.fill_ghost_mesh_progressively(
            source_prim=self.source_prim,
            target_path=self.target_path,
            mesh_offset_transform=Gf.Matrix4d().SetIdentity(),
        )

    async def test_aborts_without_writing_when_target_is_gone(self):
        task = self._start_build()
        await self.build_started
        # The scene switched while the worker was running: the ghost the
        # build was started for no longer exists.
        self.stage.RemovePrim(self.target_path)
        self.release_build.set_result(None)

        self.assertIsNone(await task)
        # _write_triangle_mesh would have defined the prim again.
        self.assertFalse(self.stage.GetPrimAtPath(self.target_path))
        # Aborted after the preview pass instead of going on to refine.
        self.assertEqual(self.build_calls, [False])

    async def test_writes_preview_and_refined_mesh(self):
        self.release_build.set_result(None)
        refined_mesh = await self._start_build()

        self.assertIsNotNone(refined_mesh)
        self.assertEqual(self.build_calls, [False, True])
        written_points = UsdGeom.Mesh(
            self.stage.GetPrimAtPath(self.target_path)
        ).GetPointsAttr()
        self.assertEqual(len(written_points.Get()), len(TRIANGLE_VERTICES))
