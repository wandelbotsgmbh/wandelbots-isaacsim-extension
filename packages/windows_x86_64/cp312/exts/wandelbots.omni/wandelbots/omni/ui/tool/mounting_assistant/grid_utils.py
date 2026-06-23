"""Grid pattern generation utilities for the mounting assistant."""

from __future__ import annotations


def generate_grid_pattern(
    center_mm: list[float],
    spacing_mm: float,
    range_mm: float,
    axes: tuple[bool, bool, bool],
) -> list[list[float]]:
    """Generate a 3D grid of candidate mounting positions around center.

    Each enabled axis gets steps from -range_mm to +range_mm with the given spacing.
    Disabled axes keep the center coordinate fixed.
    """
    step = max(1.0, spacing_mm)
    half = max(step, range_mm)
    steps: list[list[float]] = []
    for i, enabled in enumerate(axes):
        if enabled:
            n = max(1, round(half / step))
            coords = [center_mm[i] + k * step for k in range(-n, n + 1)]
        else:
            coords = [center_mm[i]]
        steps.append(coords)

    radius_sq = half * half
    positions: list[list[float]] = []
    for x in steps[0]:
        for y in steps[1]:
            for z in steps[2]:
                dx = x - center_mm[0]
                dy = y - center_mm[1]
                dz = z - center_mm[2]
                if dx * dx + dy * dy + dz * dz <= radius_sq:
                    positions.append([x, y, z])
    # Sort by distance from center so the center candidate is analyzed first
    positions.sort(
        key=lambda p: (
            (p[0] - center_mm[0]) ** 2
            + (p[1] - center_mm[1]) ** 2
            + (p[2] - center_mm[2]) ** 2
        )
    )
    return positions


def count_grid_points(
    spacing_mm: float, range_mm: float, axes: tuple[bool, bool, bool]
) -> int:
    """Count the number of grid points without generating positions."""
    return len(generate_grid_pattern([0, 0, 0], spacing_mm, range_mm, axes))
