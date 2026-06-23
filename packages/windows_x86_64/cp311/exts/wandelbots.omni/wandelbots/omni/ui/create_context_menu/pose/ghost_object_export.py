"""Store the scene's ghost objects to NOVA object storage.

Mirrors the trajectory planner export (``planning_orchestrator._store_to_nova``):
the flattened ghost-object structure is stored under the object-storage key
``ghost-objects`` as a JSON string.
"""

from __future__ import annotations

import json

import carb
import omni.kit.notification_manager as nm
import wandelbots_api_client.v2 as wb_v2

GHOST_OBJECTS_STORAGE_KEY = "ghost-objects"


async def store_ghost_objects_to_nova(api_client: wb_v2.ApiClient, cell: str) -> bool:
    """Store all scene ghost objects to NOVA object storage under ``ghost-objects``.

    The value is the JSON string of :func:`build_ghost_objects_export`. Returns
    ``True`` on success, ``False`` otherwise (failures are logged and surfaced as a
    notification but never raised, to keep the calling UI flow alive).
    """
    from wandelbots.omni.router.v2.teaching import build_ghost_objects_export

    try:
        exported = build_ghost_objects_export()
        payload_bytes = json.dumps(exported.model_dump()).encode("utf-8")
        await wb_v2.StoreObjectApi(api_client).store_object(
            cell=cell,
            key=GHOST_OBJECTS_STORAGE_KEY,
            any_value=payload_bytes,
        )
        count = len(exported.ghost_objects)
        carb.log_info(
            f"NOVA store: stored {count} ghost object(s) under "
            f"key '{GHOST_OBJECTS_STORAGE_KEY}' in cell '{cell}'."
        )
        nm.post_notification(
            f"Exported {count} ghost object(s) to NOVA.",
            duration=4.0,
        )
        return True
    except Exception as exc:
        carb.log_warn(f"NOVA store: failed to store ghost objects: {exc}")
        nm.post_notification(
            f"Failed to export ghost objects to NOVA: {exc}",
            duration=5.0,
            status=nm.NotificationStatus.WARNING,
        )
        return False
