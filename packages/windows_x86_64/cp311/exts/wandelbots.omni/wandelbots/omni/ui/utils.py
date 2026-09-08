import carb
from typing import Callable, Any
import asyncio
import os
import weakref
import omni.ext
from omni.kit.menu.utils import MenuItemDescription


def get_icon(icon_name: str) -> str:
    path = f"{os.path.dirname(__file__)}/../assets/icons/{icon_name}"
    return path


# Global "UI busy" gate. Custom-handler widgets (the IconButton click, the
# CollapsibleSection toggle) consult this to ignore input while a blocking
# operation runs, because omni.ui's ``enabled`` does not reliably gate widgets
# that drive clicks via their own ``set_mouse_*_fn`` handlers or that sit deep
# inside a ScrollingFrame.
_ui_busy = False


def set_ui_busy(busy: bool) -> None:
    global _ui_busy
    _ui_busy = bool(busy)


def is_ui_busy() -> bool:
    return _ui_busy


def weak_cb(owner: object, method_name: str, *bound_args) -> Callable:
    """Build a widget callback that holds only a weak reference to ``owner``.

    Callbacks stored on widgets inside a window frame must not strongly
    reference the window's builder, or the window can never be GC'd across
    extension reloads. Returns a callable that resolves the weakref, no-ops if
    the owner is gone, and forwards both ``bound_args`` and any args omni.ui
    passes at call time to ``owner.method_name``.
    """
    ref = weakref.ref(owner)

    def _invoke(*call_args):
        target = ref()
        if target is None:
            return None
        return getattr(target, method_name)(*bound_args, *call_args)

    return _invoke


def defer_call(callback: Callable[[], Any]) -> None:
    """
    Defer a function call to be executed in the next UI frame.

    This function schedules a callback to be executed in the next frame,
    which is useful for UI updates that need to happen after the current
    frame processing is complete.

    Args:
        callback: The function to call in the next frame

    Example:
        def update_ui():
            # Update UI elements
            pass

        defer_call(update_ui)  # Execute in next frame
    """
    if not callable(callback):
        carb.log_error("defer_call: callback must be callable")
        return

    try:
        # Use asyncio to defer to next frame
        async def _deferred_execution():
            await asyncio.sleep(0)  # Yield control to allow frame to complete
            try:
                callback()
            except Exception as e:
                carb.log_error(f"Error in deferred callback: {e}")

        # Schedule the deferred execution
        asyncio.ensure_future(_deferred_execution())

    except Exception as e:
        carb.log_error(f"Failed to defer call: {e}")
        # Fallback: execute immediately if deferring fails
        try:
            callback()
        except Exception as callback_error:
            carb.log_error(f"Error in fallback callback execution: {callback_error}")


def make_menu_item_description(
    ext_id: str,
    name: str,
    onclick_fun,
    action_name: str = "",
    header: str | None = None,
    glyph: str = "",
    on_ticked_fn=None,
) -> MenuItemDescription:
    action_unique = f"{ext_id.replace(' ', '_')}{name.replace(' ', '_')}{action_name.replace(' ', '_')}"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(ext_id, action_unique)
    action_registry.register_action(ext_id, action_unique, onclick_fun)
    return MenuItemDescription(
        name=name,
        header=header,
        glyph=glyph,
        onclick_action=(ext_id, action_unique),
        ticked_fn=on_ticked_fn,
    )
