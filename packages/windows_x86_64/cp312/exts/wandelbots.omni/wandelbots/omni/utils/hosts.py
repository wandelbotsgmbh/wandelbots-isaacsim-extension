"""Host string helpers.

One NOVA instance appears under differently-shaped strings: the portal keeps the
scheme, a manually added on-prem host has none, and ``MotionStreamConfiguration``
strips it before the host reaches a prim. Every host comparison therefore has to
normalize first.
"""

import urllib.parse


def strip_host_scheme(host: str) -> str:
    """Drop a leading ``http://``/``https://`` from *host*, leaving the rest as is."""
    parsed = urllib.parse.urlparse(host)
    if parsed.scheme not in ("http", "https"):
        return host
    # netloc = hostname[:port], path as fallback for scheme-less URLs
    return parsed.netloc or parsed.path


def normalize_host(host: str | None) -> str:
    """Return *host* reduced to the comparable ``hostname[:port]`` form.

    Comparison only; what gets written to a prim is unaffected.
    """
    if not host:
        return ""
    return strip_host_scheme(host.strip()).rstrip("/").lower()
