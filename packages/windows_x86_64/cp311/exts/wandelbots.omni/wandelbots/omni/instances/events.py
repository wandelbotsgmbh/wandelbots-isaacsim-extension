from typing_extensions import Literal

import carb
import carb.eventdispatcher

MOTION_GROUP_CONNECTION_CHANGED = "wandelbots.omni.MOTION_GROUP_CONNECTION_CHANGED"


def push_motion_group_connection_changed(
    host: str = "",
    cell: str = "",
    controller: str = "",
    motion_group: str = "",
    prim_path: str = "",
    action: Literal["connected", "disconnected"] = "connected",
) -> None:
    """Dispatch a ``MOTION_GROUP_CONNECTION_CHANGED`` event.

    Args:
        host: The NOVA instance host.
        cell: Name of the cell.
        controller: Name of the controller.
        motion_group: Name of the motion group (e.g. ``0@ur10e``).
        prim_path: Stage prim path of the articulation.
        action: ``"connected"`` or ``"disconnected"``.
    """
    carb.eventdispatcher.get_eventdispatcher().dispatch_event(
        MOTION_GROUP_CONNECTION_CHANGED,
        payload={
            "host": host,
            "cell": cell,
            "controller": controller,
            "motion_group": motion_group,
            "prim_path": prim_path,
            "action": action,
        },
    )
    carb.log_verbose(
        f"Dispatched MOTION_GROUP_CONNECTION_CHANGED: "
        f"host={host}, cell={cell}, controller={controller}, "
        f"motion_group={motion_group}, prim_path={prim_path}, action={action}"
    )


def subscribe_to_motion_group_connection_changed(callback):
    return carb.eventdispatcher.get_eventdispatcher().observe_event(
        event_name=MOTION_GROUP_CONNECTION_CHANGED,
        on_event=lambda event: callback(event.payload),
        observer_name="motion_group_connection_changed_sub",
    )
