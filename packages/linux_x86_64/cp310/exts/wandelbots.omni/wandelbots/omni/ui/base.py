import omni.ui as ui


class BaseUIBuilder:
    """Base class for Wandelbots UI components"""

    def __init__(self, title: str, width: int = 800, height: int = 600):
        self._window = None
        self._title = title
        self._width = width
        self._height = height
        self._dismissed = False

    def setup(self):
        """Initialize the UI window"""
        if self._window is None:
            self._window = ui.Window(
                self._title, width=self._width, height=self._height
            )
            self._window.set_visibility_changed_fn(self.on_window_dismissed)
            self._window.visible = True

    def build_ui(self):
        """Display the UI component"""
        raise NotImplementedError("Subclasses must implement this method")

    def _cleanup(self):
        """Cleanup resources"""
        if self._window:
            self._window.visible = False
            self._window.destroy()
            self._window = None

    def close(self):
        self._cleanup()

    def on_window_dismissed(self, is_visible):
        """Handle window dismiss"""
        self._dismissed = not is_visible

    @property
    def dismissed(self):
        """Check if the window has been dismissed"""
        return self._dismissed
