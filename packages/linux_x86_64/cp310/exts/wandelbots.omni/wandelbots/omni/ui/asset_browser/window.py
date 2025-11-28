"""
Wandelbots Asset Browser Window - Clean and Simple
"""

import omni.ui as ui
from omni.kit.browser.folder.core import TreeFolderBrowserWidget
from .model import WandelbotsAssetBrowserModel
from .delegate import WandelbotsAssetDelegate


class WandelbotsAssetBrowserWindow(ui.Window):
    """Clean asset browser window using native TreeFolderBrowserWidget functionality"""

    def __init__(self):
        super().__init__("Wandelbots NOVA Assets", visible=True)
        self.frame.set_build_fn(self._build_ui)
        self.deferred_dock_in("Content")

    def _build_ui(self):
        """Build the browser UI - uses native gear icon for folder selection"""
        with self.frame:
            # Asset browser widget with native settings functionality
            model = WandelbotsAssetBrowserModel()
            delegate = WandelbotsAssetDelegate(model)
            TreeFolderBrowserWidget(model, detail_delegate=delegate)
