from omni.kit.viewport.utility import get_active_viewport_window
from wandelbots.omni.ui.overlay.overlay import ViewportOverlay


class OverlayRegistry:
    def __init__(self):
        self._overlays: dict[str, ViewportOverlay] = {}

    def register_overlay(self, name: str, overlay: ViewportOverlay):
        self._overlays[name] = overlay
        overlay.attach_to_viewport(get_active_viewport_window())

    def get_overlay(self, name: str) -> ViewportOverlay | None:
        return self._overlays.get(name)

    def unregister_overlay(self, name: str):
        if name in self._overlays:
            del self._overlays[name]

    def list_overlays(self) -> list[str]:
        return list(self._overlays.keys())

    def clear_overlays(self):
        self._overlays.clear()


overlay_registry = OverlayRegistry()


def get_overlay_registry() -> OverlayRegistry:
    return overlay_registry
