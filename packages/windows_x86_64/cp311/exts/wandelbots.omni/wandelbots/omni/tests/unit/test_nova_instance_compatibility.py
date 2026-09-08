"""Unit tests for NOVAInstance.is_compatible's version comparison.

The installed wandelbots-api-client package (MIN_VERSION's source) and the
version a NOVA instance reports can both carry a pre-release, dev, or local
build suffix (e.g. "26.6.0rc1", "26.6.0+a25f3c1") depending on exactly how
they were built. Comparing full Version objects then rejects (or advertises)
that suffix as if it were part of the actual compatibility requirement - only
the release segment reflects real compatibility.
"""

from packaging.version import Version

import omni.kit.test

from wandelbots.omni.instances import models as instances_models
from wandelbots.omni.instances.models import NOVACustomInstance


def _instance_with_version(version: str | None) -> NOVACustomInstance:
    instance = NOVACustomInstance(host="10.0.0.1", name="test-instance")
    instance.version = Version(version) if version is not None else None
    return instance


class TestNovaInstanceCompatibility(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._original_min_version = instances_models.MIN_VERSION
        self._original_min_release = instances_models.MIN_VERSION_RELEASE
        self._original_min_display = instances_models.MIN_VERSION_DISPLAY

    async def tearDown(self):
        instances_models.MIN_VERSION = self._original_min_version
        instances_models.MIN_VERSION_RELEASE = self._original_min_release
        instances_models.MIN_VERSION_DISPLAY = self._original_min_display

    def _set_min_version(self, version: str) -> None:
        instances_models.MIN_VERSION = Version(version)
        instances_models.MIN_VERSION_RELEASE = instances_models.MIN_VERSION.release
        instances_models.MIN_VERSION_DISPLAY = ".".join(
            str(part) for part in instances_models.MIN_VERSION_RELEASE
        )

    async def test_no_version_is_incompatible(self):
        instance = _instance_with_version(None)
        self.assertFalse(instance.is_compatible)

    async def test_older_release_is_incompatible(self):
        self._set_min_version("26.6.0")
        instance = _instance_with_version("26.5.9")
        self.assertFalse(instance.is_compatible)

    async def test_matching_release_is_compatible(self):
        self._set_min_version("26.6.0")
        instance = _instance_with_version("26.6.0")
        self.assertTrue(instance.is_compatible)

    async def test_newer_release_is_compatible(self):
        self._set_min_version("26.6.0")
        instance = _instance_with_version("27.0.0")
        self.assertTrue(instance.is_compatible)

    async def test_dirty_local_min_version_does_not_reject_the_release_candidate(self):
        # A locally built api-client (e.g. built from a commit past the
        # 26.6.0 tag) can report "26.6.0+a25f3c1" as its own version. That
        # must not turn into a requirement stricter than "26.6.0".
        self._set_min_version("26.6.0+a25f3c1")
        instance = _instance_with_version("26.6.0rc1")
        self.assertTrue(instance.is_compatible)

    async def test_dirty_local_min_version_does_not_reject_the_final_release(self):
        self._set_min_version("26.6.0+a25f3c1")
        instance = _instance_with_version("26.6.0")
        self.assertTrue(instance.is_compatible)

    async def test_min_version_display_has_no_prerelease_or_local_suffix(self):
        self._set_min_version("26.6.0+a25f3c1")
        self.assertEqual(instances_models.MIN_VERSION_DISPLAY, "26.6.0")
