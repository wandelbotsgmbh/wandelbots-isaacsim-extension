"""The cloud and the reachability marker are switched apart.

The cloud is the expensive half and is often in the way; the marker is the
answer the tool exists to give. Hiding one must not take the other with it.
"""

import omni.kit.test
import wandelbots_api_client.v2.models as wb_models

from wandelbots.omni.ui.overlay.reachability_envelope.reachability_envelope_overlay import (
    DEFAULT_DENSITY,
    ReachabilityEnvelopeOverlay,
    samples_for_density,
)


class TestEnvelopeVisibilitySwitches(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.overlay = ReachabilityEnvelopeOverlay("test.envelope.visibility")

    async def tearDown(self):
        self.overlay.set_enabled(False)
        self.overlay = None

    async def test_both_start_off(self):
        self.assertFalse(self.overlay.enabled)
        self.assertFalse(self.overlay.cloud_visible)

    async def test_opening_the_tool_does_not_show_the_cloud(self):
        """The overlay follows the window; the cloud waits for its checkbox."""
        self.overlay.set_enabled(True)

        self.assertTrue(self.overlay.enabled)
        self.assertFalse(self.overlay.cloud_visible)

    async def test_hiding_the_cloud_leaves_the_overlay_on(self):
        self.overlay.set_enabled(True)
        self.overlay.set_cloud_visible(True)

        self.overlay.set_cloud_visible(False)

        self.assertFalse(self.overlay.cloud_visible)
        self.assertTrue(self.overlay.enabled)

    async def test_closing_the_tool_takes_everything_down(self):
        self.overlay.set_enabled(True)
        self.overlay.set_cloud_visible(True)

        self.overlay.set_enabled(False)

        self.assertFalse(self.overlay.enabled)

    async def test_a_hidden_cloud_is_not_recomputed(self):
        """Recompute is the cloud's button, so it must decline while it is off."""
        self.overlay.set_enabled(True)

        self.assertFalse(self.overlay.recompute())

    async def test_the_status_reports_both_switches(self):
        self.overlay.set_enabled(True)

        status = self.overlay.get_status()

        self.assertTrue(status["enabled"])
        self.assertFalse(status["cloud_visible"])

    async def test_hiding_the_cloud_drops_its_voxels(self):
        """A kept envelope would go stale against the pose while nothing redraws."""
        self.overlay.set_enabled(True)
        self.overlay.set_cloud_visible(True)
        self.overlay.set_cloud_visible(False)

        self.assertEqual(0, self.overlay.get_status()["voxels"])


class TestEnvelopeReset(omni.kit.test.AsyncTestCase):
    """A replaced stage starts the overlay over without switching it off.

    Every prim path it holds belongs to the closed stage, and a cloud drawn for
    it would stay over the new scene.
    """

    async def setUp(self):
        self.overlay = ReachabilityEnvelopeOverlay("test.envelope.reset")

    async def tearDown(self):
        self.overlay.set_enabled(False)
        self.overlay = None

    async def test_the_targets_are_forgotten(self):
        self.overlay.set_enabled(True)
        self.overlay.set_motion_group("/World/Old/robot")
        self.overlay.set_pose_path("/World/Old/pose")

        self.overlay.reset()

        status = self.overlay.get_status()
        self.assertIsNone(status["motion_group"])
        self.assertIsNone(status["pose"])
        self.assertEqual(0, status["voxels"])

    async def test_the_switches_survive(self):
        """The tool stays open, so the new stage's targets are picked up as is."""
        self.overlay.set_enabled(True)
        self.overlay.set_cloud_visible(True)

        self.overlay.reset()

        self.assertTrue(self.overlay.enabled)
        self.assertTrue(self.overlay.cloud_visible)


class TestEnvelopeMounting(omni.kit.test.AsyncTestCase):
    """The mounting belongs to the robot it came with.

    Kept across a switch to a robot without one, the cloud and Snap pose would
    keep asking NOVA with the previous robot's mount.
    """

    async def setUp(self):
        self.overlay = ReachabilityEnvelopeOverlay("test.envelope.mounting")
        self.mounted = wb_models.Pose(position=[0, 0, 650], orientation=[0, 0, 0])

    async def tearDown(self):
        self.overlay.set_enabled(False)
        self.overlay = None

    async def test_none_clears_a_previous_mounting(self):
        self.overlay.set_mounting(self.mounted)

        self.overlay.set_mounting(None)

        self.assertIsNone(self.overlay.mounting)

    async def test_a_replaced_stage_forgets_the_mounting(self):
        self.overlay.set_mounting(self.mounted)

        self.overlay.reset()

        self.assertIsNone(self.overlay.mounting)


class TestEnvelopeDensity(omni.kit.test.AsyncTestCase):
    """Density sets how many samples the cloud is swept from.

    On a long arm the voxel grid has far more cells than any sample reaches, so
    only the sample count moves the IK load and the number of points drawn.
    """

    async def setUp(self):
        self.overlay = ReachabilityEnvelopeOverlay("test.envelope.density")

    async def tearDown(self):
        self.overlay.set_enabled(False)
        self.overlay = None

    async def test_more_density_means_more_samples(self):
        self.assertLess(samples_for_density(0.1), samples_for_density(0.9))

    async def test_the_slider_ends_are_clamped(self):
        self.assertEqual(samples_for_density(0.0), samples_for_density(-1.0))
        self.assertEqual(samples_for_density(1.0), samples_for_density(2.0))

    async def test_the_default_stays_light(self):
        """The default is what a user sees first; it must not load the scene."""
        self.assertLessEqual(samples_for_density(DEFAULT_DENSITY), 30_000)

    async def test_the_overlay_starts_at_the_default(self):
        self.assertEqual(
            samples_for_density(DEFAULT_DENSITY), self.overlay.sample_count
        )

    async def test_setting_the_density_changes_the_sample_count(self):
        self.overlay.set_density(1.0)

        self.assertEqual(samples_for_density(1.0), self.overlay.sample_count)
