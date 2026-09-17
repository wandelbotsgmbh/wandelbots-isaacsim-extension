import tempfile
from pathlib import Path

import numpy as np
import omni.kit.test

from wandelbots.omni.utils.watertight_mesh import (
    _cluster_decimate,
    _mesh_volume,
    _split_connected_components,
    read_submeshes,
    write_submeshes,
)

# The full pipeline (build_watertight_mesh) is intentionally untested here:
# its native deps must never load into the Kit process (see module docstring).

UNIT_TET_VERTICES = np.array(
    [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64
)
UNIT_TET_TRIANGLES = np.array(
    [[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]], dtype=np.int32
)


class TestWatertightMesh(omni.kit.test.AsyncTestCase):
    async def test_mesh_volume_of_unit_tetrahedron(self):
        volume = _mesh_volume(UNIT_TET_VERTICES, UNIT_TET_TRIANGLES)
        self.assertAlmostEqual(volume, 1.0 / 6.0, places=10)

    async def test_split_connected_components(self):
        # Two disconnected tetrahedra packed into one soup
        vertices = np.vstack([UNIT_TET_VERTICES, UNIT_TET_VERTICES + 5.0])
        triangles = np.vstack([UNIT_TET_TRIANGLES, UNIT_TET_TRIANGLES + 4])
        components = _split_connected_components(vertices, triangles)
        self.assertEqual(len(components), 2)
        for component_vertices, component_triangles in components:
            self.assertEqual(len(component_vertices), 4)
            self.assertEqual(len(component_triangles), 4)

    async def test_cluster_decimate_welds_and_drops_degenerates(self):
        # Two near-identical vertices weld into one, collapsing one triangle
        vertices = np.array(
            [[0, 0, 0], [1, 0, 0], [1.0001, 0, 0], [0, 1, 0]], dtype=np.float64
        )
        triangles = np.array([[0, 1, 3], [1, 2, 3], [0, 1, 2]], dtype=np.int32)
        welded_vertices, welded_triangles = _cluster_decimate(
            vertices, triangles, cell_size=0.01
        )
        self.assertEqual(len(welded_vertices), 3)
        # [1, 2, 3] and [0, 1, 2] both collapse (1 and 2 weld together)
        self.assertEqual(len(welded_triangles), 1)

    async def test_submeshes_npz_roundtrip(self):
        submeshes = [
            (UNIT_TET_VERTICES, UNIT_TET_TRIANGLES),
            (UNIT_TET_VERTICES * 2.0, UNIT_TET_TRIANGLES),
        ]
        with tempfile.TemporaryDirectory() as exchange_dir:
            path = str(Path(exchange_dir) / "submeshes.npz")
            write_submeshes(path, submeshes)
            restored = read_submeshes(path)
        self.assertEqual(len(restored), 2)
        for (vertices, triangles), (restored_vertices, restored_triangles) in zip(
            submeshes, restored
        ):
            np.testing.assert_array_equal(vertices, restored_vertices)
            np.testing.assert_array_equal(triangles, restored_triangles)

    async def test_submeshes_npz_carries_decompose_flag(self):
        # The flag travels inside the npz (worker argv must stay untouched);
        # the worker's main() reads it exactly like this.
        submeshes = [(UNIT_TET_VERTICES, UNIT_TET_TRIANGLES)]
        with tempfile.TemporaryDirectory() as exchange_dir:
            path = str(Path(exchange_dir) / "submeshes.npz")
            write_submeshes(path, submeshes, decompose=False)
            with np.load(path) as data:
                self.assertFalse(bool(data["decompose"]))
            self.assertEqual(len(read_submeshes(path)), 1)

            write_submeshes(path, submeshes)
            with np.load(path) as data:
                self.assertTrue(bool(data["decompose"]))
