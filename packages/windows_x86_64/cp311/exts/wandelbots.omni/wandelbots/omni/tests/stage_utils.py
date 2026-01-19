from contextlib import contextmanager
from pxr import Usd, UsdUtils
import omni.usd

try:
    from isaacsim.core.utils.stage import use_stage as isaac_use_stage

    HAS_USE_STAGE = True
except ImportError:
    HAS_USE_STAGE = False


@contextmanager
def use_stage(stage: Usd.Stage):
    """
    Sets `stage` as the `current stage` within the context.

    Some methods do not allow to pass a stage argument and use get_current_stage() internally.

    Supports fallback for  IsaacSim <=4.5

    Yields:
        The stage that was set as current
    """
    if HAS_USE_STAGE:
        with isaac_use_stage(stage):
            yield stage
    else:
        cache = UsdUtils.StageCache.Get()
        cache.Insert(stage)

        stage_id = cache.GetId(stage).ToLongInt()
        usd_context = omni.usd.get_context()
        usd_context.attach_stage_with_callback(stage_id)
        yield stage
