import isaacsim.core.utils.stage as stage_utils
import wandelbots.usd as wb_schema
from pxr import UsdPhysics, Usd


def get_scene_motion_group_prim_paths(include_prims_without_api=True) -> list[str]:
    """Returns all prim paths with articulation root or motion group api.
    Can be used to discover potential robots

    Returns:
        list[str]: Paths to prims with articulation root or motion group api
    """
    stage: Usd.Stage = stage_utils.get_current_stage()
    if stage is None:
        return []

    def _filter(prim: Usd.Prim) -> bool:
        if prim.HasAPI(wb_schema.MotionGroupAPI):
            return True
        if prim.HasAPI(wb_schema.ToolAPI):
            return False
        if include_prims_without_api and prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            return True
        return False

    return [prim.GetPrimPath().pathString for prim in stage.Traverse() if _filter(prim)]
