from typing import Tuple
import omni.timeline
import omni.usd
from pxr import UsdGeom


class SceneUtils:
    @staticmethod
    async def check_simulation() -> Tuple[omni.timeline.Timeline, bool]:
        """
        Checks if the simulation is still played in the scene
        Returns:
            Timeline from the scene and a bool status variable which tells if the simulation is played or not
        """
        timeline = omni.timeline.get_timeline_interface()
        is_playing = timeline.is_playing()
        return timeline, is_playing

    @staticmethod
    def get_stage_units() -> float:
        stage = omni.usd.get_context().get_stage()
        stage_unit = UsdGeom.GetStageMetersPerUnit(stage)
        return stage_unit
