"""Which carb settings the Preferences window shows, and how each is edited.

carb keeps settings in a tree of dicts. This module turns the branches the
extension owns, plus the few foreign settings declared in FOREIGN_SETTINGS,
into a flat list of leaves and decides which editor each value gets, so the
window itself only has to render what it is handed. Nothing here
imports carb or omni.ui, which is what makes the rules testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Settings the extension owns live under a node named after it. The trailing dot
# is deliberate: it keeps "wandelbots.omni" in and any unrelated "wandelbotsX"
# out.
SETTING_PREFIX = "wandelbots."

# The containers carb holds those nodes under. "/persistent" is where a setting
# that survives a restart lives, and Kit keeps extension settings one level
# deeper again under "exts".
SETTING_ROOTS = ("/exts", "/persistent/exts", "/", "/persistent")

BOOL = "bool"
NUMBER = "number"
STRING = "string"
TEXT = "text"
COLOR = "color"
READ_ONLY = "read_only"

# A value behind one of these words is a credential. It is listed so its
# presence is visible, but never rendered in clear text.
_SECRET_WORDS = ("token", "secret", "password", "credential", "api_key", "apikey")


@dataclass(frozen=True)
class Setting:
    """One editable carb setting."""

    path: str
    value: object
    kind: str
    # Set only for a declared ForeignSetting, whose path names no extension
    # and whose key reads badly as words.
    group: str = ""
    title: str = ""
    description: str = ""

    @property
    def extension_name(self) -> str:
        return self.group or split_extension_path(self.path)[0]

    @property
    def key(self) -> str:
        """The path below the extension - what differs within a group."""
        return split_extension_path(self.path)[1]

    @property
    def label(self) -> str:
        """The row label. The raw key stays available as the row's tooltip."""
        return self.title or readable_words(self.key)

    @property
    def tooltip(self) -> str:
        if not self.description:
            return self.path
        return f"{self.description}\n{self.path}"


@dataclass(frozen=True)
class ForeignSetting:
    """A setting outside the extension's own tree that the page still shows."""

    path: str
    label: str
    description: str


FOREIGN_GROUP = "Isaac Sim"

# Settings other extensions own, listed because a Wandelbots workflow needs
# them at hand. Only these paths are read; nothing else outside a
# `wandelbots.` node is. Kit does not persist them, so a change lasts until
# Isaac Sim restarts and the app's .kit file sets them again.
FOREIGN_SETTINGS = (
    # A gessbot driven through Navitec needs the timeline to follow the wall
    # clock.
    ForeignSetting(
        path="/app/player/useFixedTimeStepping",
        label="Use Fixed Time Stepping",
        description=(
            "On: every frame advances the timeline by exactly one time code. "
            "Off: the timeline follows the wall clock. Resets on restart."
        ),
    ),
)


def collect_settings(tree_of: callable) -> list[Setting]:
    """Every setting under a `wandelbots.` node, sorted by path.

    `tree_of` returns the raw value carb holds at a path (``settings.get``), so
    the caller owns the carb dependency and this stays testable with a dict.
    """
    settings: list[Setting] = []
    seen: set[str] = set()
    for root in SETTING_ROOTS:
        container = tree_of(root)
        if not isinstance(container, dict):
            continue
        for name, subtree in container.items():
            if not name.startswith(SETTING_PREFIX):
                continue
            base = f"{root.rstrip('/')}/{name}"
            for path, value in flatten_settings(subtree, base):
                if path in seen:
                    continue
                seen.add(path)
                if is_secret(path):
                    # Tokens and passwords are left out entirely, by decision:
                    # there is nothing to configure about them, and a
                    # preferences page is the last place they should be
                    # readable. The same goes for the credential store under
                    # /persistent/wandelbots, which SETTING_PREFIX already
                    # excludes because its node carries no dot.
                    continue
                settings.append(
                    Setting(
                        path=path,
                        value=value,
                        kind=classify_setting(path, value),
                    )
                )
    return sorted(settings, key=lambda setting: setting.path)


def collect_foreign_settings(value_of: callable) -> list[Setting]:
    """The declared FOREIGN_SETTINGS that carb holds, in declaration order.

    `value_of` returns the value carb holds at a path (``settings.get``). A
    path with no value is skipped: the app that would author it is not the one
    running, and writing it from here would invent a setting nothing reads.
    """
    settings: list[Setting] = []
    for declared in FOREIGN_SETTINGS:
        value = value_of(declared.path)
        if value is None:
            continue
        settings.append(
            Setting(
                path=declared.path,
                value=value,
                kind=classify_setting(declared.path, value),
                group=FOREIGN_GROUP,
                title=declared.label,
                description=declared.description,
            )
        )
    return settings


def flatten_settings(subtree: object, base_path: str) -> list[tuple[str, object]]:
    """Leaves of a carb settings subtree as (path, value) pairs.

    A dict is a branch, everything else is a leaf - including a list, which carb
    uses for arrays and which must not be walked as if its indices were names.
    """
    if not isinstance(subtree, dict):
        return [(base_path, subtree)]
    leaves: list[tuple[str, object]] = []
    for name, value in subtree.items():
        leaves.extend(flatten_settings(value, f"{base_path}/{name}"))
    return leaves


def classify_setting(path: str, value: object) -> str:
    """The editor a value gets.

    bool is checked before number because a bool is an int in Python, and a
    toggle rendered as a number field would write 0/1 into a flag.
    """
    if isinstance(value, bool):
        return BOOL
    if isinstance(value, (int, float)):
        return NUMBER
    if isinstance(value, str):
        # Colours are stored as hex strings here, not as arrays - see
        # float_array_to_hex, which is what writes them.
        if _is_color_name(path) and is_hex_color(value):
            return COLOR
        return TEXT if "\n" in value else STRING
    if _is_color(path, value):
        return COLOR
    # A list of strings, a dict carb handed back as a leaf, None: shown, not
    # edited. Guessing an editor for these writes the wrong type into carb.
    return READ_ONLY


def is_secret(path: str) -> bool:
    lowered = path.lower()
    return any(word in lowered for word in _SECRET_WORDS)


def _is_color(path: str, value: object) -> bool:
    """RGB or RGBA, either by the name or by every component being normalized.

    A plain three-number array can be anything - a position, a scale - so a
    colour is only assumed when the name says so or the range makes nothing else
    plausible.
    """
    if not isinstance(value, (list, tuple)) or len(value) not in (3, 4):
        return False
    if any(
        isinstance(part, bool) or not isinstance(part, (int, float)) for part in value
    ):
        return False
    if _is_color_name(path):
        return True
    return all(0.0 <= float(part) <= 1.0 for part in value)


def _is_color_name(path: str) -> bool:
    return path.lower().rsplit("/", 1)[-1].endswith(("color", "colour"))


def is_hex_color(text: str) -> bool:
    """Whether a string is "#RRGGBB" or "#RRGGBBAA", with or without the hash."""
    digits = text.lstrip("#")
    if len(digits) not in (6, 8):
        return False
    return all(character in "0123456789abcdefABCDEF" for character in digits)


def split_extension_path(path: str) -> tuple[str, str]:
    """A setting path as (extension name, path below it).

    Both "/exts/<name>/..." and "/persistent/exts/<name>/..." belong to the same
    extension, so a setting is grouped by the extension and named by the rest.
    A path with no "exts" segment keeps its whole self as the name.
    """
    segments = [segment for segment in path.split("/") if segment]
    if "exts" in segments:
        index = segments.index("exts")
        if index + 1 < len(segments):
            return segments[index + 1], "/".join(segments[index + 2 :])
    return "", "/".join(segments)


def group_title(extension_name: str, title: str | None) -> str:
    """Header for one extension's settings.

    The readable title is what a reader recognizes, the name is what the
    setting path actually says - so show both, and fall back to the name alone
    when the extension is not loaded and has no title to offer.
    """
    if not extension_name:
        return "Other"
    return f"{title} ({extension_name})" if title else extension_name


def group_by_extension(settings: list[Setting]) -> dict[str, list[Setting]]:
    """Settings by extension name, each group in path order."""
    groups: dict[str, list[Setting]] = {}
    for setting in settings:
        groups.setdefault(setting.extension_name, []).append(setting)
    return groups


# Words that read wrong when only their first letter is capitalized.
_ACRONYMS = frozenset(
    {"mdl", "usd", "api", "url", "http", "https", "ui", "io", "ik", "tcp", "dof", "id"}
)


def readable_words(key: str) -> str:
    """Key segments as capitalized words: "a_b/cD" -> "A B C D".

    The words come from the whole key, not its last segment: one extension
    carries several `overlay_color`, and three rows all reading "Overlay Color"
    would say nothing about which is which.
    """
    words: list[str] = []
    for segment in re.split(r"[/_]", key):
        words.extend(_split_camel_case(segment))
    return " ".join(_cased(word) for word in words if word)


def _split_camel_case(segment: str) -> list[str]:
    # An all-caps run stays whole ("USDPreview" -> "USD", "Preview") so an
    # acronym is not torn into single letters.
    return re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+", segment)


def _cased(word: str) -> str:
    return word.upper() if word.lower() in _ACRONYMS else word.capitalize()


def duplicate_keys(settings: list[Setting]) -> list[str]:
    """Keys that one extension holds in both the volatile and persistent tree.

    Whichever tree a setting lives in is the owning code's choice, so the two
    should never hold the same key - but nothing enforces it, and if it happens
    the page shows two rows with the same label writing to different places.
    Reported rather than silently merged: merging would hide a real mistake in
    whichever code authored the second one.
    """
    seen: set[tuple[str, str]] = set()
    duplicates: list[str] = []
    for setting in settings:
        identity = (setting.extension_name, setting.key)
        if identity in seen and setting.key not in duplicates:
            duplicates.append(setting.key)
        seen.add(identity)
    return duplicates
