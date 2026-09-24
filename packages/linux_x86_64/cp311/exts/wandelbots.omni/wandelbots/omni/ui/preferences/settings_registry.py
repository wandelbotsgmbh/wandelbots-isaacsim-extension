"""What a setting accepts, beyond its Python type.

A type alone does not say what a setting may hold. `motion_command` is a
string, but the toolbar only understands three of them; `max_joint_configs` is
an int, but the overlay slices its joint list with it and its own control caps
that at 18. An unconstrained field on this page could write either into a state
the owning code does not expect, and nothing would report it.

The constraints are declared here rather than guessed from the value, and the
page turns them into the editor Kit already has for them: a combo box for a
fixed set, a hard-ranged field for a range.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Constraint:
    """What a setting is allowed to hold."""

    choices: Optional[tuple[str, ...]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None


_GHOST_TEACHING = "/persistent/exts/wandelbots.omni/ghost_teaching"

# Keyed by full carb path. Each entry names where the limit comes from, so a
# changed limit can be traced back to the code that enforces it.
CONSTRAINTS: dict[str, Constraint] = {
    # SettingsModel.motion_command is a Literal of these three.
    f"{_GHOST_TEACHING}/motion_command": Constraint(
        choices=("joint_p2p", "cartesian_p2p", "line")
    ),
    # The tool's own control offers 1..18, and the overlay uses the value
    # directly as a slice limit.
    f"{_GHOST_TEACHING}/max_joint_configs": Constraint(minimum=1, maximum=18),
}


def constraint_for(path: str) -> Optional[Constraint]:
    return CONSTRAINTS.get(path)
