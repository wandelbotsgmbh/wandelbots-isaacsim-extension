"""Screen size of one envelope voxel.

``sc.Points`` takes sizes in screen pixels, so a fixed size only looks right at
one zoom level: move the camera in and the voxels spread apart on screen while
the dots stay the same, until the cloud reads as a few specks instead of a
volume. These pin the rule that keeps one point covering one voxel.
"""

import omni.kit.test

from wandelbots.omni.ui.overlay.reachability_envelope.reachability_envelope_overlay import (
    MAX_POINT_PIXELS,
    MIN_POINT_PIXELS,
    voxel_point_pixels,
)

# 1 / tan(fov_y / 2) for a 60 degree vertical field of view.
PROJECTION_SCALE = 1.7320508
VIEWPORT_HEIGHT = 1080.0


class TestVoxelPointPixels(omni.kit.test.AsyncTestCase):
    async def test_halving_the_distance_doubles_the_size(self):
        far = voxel_point_pixels(0.04, 4.0, PROJECTION_SCALE, VIEWPORT_HEIGHT)
        near = voxel_point_pixels(0.04, 2.0, PROJECTION_SCALE, VIEWPORT_HEIGHT)

        self.assertAlmostEqual(2.0 * far, near, places=6)

    async def test_a_bigger_voxel_covers_more_pixels(self):
        small = voxel_point_pixels(0.02, 3.0, PROJECTION_SCALE, VIEWPORT_HEIGHT)
        large = voxel_point_pixels(0.04, 3.0, PROJECTION_SCALE, VIEWPORT_HEIGHT)

        self.assertAlmostEqual(2.0 * small, large, places=6)

    async def test_matches_the_projection_by_hand(self):
        pixels = voxel_point_pixels(0.04, 3.0, PROJECTION_SCALE, VIEWPORT_HEIGHT)

        expected = 0.04 * PROJECTION_SCALE * VIEWPORT_HEIGHT / (2.0 * 3.0)
        self.assertAlmostEqual(expected, pixels, places=6)

    async def test_far_away_stays_drawable(self):
        """Without a floor the cloud would vanish entirely when zoomed out."""
        pixels = voxel_point_pixels(0.04, 10_000.0, PROJECTION_SCALE, VIEWPORT_HEIGHT)

        self.assertEqual(MIN_POINT_PIXELS, pixels)

    async def test_inside_the_cloud_stays_bounded(self):
        pixels = voxel_point_pixels(0.04, 0.001, PROJECTION_SCALE, VIEWPORT_HEIGHT)

        self.assertEqual(MAX_POINT_PIXELS, pixels)

    async def test_a_degenerate_camera_falls_back_to_the_floor(self):
        self.assertEqual(
            MIN_POINT_PIXELS,
            voxel_point_pixels(0.04, 0.0, PROJECTION_SCALE, VIEWPORT_HEIGHT),
        )
        self.assertEqual(
            MIN_POINT_PIXELS, voxel_point_pixels(0.04, 3.0, PROJECTION_SCALE, 0.0)
        )
