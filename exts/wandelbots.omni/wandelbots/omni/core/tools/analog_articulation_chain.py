from typing import Literal, final, Dict, List, Optional
import numpy as np
import carb
from pydantic import Field
from wandelbots.omni.datatypes import AnalogSignal
from omni.isaac.core.articulations import Articulation
from omni.isaac.core.utils.types import ArticulationAction
from wandelbots.omni.core.tools.base import ConfigurableTool


class AnalogArticulationChain(ConfigurableTool):
    @final
    class Configuration(ConfigurableTool.Configuration):
        identifier: str
        type: Literal["AnalogArticulationChain"] = "AnalogArticulationChain"
        prim_path: str
        robot: str
        signals: List[AnalogSignal] = Field(
            ...,
            example=[
                AnalogSignal(
                    id="analog_out[0]", range=[0.004, 0.4]
                ),  # voltage or current(mA by default)
                AnalogSignal(
                    id="analog_out[1]", range=[0.004, 0.4]
                ),  # voltage or current(mA by default)
            ],
        )
        lower_limit: list[float] = [0, 0]  # position in m
        upper_limit: list[float] = [-0.006, 0.006]  # position in m
        joint_velocities: Optional[list[float]] = [0.001, 0.001]

        class Config:
            title = "Analog Articulation Chain Configuration"

    def __init__(self, configuration=Configuration):
        super().__init__(configuration=configuration)
        self.validate()
        self._articulation = Articulation(self.configuration.prim_path)
        self._is_digital = False

    def reinitialize(self):
        carb.log_info(f"Reinitializing articulation for {self.configuration.identifier}")
        self._articulation.initialize()
        self.dof = int(self._articulation.num_dof or 0)

    def send_analog_signals(self, data: Dict[str, float]):
        if not data:
            return

        # Check if range of each analog signal is within specified signal range or not
        signal_ranges = {
            signal.id: signal.range for signal in self.configuration.signals
        }
        if not all(
            signal_ranges[key][0] <= value <= signal_ranges[key][1]
            for key, value in data.items()
        ):
            raise ValueError(
                f"At least one of the input analog signals for {self.identifier} is out of signal range"
            )

        # Interpolate joint positions based on signal values
        tool_positions = list(
            zip(self.configuration.lower_limit, self.configuration.upper_limit)
        )
        joint_positions = []
        for idx, (key, val) in enumerate(data.items()):
            joint_position = (
                np.interp(val, signal_ranges[key], tool_positions[idx])
                .round(4)
                .tolist()
            )
            joint_positions.append(joint_position)
        carb.log_info(
            f"Setting {self.identifier} to analog mode with joint positions: {joint_positions}"
        )

        # Apply joint positions after interpolation
        if joint_positions:
            joint_indices = np.array(range(self.dof - len(joint_positions), self.dof))
            robot_action = ArticulationAction(
                joint_positions=np.array(joint_positions),
                joint_velocities=np.array(self.configuration.joint_velocities),
                joint_indices=joint_indices,
            )
            self._articulation.apply_action(robot_action)

    def validate(self):
        super().validate()
        if not all(each.range is not None for each in self.configuration.signals):
            raise ValueError(
                "Valid range is not found for atleast one of the given signals"
            )
