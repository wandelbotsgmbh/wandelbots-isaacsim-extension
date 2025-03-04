from typing import Literal, final
import numpy as np
import carb
from pydantic import Field
from wandelbots.omni.datatypes import ArticulationChainState
from omni.isaac.core.articulations import Articulation
from omni.isaac.core.utils.types import ArticulationAction
from wandelbots.omni.core.tools.base import ConfigurableTool


class ArticulationChain(ConfigurableTool):
    @final
    class Configuration(ConfigurableTool.Configuration):
        identifier: str
        type: Literal["ArticulationChain"] = "ArticulationChain"
        prim_path: str
        robot: str
        signals: list[str] = Field(..., example=["digital_out[0]", "digital_out[1]"])
        lower_limit: list[float] = [0, 0]  # position in m
        upper_limit: list[float] = [-0.006, 0.006]  # position in m
        states: list[ArticulationChainState] = Field(
            ...,
            example=[
                ArticulationChainState(
                    mode="full_open",
                    signals_mapping={"digital_out[0]": True, "digital_out[1]": False},
                    joint_positions=[0, 0],  # position in m
                    joint_velocities=[0.001, 0.001],
                ),  # velocity in m/sec (experimental)
                ArticulationChainState(
                    mode="full_close",
                    signals_mapping={"digital_out[0]": False, "digital_out[1]": True},
                    joint_positions=[-0.006, 0.006],
                    joint_velocities=[0.001, 0.001],
                ),
            ],
        )

        class Config:
            title = "Articulation Chain Configuration"

    def __init__(self, configuration=Configuration):
        super().__init__(configuration=configuration)
        self.validate()
        self._articulation = Articulation(self.configuration.prim_path)

    def reinitialize(self):
        carb.log_info(f"Reinitializing articulation for {self.configuration.identifier}")
        self._articulation.initialize()
        self.dof = int(self._articulation.num_dof or 0)

    def set_tool_state(self, mode: str):
        joint_positions, joint_velocities = next(
            (tool_state.joint_positions, tool_state.joint_velocities)
            for tool_state in self.configuration.states
            if tool_state.mode == mode
        )

        if joint_positions:
            joint_indices = np.array(range(self.dof - len(joint_positions), self.dof))
            if joint_velocities is None:
                joint_velocities = np.zeros(len(joint_positions))
            robot_action = ArticulationAction(
                joint_positions=np.array(joint_positions),
                joint_velocities=np.array(joint_velocities),
                joint_indices=joint_indices,
            )
            self._articulation.apply_action(robot_action)

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

        for state in self.configuration.states:
            if (
                not len(state.joint_positions)
                == len(state.joint_positions)
                == len(self.configuration.lower_limit)
                == len(self.configuration.upper_limit)
            ):
                raise ValueError(
                    "All joint state positions and velocities must have same length"
                )

            if not all(
                min(
                    self.configuration.lower_limit[i], self.configuration.upper_limit[i]
                )
                <= pos
                <= max(
                    self.configuration.lower_limit[i], self.configuration.upper_limit[i]
                )
                for i, pos in enumerate(state.joint_positions)
            ):
                raise ValueError(
                    f"Joint positions {state.joint_positions} in mode {state.mode} are out of limits"
                )
