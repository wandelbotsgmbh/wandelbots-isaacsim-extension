"""
Wandelbots Asset Browser Delegate - With Drag & Drop
"""

import omni.ui as ui
from omni.kit.browser.core import DetailItem, create_drop_helper
from omni.kit.browser.folder.core import FolderDetailDelegate


class WandelbotsAssetDelegate(FolderDetailDelegate):
    """
    Delegate to show asset item in detail view with drag & drop support
    Args:
        model: Wandelbots asset browser model
    """

    def __init__(self, model):
        super().__init__(model=model)
        self._dragging_url = None

        # Always enable drag and drop for USD files
        self._drop_helper = create_drop_helper(
            on_pick_fn=self._on_pick,
            add_outline=True,
            on_drop_accepted_fn=self._on_drop_accepted,
            on_drop_fn=self._on_drop,
        )

    def destroy(self):
        """Clean up resources"""
        self._drop_helper = None
        super().destroy()

    def on_drag(self, item: DetailItem) -> str:
        """Handle drag operation - create visual preview and track draggable items"""
        icon_size = 128
        with ui.VStack(width=icon_size):
            ui.Label(
                item.name,
                word_wrap=False,
                elided_text=True,
                skip_draw_when_clipped=True,
                alignment=ui.Alignment.TOP,
                style_type_name_override="GridView.Item",
            )

        # Only allow dragging of USD files
        self._dragging_url = None
        if item.url.lower().endswith((".usd", ".usda", ".usdc")):
            self._dragging_url = item.url

        return item.url

    def _on_pick(self):
        """Called when an item is picked for dragging - return URL if draggable"""
        # This replaces the deprecated 'pickable=True' parameter
        return self._dragging_url if self._dragging_url else None

    def _on_drop_accepted(self, url):
        """Only handle dragging from our asset browser for USD files"""
        return url == self._dragging_url

    def _on_drop(self, url, target, viewport_name, context_name):
        """Handle drop - let viewport handle the actual asset loading"""
        if url == self._dragging_url:
            # Reset dragging URL
            self._dragging_url = None
            # Let viewport do the asset dropping - return None to pass through
            return None

        self._dragging_url = None
        return None
