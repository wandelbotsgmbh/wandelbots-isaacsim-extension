"""Projecting a 3D box onto the frame.

The wireframe the bounding-box-3d image format draws is only as good as this
arithmetic, and it is pure - so it is checked here rather than by looking at
a rendered picture.
"""

import numpy as np
import omni.kit.test

from PIL import Image, ImageDraw

from wandelbots.omni.periphery.camera_configuration import (
    BOX_EDGES,
    draw_wireframe,
    project_box_corners,
)

WIDTH, HEIGHT = 200, 100
VIEW = np.eye(4)
# Symmetric perspective, 90 degrees horizontally, aspect 2:1.
PROJECTION = np.array(
    [
        [1.0, 0, 0, 0],
        [0, 2.0, 0, 0],
        [0, 0, -1.0, -1.0],
        [0, 0, -2.0, 0.0],
    ]
).T
AHEAD = (-1.0, -1.0, -11.0, 1.0, 1.0, -9.0)


def spread(corners):
    return max(c[0] for c in corners) - min(c[0] for c in corners)


class TestProjectBoxCorners(omni.kit.test.AsyncTestCase):
    def project(self, bbox, local_to_world=None):
        return project_box_corners(
            bbox=bbox,
            local_to_world=np.eye(4) if local_to_world is None else local_to_world,
            view=VIEW,
            projection=PROJECTION,
            resolution=(WIDTH, HEIGHT),
        )

    async def test_a_box_straight_ahead_projects_around_the_centre(self):
        corners = self.project(AHEAD)
        self.assertEqual(len(corners), 8)
        self.assertLess(min(c[0] for c in corners), WIDTH / 2)
        self.assertGreater(max(c[0] for c in corners), WIDTH / 2)
        self.assertLess(min(c[1] for c in corners), HEIGHT / 2)
        self.assertGreater(max(c[1] for c in corners), HEIGHT / 2)

    async def test_moving_the_box_right_moves_the_drawing_right(self):
        shifted = np.eye(4)
        # Row-vector convention: USD puts the translation in the last row.
        shifted[3, 0] = 3.0
        self.assertGreater(
            min(c[0] for c in self.project(AHEAD, shifted)),
            min(c[0] for c in self.project(AHEAD)),
        )

    async def test_a_farther_box_is_smaller_on_screen(self):
        near = self.project((-1.0, -1.0, -6.0, 1.0, 1.0, -4.0))
        far = self.project((-1.0, -1.0, -21.0, 1.0, 1.0, -19.0))
        self.assertLess(spread(far), spread(near))

    async def test_a_box_behind_the_camera_is_dropped(self):
        """Dividing by a negative w mirrors the box back into frame."""
        self.assertEqual(self.project((-1.0, -1.0, 9.0, 1.0, 1.0, 11.0)), [])

    async def test_the_twelve_edges_join_the_eight_corners(self):
        self.assertEqual(len(BOX_EDGES), 12)
        self.assertEqual({i for edge in BOX_EDGES for i in edge}, set(range(8)))


class TestDrawWireframe(omni.kit.test.AsyncTestCase):
    """A box the projection rejected must be skipped, not indexed into."""

    def canvas(self):
        image = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
        return image, ImageDraw.Draw(image)

    async def test_a_rejected_box_draws_nothing_instead_of_raising(self):
        image, draw = self.canvas()
        draw_wireframe(draw, [], (255, 0, 0))
        self.assertEqual(image.getbbox(), None)

    async def test_a_projected_box_draws_its_edges(self):
        image, draw = self.canvas()
        corners = [
            (10.0, 10.0),
            (10.0, 60.0),
            (60.0, 10.0),
            (60.0, 60.0),
            (30.0, 30.0),
            (30.0, 80.0),
            (80.0, 30.0),
            (80.0, 80.0),
        ]
        draw_wireframe(draw, corners, (255, 0, 0))
        self.assertIsNotNone(image.getbbox())
