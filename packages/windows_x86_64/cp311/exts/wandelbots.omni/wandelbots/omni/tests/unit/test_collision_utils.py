"""Unit tests for core collision utilities (link-chain extras merging)."""

from __future__ import annotations

import omni.kit.test

from wandelbots.omni.core.collision.utils import merge_link_chain_extras


class TestMergeLinkChainExtras(omni.kit.test.AsyncTestCase):
    async def test_no_extras_returns_copy_of_canonical(self):
        canonical = [{"visuals": "link-0"}, {"visuals": "link-1"}]
        merged = merge_link_chain_extras(canonical, None)

        self.assertEqual(merged, canonical)
        # Must be a copy - mutating the result must not touch the canonical model
        merged[0]["extra"] = "x"
        self.assertNotIn("extra", canonical[0])

    async def test_extras_are_merged_per_link(self):
        canonical = [{"visuals": "link-0"}, {"visuals": "link-1"}, {}]
        extras = [{}, {"dresspack": "extra-1"}, {"cable": "extra-2"}]

        merged = merge_link_chain_extras(canonical, extras)

        self.assertEqual(merged[0], {"visuals": "link-0"})
        self.assertEqual(merged[1], {"visuals": "link-1", "dresspack": "extra-1"})
        self.assertEqual(merged[2], {"cable": "extra-2"})
        # Inputs untouched
        self.assertEqual(canonical[1], {"visuals": "link-1"})
        self.assertEqual(extras[1], {"dresspack": "extra-1"})

    async def test_id_collision_keeps_canonical(self):
        # Legacy setups stored full merged chains; their duplicated model
        # entries must lose against the freshly fetched canonical geometry.
        canonical = [{"visuals": "fresh-model"}]
        extras = [{"visuals": "stale-copy", "dresspack": "extra"}]

        merged = merge_link_chain_extras(canonical, extras)

        self.assertEqual(merged[0]["visuals"], "fresh-model")
        self.assertEqual(merged[0]["dresspack"], "extra")

    async def test_extra_links_beyond_model_extend_the_chain(self):
        # Canonical models are truncated after the last link with geometry
        # (commonly omitting the flange link), so flange-mounted extras stored
        # beyond that length must survive the merge.
        canonical = [{"visuals": "link-0"}]
        extras = [{}, {"flange_camera": "beyond-model"}]

        merged = merge_link_chain_extras(canonical, extras)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0], {"visuals": "link-0"})
        self.assertEqual(merged[1], {"flange_camera": "beyond-model"})
