from typing_extensions import Literal

import carb
import carb.eventdispatcher

from wandelbots.omni.ui.utils import set_ui_busy

OPEN_INSTANCES_PANEL = "wandelbots.omni.OPEN_INSTANCES_PANEL"
MOTION_GROUP_CONNECTION_CHANGED = "wandelbots.omni.MOTION_GROUP_CONNECTION_CHANGED"
UI_BUSY_CHANGED = "wandelbots.omni.UI_BUSY_CHANGED"


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


def push_open_instances_panel() -> None:
    carb.eventdispatcher.get_eventdispatcher().dispatch_event(OPEN_INSTANCES_PANEL)


def subscribe_to_open_instances_panel(callback):
    return carb.eventdispatcher.get_eventdispatcher().observe_event(
        event_name=OPEN_INSTANCES_PANEL,
        on_event=lambda event: callback(),
        observer_name="open_instances_panel_sub",
    )


def push_ui_busy_changed(busy: bool, message: str = "") -> None:
    """Dispatch a ``UI_BUSY_CHANGED`` event.

    Args:
        busy: ``True`` while a blocking operation runs, ``False`` once done.
        message: Optional status text describing the current operation.
    """
    set_ui_busy(busy)
    carb.eventdispatcher.get_eventdispatcher().dispatch_event(
        UI_BUSY_CHANGED,
        payload={"busy": busy, "message": message},
    )


def subscribe_to_ui_busy_changed(callback):
    return carb.eventdispatcher.get_eventdispatcher().observe_event(
        event_name=UI_BUSY_CHANGED,
        on_event=lambda event: callback(event.payload),
        observer_name="ui_busy_changed_sub",
    )
