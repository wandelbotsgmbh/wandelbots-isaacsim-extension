"""Unit tests for create_diagnose_package with no NOVA instance selected.

Instances are optional, so the service has to produce a log-only or stage-only
package and refuse only when every source is absent. Everything that leaves the
process (scene location, instance download, the final write) is patched, so the
tests assert on the zip that would have been written.
"""

import io
import os
import tempfile
import zipfile
from unittest import mock

import omni.kit.test

from wandelbots.omni.diagnose import diagnose_service


def _patch(**kwargs):
    """Patch the module-level collaborators of create_diagnose_package."""
    return mock.patch.multiple(diagnose_service, **kwargs)


class TestCreateDiagnosePackageWithoutInstances(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._written: list[bytes] = []

    async def _create(self, **overrides):
        """Run create_diagnose_package with no instances; return the zip entries."""

        async def _write(path, data):
            self._written.append(data)

        async def _scene_dir():
            return "/scenes"

        defaults = dict(
            _resolve_scene_dir=_scene_dir,
            _write_package=_write,
            get_isaac_sim_log_path=mock.Mock(return_value=None),
            get_stage_tree=mock.Mock(return_value=None),
            get_motion_groups_json=mock.Mock(return_value=None),
        )
        include_stage_tree = overrides.pop("include_stage_tree", False)
        include_motion_groups = overrides.pop("include_motion_groups", False)
        defaults.update(overrides)

        with _patch(**defaults):
            result = await diagnose_service.create_diagnose_package(
                [],
                timestamp="20260101-000000",
                include_stage_tree=include_stage_tree,
                include_motion_groups=include_motion_groups,
            )
        self.assertEqual(len(self._written), 1)
        with zipfile.ZipFile(io.BytesIO(self._written[0])) as zf:
            return result, zf.namelist()

    async def test_log_only_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "kit.log")
            with open(log_path, "wb") as handle:
                handle.write(b"session log")
            result, names = await self._create(
                get_isaac_sim_log_path=mock.Mock(return_value=log_path)
            )
        self.assertIn("kit.log", names)
        self.assertIn("README.md", names)
        self.assertTrue(result.log_included)
        self.assertEqual(result.succeeded_instances, [])

    async def test_stage_only_package(self):
        _, names = await self._create(
            include_stage_tree=True,
            get_stage_tree=mock.Mock(return_value="- World (Xform)"),
        )
        self.assertIn("stage_tree.txt", names)
        self.assertIn("README.md", names)

    async def test_motion_groups_only_package(self):
        _, names = await self._create(
            include_motion_groups=True,
            get_motion_groups_json=mock.Mock(return_value="{}"),
        )
        self.assertIn("motion_groups.json", names)

    async def test_stage_data_survives_a_missing_log(self):
        # The guard used to run before the stage extraction, so opt-in data was
        # discarded whenever there was no instance and no log.
        _, names = await self._create(
            include_stage_tree=True,
            include_motion_groups=True,
            get_stage_tree=mock.Mock(return_value="- World (Xform)"),
            get_motion_groups_json=mock.Mock(return_value="{}"),
        )
        self.assertIn("stage_tree.txt", names)
        self.assertIn("motion_groups.json", names)

    async def test_nothing_collected_raises(self):
        with self.assertRaises(RuntimeError):
            await self._create()
        self.assertEqual(self._written, [])

    async def test_unsaved_scene_raises(self):
        async def _no_scene_dir():
            return None

        with self.assertRaises(RuntimeError):
            await self._create(_resolve_scene_dir=_no_scene_dir)
        self.assertEqual(self._written, [])
