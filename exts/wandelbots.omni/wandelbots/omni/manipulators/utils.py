try:
    import isaacsim.core.utils.stage as stage_utils
except ImportError:
    import omni.isaac.core.utils.stage as stage_utils  # type: ignore
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

    return [
        prim.GetPrimPath().pathString
        for prim in stage.Traverse()
        if prim.HasAPI(wb_schema.MotionGroupAPI)
        or (include_prims_without_api and prim.HasAPI(UsdPhysics.ArticulationRootAPI))
    ]
