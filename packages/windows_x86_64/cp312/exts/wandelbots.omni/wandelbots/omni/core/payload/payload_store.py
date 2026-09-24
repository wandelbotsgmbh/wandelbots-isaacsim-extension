"""Payloads in the NOVA object store.

A payload is stored as the plain JSON of the planner's ``Payload`` model under
``payload/<name>``, so the stored object can be dropped into
``MotionGroupSetup.payload`` as it is.
"""

from __future__ import annotations

import asyncio

import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models
from wandelbots_api_client.v2.exceptions import ApiException

from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVAInstance

PAYLOAD_KEY_PREFIX = "payload/"


class PayloadStoreError(RuntimeError):
    """The instance cannot be used as a store: no cell, or no API client."""


# What a store round trip can raise: the request itself, a missing cell, the
# network, and a stored object that does not parse as a payload (ValueError
# covers the JSON, the encoding and the pydantic validation).
STORE_ERRORS = (
    ApiException,
    PayloadStoreError,
    OSError,
    asyncio.TimeoutError,
    ValueError,
)


def payload_key(name: str) -> str:
    return f"{PAYLOAD_KEY_PREFIX}{name}"


def payload_name_from_key(key: str) -> str | None:
    if not key.startswith(PAYLOAD_KEY_PREFIX):
        return None
    name = key[len(PAYLOAD_KEY_PREFIX) :]
    return name or None


async def store_payload(
    store_api: wb_v2.StoreObjectApi, cell: str, payload: wb_v2_models.Payload
) -> None:
    await store_api.store_object(
        cell=cell,
        key=payload_key(payload.name),
        any_value=payload.to_json().encode("utf-8"),
    )


async def list_payload_names(store_api: wb_v2.StoreObjectApi, cell: str) -> list[str]:
    keys = await store_api.list_all_object_keys(cell=cell)
    names = (payload_name_from_key(key) for key in keys or [])
    return sorted(name for name in names if name)


async def load_payload(
    store_api: wb_v2.StoreObjectApi, cell: str, name: str
) -> wb_v2_models.Payload:
    raw = await store_api.get_object(cell=cell, key=payload_key(name))
    return wb_v2_models.Payload.from_json(bytes(raw).decode("utf-8"))


async def _resolve_cell(instance: NOVAInstance) -> str:
    cell = await get_instances_api().fetch_primary_cell_id(instance)
    if cell is None:
        raise PayloadStoreError(
            f"Instance '{instance.display_name}' has no cells available."
        )
    return cell


def _open_api_client(instance: NOVAInstance) -> wb_v2.ApiClient:
    api_client = get_instances_api().create_api_client_for_instance(instance)
    if api_client is None:
        raise PayloadStoreError(
            f"Could not create an API client for '{instance.display_name}'."
        )
    return api_client


async def store_payload_on_instance(
    instance: NOVAInstance, payload: wb_v2_models.Payload
) -> str:
    """Store the payload in the instance's cell and return that cell's name."""
    cell = await _resolve_cell(instance)
    async with _open_api_client(instance) as api_client:
        await store_payload(wb_v2.StoreObjectApi(api_client), cell, payload)
    return cell


async def list_payload_names_on_instance(instance: NOVAInstance) -> list[str]:
    cell = await _resolve_cell(instance)
    async with _open_api_client(instance) as api_client:
        return await list_payload_names(wb_v2.StoreObjectApi(api_client), cell)


async def load_payload_from_instance(
    instance: NOVAInstance, name: str
) -> wb_v2_models.Payload:
    cell = await _resolve_cell(instance)
    async with _open_api_client(instance) as api_client:
        return await load_payload(wb_v2.StoreObjectApi(api_client), cell, name)
