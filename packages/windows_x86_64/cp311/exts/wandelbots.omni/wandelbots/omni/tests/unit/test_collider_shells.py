"""Unit tests for the shared collider point-shell generators.

These are the point sets both the reachability Attach Tool flow and the
collision setup export lower authored colliders into; the key invariant is
circumscription - a shell's convex hull contains the authored shape, it never
sits inside it.
"""

from __future__ import annotations

import math

import omni.kit.test

from wandelbots.omni.utils.collider_shells import (
    RING_SEGMENTS,
    ROUND_SHAPE_INFLATION,
    aabb_corners,
    capsule_shell,
    cone_shell,
    cube_corners,
    cylinder_shell,
    sphere_shell,
)


class TestColliderShells(omni.kit.test.AsyncTestCase):
    async def test_cube_corners_exact(self):
        corners = cube_corners(2.0)
        self.assertEqual(len(corners), 8)
        for corner in corners:
            for coordinate in corner:
                self.assertAlmostEqual(abs(coordinate), 1.0)

    async def test_aabb_corners_span_and_order(self):
        corners = aabb_corners([(0, 0, 0), (1, 2, 3), (-1, 0.5, 1)])
        self.assertEqual(len(corners), 8)
        self.assertEqual(corners[0], (-1, 0, 0))
        self.assertEqual(corners[-1], (1, 2, 3))

    async def test_aabb_corners_empty(self):
        self.assertEqual(aabb_corners([]), [])

    async def test_sphere_shell_circumscribes(self):
        radius, center = 0.5, (1.0, -2.0, 3.0)
        shell = sphere_shell(radius, center)
        self.assertEqual(len(shell), 2 + 5 * RING_SEGMENTS)
        for point in shell:
            self.assertAlmostEqual(
                math.dist(point, center), radius * ROUND_SHAPE_INFLATION
            )
        self.assertGreater(ROUND_SHAPE_INFLATION, 1.0)
        self.assertLess(ROUND_SHAPE_INFLATION, 1.06)

    async def test_capsule_shell_extents(self):
        radius, height = 0.2, 1.0
        shell = capsule_shell(radius, height, "X")
        inflated = radius * ROUND_SHAPE_INFLATION
        xs = [p[0] for p in shell]
        self.assertAlmostEqual(max(xs), height / 2 + inflated)
        self.assertAlmostEqual(min(xs), -(height / 2 + inflated))
        # The cylinder-section rings (latitude 0) sit exactly at +/-height/2
        # with the inflated radius.
        ring0 = [p for p in shell if abs(abs(p[0]) - height / 2) < 1e-9]
        self.assertEqual(len(ring0), 2 * RING_SEGMENTS)
        for point in ring0:
            self.assertAlmostEqual(math.hypot(point[1], point[2]), inflated)

    async def test_cylinder_shell_circumscribes(self):
        radius, height = 0.3, 0.8
        shell = cylinder_shell(radius, height, "Y")
        self.assertEqual(len(shell), 2 * RING_SEGMENTS)
        ring_radius = radius / math.cos(math.pi / RING_SEGMENTS)
        for point in shell:
            self.assertAlmostEqual(abs(point[1]), height / 2)
            self.assertAlmostEqual(math.hypot(point[0], point[2]), ring_radius)
        # Circumscription: the midpoint of a polygon edge must still be at
        # least the true radius away from the axis.
        a, b = shell[0], shell[1]
        midpoint = math.hypot((a[0] + b[0]) / 2, (a[2] + b[2]) / 2)
        self.assertGreaterEqual(midpoint, radius - 1e-9)

    async def test_cone_shell_apex_and_base(self):
        radius, height = 0.25, 0.6
        shell = cone_shell(radius, height, "Z")
        self.assertEqual(len(shell), 1 + RING_SEGMENTS)
        self.assertEqual(shell[0], (0.0, 0.0, height / 2))
        ring_radius = radius / math.cos(math.pi / RING_SEGMENTS)
        for point in shell[1:]:
            self.assertAlmostEqual(point[2], -height / 2)
            self.assertAlmostEqual(math.hypot(point[0], point[1]), ring_radius)
