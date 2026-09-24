"""Batched NOVA inverse kinematics that answers, per pose, its joint-limit margin.

An envelope recompute asks about tens of thousands of poses. The generated client
builds several Python objects per pose, and they stay alive while the request is in
flight. Python's garbage collector promotes them, and every few batches that ends in
a full collection, which stalls Kit's render thread for about 300 ms. Here a batch is
one JSON string out and one array of numbers back.
"""

from __future__ import annotations

import gc
import json

import aiohttp
import numpy as np
from pydantic_core import to_jsonable_python
from wandelbots_api_client.v2.exceptions import ApiException

_INVERSE_KINEMATICS_PATH = "/cells/{cell}/kinematic/inverse"
_JSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


def encode_ik_request(
    request_fields: dict, positions_mm: np.ndarray, orientation_rotvec: list[float]
) -> str:
    """JSON body for NOVA's inverse kinematics, all poses at one orientation.

    ``request_fields`` holds every field but ``tcp_poses``, already in its JSON
    form. The poses are formatted from one flat list of floats, so the body
    costs a handful of Python objects however many poses it carries.
    """
    positions = np.asarray(positions_mm, dtype=np.float64).reshape(-1, 3)
    pose = (
        '{"position":[%r,%r,%r],"orientation":'
        + json.dumps([float(value) for value in orientation_rotvec])
        + "}"
    )
    poses = ",".join([pose] * positions.shape[0]) % tuple(positions.ravel().tolist())
    fields = {key: value for key, value in request_fields.items() if key != "tcp_poses"}
    return json.dumps(to_jsonable_python(fields))[:-1] + ',"tcp_poses":[' + poses + "]}"


def joint_margins(
    response_body: bytes | str, lower: np.ndarray, upper: np.ndarray
) -> np.ndarray:
    """Per pose, how far its best joint solution stays from the joint limits.

    1 is every joint in the middle of its range, 0 is a joint on a limit. The
    tightest joint decides a solution's margin and the loosest solution decides
    the pose's. NaN marks a pose NOVA found no solution for.

    The response holds a list per solution. The collector stays off while they
    exist, so none of them is promoted into an older generation; they are gone
    again before it is switched back on.
    """
    collector_was_on = gc.isenabled()
    gc.disable()
    try:
        joints = json.loads(response_body)["joints"]
        margins = np.full(len(joints), np.nan)
        owners = [pose for pose, solutions in enumerate(joints) for _ in solutions]
        rows = [solution for solutions in joints for solution in solutions]
        del joints
        if rows:
            solution_margins = _solution_margins(np.asarray(rows), lower, upper)
            del rows
            best = np.full(margins.shape[0], -np.inf)
            np.maximum.at(best, np.asarray(owners), solution_margins)
            solved = np.isfinite(best)
            margins[solved] = best[solved]
    finally:
        if collector_was_on:
            gc.enable()
    return margins


def _solution_margins(
    solutions: np.ndarray, lower: np.ndarray, upper: np.ndarray
) -> np.ndarray:
    lower = np.asarray(lower, dtype=np.float64)
    upper = np.asarray(upper, dtype=np.float64)
    half_span = np.maximum((upper - lower) / 2.0, 1e-9)
    distance = np.minimum(solutions - lower, upper - solutions) / half_span
    return np.clip(distance.min(axis=1), 0.0, 1.0)


class InverseKinematicsProbe:
    """Posts pre-encoded IK requests with the api client's host, token and TLS."""

    def __init__(self, api_client):
        self._api_client = api_client
        self._session: aiohttp.ClientSession | None = None

    async def joint_margins(
        self, cell: str, body: str, lower: np.ndarray, upper: np.ndarray
    ) -> np.ndarray:
        """See ``joint_margins``. Raises ``ApiException`` on an error status."""
        method, url, headers, _body, _post = self._api_client.param_serialize(
            method="POST",
            resource_path=_INVERSE_KINEMATICS_PATH,
            path_params={"cell": cell},
            header_params=dict(_JSON_HEADERS),
            auth_settings=["BearerAuth"],
        )
        async with self._client_session().request(
            method, url, headers=headers, data=body
        ) as response:
            payload = await response.read()
            if not 200 <= response.status <= 299:
                raise ApiException(
                    status=response.status,
                    reason=response.reason,
                    body=payload.decode(errors="replace"),
                )
        return joint_margins(payload, lower, upper)

    def _client_session(self) -> aiohttp.ClientSession:
        # Created on first use: an aiohttp session binds to the running loop.
        # trust_env keeps proxy settings the same as the generated client's.
        if self._session is None:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    ssl=self._api_client.rest_client.ssl_context
                ),
                trust_env=True,
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
