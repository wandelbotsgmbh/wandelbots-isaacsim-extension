import omni.timeline
import omni.usd
from pxr import UsdGeom, Usd


class SceneUtils:
    @staticmethod
    def check_simulation() -> tuple[omni.timeline.Timeline, bool]:
        """
        Checks if the simulation is still played in the scene
        Returns:
            Timeline from the scene and a bool status variable which tells if the simulation is played or not
        """
        timeline = omni.timeline.get_timeline_interface()
        is_playing = timeline.is_playing()
        return timeline, is_playing

    @staticmethod
    def get_stage_units(stage: Usd.Stage = None) -> float:
        if stage is None:
            stage = omni.usd.get_context().get_stage()
        stage_unit = UsdGeom.GetStageMetersPerUnit(stage)
        return stage_unit

    @staticmethod
    def value_to_millimeters(stage_value: float) -> float:
        return stage_value * SceneUtils.get_stage_units() * 1000.0

    @staticmethod
    def millimeters_to_stage_value(mm: float) -> float:
        return (mm / 1000.0) / SceneUtils.get_stage_units()

    _UNIT_LABELS = {1.0: "m", 0.01: "cm", 0.001: "mm"}

    @staticmethod
    def get_unit_label() -> str:
        mpu = SceneUtils.get_stage_units()
        return SceneUtils._UNIT_LABELS.get(mpu, f"{mpu}m")
