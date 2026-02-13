import abc
from omni.kit.viewport.window import ViewportWindow


class ViewportOverlay(abc.ABC):
    @abc.abstractmethod
    def attach_to_viewport(self, viewport_window: ViewportWindow):
        pass
