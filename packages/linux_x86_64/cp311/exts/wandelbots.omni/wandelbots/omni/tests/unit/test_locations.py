"""Unit tests for the URL-aware location helpers.

Regression under test: ``os.path.dirname`` on a ``file:`` url reduced it to
``"file:"``, which then reached ``os.makedirs``.
"""

import os

import omni.kit.test

from wandelbots.omni.utils.locations import (
    is_url,
    join_location,
    normalize_location,
    parent_location,
    sibling_location,
    url_scheme,
)


class TestUrlScheme(omni.kit.test.AsyncTestCase):
    async def test_remote_schemes(self):
        self.assertEqual(url_scheme("omniverse://server/a/b.usd"), "omniverse")
        self.assertEqual(url_scheme("https://host/a/b.usd"), "https")
        self.assertEqual(url_scheme("file:/D:/scenes/main.usd"), "file")
        self.assertEqual(url_scheme("FILE:///data/main.usd"), "file")

    async def test_windows_drive_letter_is_not_a_scheme(self):
        self.assertIsNone(url_scheme("D:/scenes/main.usd"))
        self.assertIsNone(url_scheme("D:\\scenes\\main.usd"))

    async def test_plain_paths(self):
        self.assertIsNone(url_scheme("/usr/share/scenes/main.usd"))
        self.assertIsNone(url_scheme(""))
        self.assertIsNone(url_scheme(None))

    async def test_is_url(self):
        self.assertTrue(is_url("omniverse://server/a.usd"))
        self.assertFalse(is_url("/usr/share/a.usd"))
        self.assertFalse(is_url("D:/a.usd"))


class TestNormalizeLocation(omni.kit.test.AsyncTestCase):
    async def test_file_url_becomes_local_path(self):
        # url2pathname is platform specific, so assert the invariant that matters:
        # the result is no longer a URL and no longer carries the scheme.
        normalized = normalize_location("file:///data/scenes/main.usd")
        self.assertFalse(is_url(normalized))
        self.assertNotIn("file:", normalized)
        self.assertTrue(normalized.endswith("main.usd"))

    async def test_single_slash_file_url(self):
        normalized = normalize_location("file:/data/scenes/main.usd")
        self.assertFalse(is_url(normalized))
        self.assertNotIn("file:", normalized)

    async def test_remote_url_untouched(self):
        url = "omniverse://server/scenes/main.usd"
        self.assertEqual(normalize_location(url), url)

    async def test_plain_path_untouched(self):
        self.assertEqual(normalize_location("/data/a.usd"), "/data/a.usd")

    async def test_empty(self):
        self.assertEqual(normalize_location(""), "")
        self.assertEqual(normalize_location(None), "")


class TestParentAndJoin(omni.kit.test.AsyncTestCase):
    async def test_parent_of_remote_url_keeps_scheme_and_host(self):
        self.assertEqual(
            parent_location("omniverse://server/scenes/plant/main.usd"),
            "omniverse://server/scenes/plant",
        )

    async def test_parent_of_file_url_is_a_local_dir(self):
        parent = parent_location("file:/data/scenes/plant/main.usd")
        self.assertFalse(is_url(parent))
        # The old code produced "file:" here.
        self.assertNotEqual(parent, "file:")
        self.assertTrue(parent.endswith(f"scenes{os.sep}plant"))

    async def test_parent_of_plain_path(self):
        self.assertEqual(
            parent_location("/usr/share/scenes/plant/main.usd"),
            "/usr/share/scenes/plant",
        )

    async def test_parent_of_empty(self):
        self.assertEqual(parent_location(""), "")

    async def test_join_remote_url(self):
        self.assertEqual(
            join_location("omniverse://server/scenes/plant", "log.zip"),
            "omniverse://server/scenes/plant/log.zip",
        )

    async def test_join_local_path(self):
        self.assertEqual(
            join_location("/usr/share/scenes", "log.zip"),
            os.path.join("/usr/share/scenes", "log.zip"),
        )

    async def test_join_without_name_returns_directory(self):
        self.assertEqual(join_location("/usr/share/scenes", ""), "/usr/share/scenes")

    async def test_sibling_of_remote_url(self):
        self.assertEqual(
            sibling_location("omniverse://server/scenes/plant/main.usd", "out.zip"),
            "omniverse://server/scenes/plant/out.zip",
        )

    async def test_sibling_of_file_url_is_writable_locally(self):
        sibling = sibling_location("file:/data/scenes/main.usd", "out.zip")
        self.assertFalse(is_url(sibling))
        self.assertTrue(sibling.endswith("out.zip"))
        # os.path.dirname must yield a usable directory, not "file:".
        self.assertNotEqual(os.path.dirname(sibling), "file:")
