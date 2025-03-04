from typing import Literal, final, Optional
import carb
import numpy as np
from pydantic import Field
from omni.isaac.manipulators.grippers import SurfaceGripper as SG
from wandelbots.omni.core.tools.base import ConfigurableTool
from wandelbots.omni.datatypes import SurfaceGripperState


class SurfaceGripper(ConfigurableTool):
    @final
    class Configuration(ConfigurableTool.Configuration):
        identifier: str
        type: Literal["SurfaceGripper"] = "SurfaceGripper"
        prim_path: str
        robot: str
        signals: list[str] = Field(..., example=["digital_out[0]", "digital_out[1]"])
        translate: Optional[float] = 0
        direction: Optional[str] = "z"
        grip_threshold: Optional[float] = 0.01
        force_limit: Optional[float] = 1000000.0
        torque_limit: Optional[float] = 1000000.0
        bend_angle: Optional[float] = np.pi / 24
        kp: Optional[float] = 100000.0
        kd: Optional[float] = 10000.0
        disable_gravity: Optional[bool] = False
        states: list[SurfaceGripperState] = Field(
            ...,
            example=[
                SurfaceGripperState(
                    mode="open",
                    signals_mapping={"digital_out[0]": True, "digital_out[1]": False},
                ),
                SurfaceGripperState(
                    mode="close",
                    signals_mapping={"digital_out[0]": False, "digital_out[1]": True},
                ),
            ],
        )

        class Config:
            title = "Surface Gripper Configuration"

    def __init__(self, configuration=Configuration):
        super().__init__(configuration=configuration)
        self.validate()
        self.gripper = SG(
            end_effector_prim_path=self.configuration.prim_path,
            translate=self.configuration.translate,
            direction=self.configuration.direction,
            grip_threshold=self.configuration.grip_threshold,
            force_limit=self.configuration.force_limit,
            torque_limit=self.configuration.torque_limit,
            bend_angle=self.configuration.bend_angle,
            kp=self.configuration.kp,
            kd=self.configuration.kd,
            disable_gravity=self.configuration.disable_gravity,
        )

    def reinitialize(self):
        carb.log_info(f"Reinitializing surface gripper {self.configuration.identifier}")
        self.gripper.initialize()

    def set_tool_state(self, mode: str):
        if mode == "open":
            self.gripper.open()
        if mode == "close":
            self.gripper.close()

    def validate(self):
        super().validate()
        state_signals = [
            key
            for state in self.configuration.states
            for key in state.signals_mapping.keys()
        ]
        if not set(state_signals) <= set(self.signals):
            raise ValueError(
                "All the signals in states signal map must be present in signals configured"
            )
