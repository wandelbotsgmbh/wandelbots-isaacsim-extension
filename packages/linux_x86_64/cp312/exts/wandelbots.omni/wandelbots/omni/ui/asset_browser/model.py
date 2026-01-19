"""
Wandelbots NOVA Asset Browser Model
"""

import os

import omni.usd
from isaacsim.core.utils.stage import open_stage
from omni.kit.browser.core import DetailItem
from omni.kit.browser.folder.core import TreeFolderBrowserModel
from pxr import Sdf, Tf, Usd

SETTING_DIRECTORY = "/exts/wandelbots.omni/asset_browser/folders"


class WandelbotsAssetBrowserModel(TreeFolderBrowserModel):
    """
    Enhanced asset browser model for Wandelbots NOVA
    """

    def __init__(self):
        # Initialize with minimal configuration to ensure gear icon works
        super().__init__(
            setting_folders=SETTING_DIRECTORY,
            filter_file_suffixes=[".usd", ".usda", ".usdc", ".usdz"],
            show_summary_folder=False,
            hide_file_without_thumbnails=False,
            show_category_subfolders=True,
        )

    def execute(self, item: DetailItem) -> None:
        """
        Action when double clicked on an item: open the original file
        """

        usd_filetypes = [".usd", ".usda", ".usdc", ".usdz"]
        if item.name.endswith(tuple(usd_filetypes)):
            stage = omni.usd.get_context().get_stage()
            if not stage:
                return
            open_stage(item.url)
        else:
            pass

    def _make_prim_path(
        self,
        stage: Usd.Stage,
        url: str,
        prim_path: Sdf.Path = None,
        prim_name: str = None,
    ):
        """Make a new/unique prim path for the given url"""
        if prim_path is None or prim_path.isEmpty:
            if stage.HasDefaultPrim():
                prim_path = stage.GetDefaultPrim().GetPath()
            else:
                prim_path = Sdf.Path.absoluteRootPath

        if prim_name is None:
            prim_name = Tf.MakeValidIdentifier(
                os.path.basename(os.path.splitext(url)[0])
            )

        return Sdf.Path(
            omni.usd.get_stage_next_free_path(
                stage, prim_path.AppendChild(prim_name).pathString, False
            )
        )
