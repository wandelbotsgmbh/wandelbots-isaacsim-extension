"""URL-aware path helpers for "write next to the current scene" features.

``get_stage_url()`` returns a plain OS path, a Nucleus URL or a ``file:`` URL.
Telling them apart with ``"://" in location`` gets the last one wrong - it has no
``//``, so ``os.path.dirname("file:/D:/scenes/main.usd")`` yields ``"file:"``.
Instead: recognize any scheme, resolve ``file:`` URLs to a real OS path, and
combine remote URLs through ``omni.client``.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from urllib.request import url2pathname

import omni.client

# A scheme needs at least two characters before the colon, so a Windows drive
# letter ("D:/scenes") is not mistaken for one.
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]+):")


def url_scheme(location: str | None) -> str | None:
    """Return the lower-cased URL scheme of *location*, or None if it has none."""
    if not location:
        return None
    match = _SCHEME_RE.match(location)
    return match.group(1).lower() if match else None


def is_url(location: str | None) -> bool:
    """Whether *location* is a URL rather than an OS filesystem path."""
    return url_scheme(location) is not None


def normalize_location(location: str | None) -> str:
    """Return *location* as an OS path when it denotes a local file, else as is.

    ``file:`` URLs are converted, so callers only ever see a plain path or a
    genuinely remote URL and can branch on :func:`is_url`.
    """
    if not location:
        return ""
    if url_scheme(location) != "file":
        return location
    parsed = urllib.parse.urlparse(location)
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        # file://server/share/... - keep it a UNC path.
        return url2pathname(f"//{parsed.netloc}{parsed.path}")
    return url2pathname(parsed.path)


def parent_location(location: str | None) -> str:
    """Return the directory containing *location*, for a URL or an OS path."""
    location = normalize_location(location)
    if not location:
        return ""
    if is_url(location):
        # Trailing component is the file name; drop it but keep the scheme/host.
        base, _, _ = location.rstrip("/").rpartition("/")
        return base or location
    return os.path.dirname(location)


def join_location(directory: str, name: str) -> str:
    """Append *name* to *directory*, for a URL or an OS path."""
    if not name:
        return directory
    if is_url(directory):
        return omni.client.combine_urls(f"{directory.rstrip('/')}/", name)
    return os.path.join(directory, name)


def sibling_location(location: str | None, name: str) -> str:
    """Return the location of *name* next to *location*."""
    return join_location(parent_location(location), name)
