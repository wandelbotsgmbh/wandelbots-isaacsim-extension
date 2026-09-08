"""Find the stage prim of a motion group that NOVA knows by cell, controller
and motion group name."""

from __future__ import annotations

from urllib.parse import urlsplit

import carb
import omni.usd
from pxr import Usd

from .motion_group_service import get_motion_group_service


def host_key(value: str | None, secure: bool | None = None) -> tuple[str, int] | None:
    """Hostname and port of a host string or URL, or None if it cannot be
    parsed. Accepts 'http://host/api/v2', 'host:8080' and 'host'.

    The port belongs to the identity: instances run on the same host with
    different ports, and a hostname-only comparison would merge them. A missing
    port comes from the URL scheme, or from `secure` for a bare host.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value) if "//" in value else urlsplit(f"//{value}")
        hostname = parsed.hostname
        port = parsed.port  # raises ValueError for a malformed port
    except ValueError:
        return None
    if not hostname:
        return None
    if port is None:
        if parsed.scheme:
            secure = parsed.scheme == "https"
        port = 443 if secure else 80
    return (hostname, port)


def find_motion_group_prim(
    cell: str,
    controller: str,
    motion_group: str,
    host: str | None = None,
    prim_path: str | None = None,
) -> Usd.Prim | None:
    """Stage prim of the motion group registered for a NOVA identity, or None.

    `prim_path` skips the registry lookup when the caller already knows the
    robot. `host` picks between robots of different instances that reuse the
    same identifiers, which is the common case for default cell and controller
    names. A host mismatch only demotes a candidate, because a prim can carry
    the host of an earlier connection.
    """
    stage = omni.usd.get_context().get_stage()
    if prim_path:
        prim = stage.GetPrimAtPath(prim_path)
        if prim and prim.IsValid():
            return prim
        carb.log_warn(f"Motion group prim '{prim_path}' is not valid.")
        return None

    service = get_motion_group_service()
    if service is None:
        return None
    wanted_host = host_key(host)
    matches: list[str] = []
    host_matches: list[str] = []
    for candidate_path in service.get_all_motion_group_prim_paths():
        configuration = service.get_motion_group_configuration(candidate_path)
        if not configuration:
            continue
        stream = configuration.motion_stream_configuration
        if (
            stream.cell != cell
            or stream.controller != controller
            or stream.motion_group != motion_group
        ):
            continue
        matches.append(configuration.prim_path)
        if (
            wanted_host
            and host_key(stream.host, stream.secure_connection) == wanted_host
        ):
            host_matches.append(configuration.prim_path)

    if wanted_host and host_matches:
        matches = host_matches
    elif wanted_host and matches:
        carb.log_warn(
            f"No stage motion group for {cell}/{controller}/{motion_group} matches "
            f"instance '{wanted_host[0]}:{wanted_host[1]}'; using an "
            "identifier-only match instead."
        )
    if not matches:
        return None
    if len(matches) > 1:
        carb.log_warn(
            f"Multiple stage motion groups match {cell}/{controller}/{motion_group}: "
            f"{matches}; using the first."
        )
    prim = stage.GetPrimAtPath(matches[0])
    return prim if prim and prim.IsValid() else None
