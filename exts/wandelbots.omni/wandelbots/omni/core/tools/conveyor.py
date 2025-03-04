from typing import Literal, Optional, final
import omni.isaac.core.utils.stage as stage_utils
from pxr import Gf
import omni.graph as og
from pydantic import Field
from wandelbots.omni.core.tools.base import ConfigurableTool
from wandelbots.omni.datatypes import ConveyorState


class Conveyor(ConfigurableTool):
    @final
    class Configuration(ConfigurableTool.Configuration):
        identifier: str
        type: Literal["Conveyor"] = "Conveyor"
        prim_path: str
        robot: str
        signals: list[str] = Field(..., example=["digital_out[0]"])
        curved: Optional[bool] = False
        states: list[ConveyorState] = Field(
            ...,
            example=[
                ConveyorState(
                    mode="stop",
                    signals_mapping={"digital_out[0]": False},
                    velocity=0,
                    direction=[1, 0, 0],
                ),
                ConveyorState(
                    mode="right",
                    signals_mapping={"digital_out[0]": True},
                    velocity=0.3,
                    direction=[1, 0, 0],
                ),
                ConveyorState(
                    mode="left",
                    signals_mapping={"digital_out[0]": True},
                    velocity=0.3,
                    direction=[-1, 0, 0],
                ),
            ],
        )

        class Config:
            title = "Conveyor Configuration"

    def __init__(self, configuration=Configuration):
        super().__init__(configuration=configuration)
        self.validate()
        self.conveyor_prims = [
            x.GetPrimPath().pathString
            for x in stage_utils.traverse_stage()
            if (
                x.GetTypeName() == "OmniGraphNode"
                and "ConveyorNode" in x.GetPrimPath().pathString
                and self.prim_path in x.GetPrimPath().pathString
            )
        ]

    def set_tool_state(self, mode: str):
        velocity, direction = next(
            (tool_state.velocity, tool_state.direction)
            for tool_state in self.configuration.states
            if tool_state.mode == mode
        )

        for each_prim in self.conveyor_prims:
            ogn = og.core.get_node_by_path(each_prim)
            ogn.get_attribute("inputs:direction").set(Gf.Vec3f(direction))
            ogn.get_attribute("inputs:velocity").set(velocity)
            ogn.request_compute()

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
