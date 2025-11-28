import carb
from typing import Callable, Any
import asyncio
import os


def get_icon(icon_name: str) -> str:
    path = f"{os.path.dirname(__file__)}/../assets/icons/{icon_name}"
    carb.log_verbose(f"Loading icon from: {path}")
    return path


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
