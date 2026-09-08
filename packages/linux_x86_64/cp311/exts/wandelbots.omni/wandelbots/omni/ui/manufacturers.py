from __future__ import annotations

import re
from collections.abc import Iterable

# Lowercase manufacturer prefix -> display label. Model names and controller
# types share the prefix once lowercased. Only the casing and spelling of
# known manufacturers live here; display_label() title-cases the rest, so a
# manufacturer NOVA adds later still shows up.
_PREFIX_TO_LABEL: dict[str, str] = {
    "abb": "ABB",
    "fanuc": "FANUC",
    "kuka": "KUKA",
    "staubli": "Staeubli",
    "techman": "Techman",
    "universalrobots": "Universal Robots",
    "yaskawa": "Yaskawa",
}


def display_label(prefix: str) -> str:
    """Friendly label for a manufacturer prefix (title-cased fallback)."""
    key = prefix.lower()
    return _PREFIX_TO_LABEL.get(key, prefix.title())


def prefix_of_model_name(model_name: str) -> str:
    """Lowercase manufacturer prefix of a motion group model name.

    Canonical names are 'Manufacturer_Model'; display variants may use other
    separators, so take the leading alphanumeric run.
    """
    match = re.match(r"[A-Za-z0-9]+", model_name)
    return match.group(0).lower() if match else model_name.lower()


def manufacturers_from_model_names(model_names: Iterable[str]) -> dict[str, str]:
    """Display label -> prefix for the manufacturers present in *model_names*
    (as returned by getMotionGroupModels), sorted by label."""
    prefixes = {prefix_of_model_name(name) for name in model_names if name}
    return {
        display_label(prefix): prefix for prefix in sorted(prefixes, key=display_label)
    }


def manufacturers_from_controller_types(types: Iterable[str]) -> dict[str, str]:
    """Display label -> prefix for 'manufacturer-model' controller types
    (as returned by getRobotConfigurations), sorted by label."""
    prefixes = {
        controller_type.split("-", 1)[0].lower()
        for controller_type in types
        if controller_type
    }
    return {
        display_label(prefix): prefix for prefix in sorted(prefixes, key=display_label)
    }


# Display label -> prefix, the fallback for UI built before a NOVA catalog
# has been fetched.
MANUFACTURER_PREFIXES: dict[str, str] = {
    label: prefix for prefix, label in _PREFIX_TO_LABEL.items()
}

MANUFACTURERS: list[str] = list(MANUFACTURER_PREFIXES)
