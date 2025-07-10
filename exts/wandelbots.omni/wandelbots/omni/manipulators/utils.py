import omni.isaac.core.utils.stage as stage_utils
from pxr import UsdPhysics


def get_scene_articulation_roots() -> list[str]:
    """Returns all prim paths with articulation root api.
    Can be used to discover potential robots

    Returns:
        list[str]: Paths to prims with articulation root api
    """
    return [
        prim.GetPrimPath().pathString
        for prim in stage_utils.traverse_stage()
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI)
    ]
