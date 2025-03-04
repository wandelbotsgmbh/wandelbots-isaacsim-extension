import carb
from dataclasses import dataclass
from typing import Any, Dict, ClassVar, List, Union
import pydantic
import omni.isaac.core.utils.stage as stage_utils
from wandelbots.omni.datatypes import AnalogSignal


@dataclass
class ConfigurableTool:
    class Configuration(pydantic.BaseModel):
        identifier: str
        type: str
        prim_path: str
        robot: str
        signals: Union[list[str], list[AnalogSignal]]

    _configuration: Configuration
    tools_registry: ClassVar[Dict] = {}

    def __init_subclass__(cls):
        super().__init_subclass__()
        if ConfigurableTool.Configuration is cls.Configuration:
            raise ValueError(
                "ConfigurableTool.Configuration should not be the same as cls.Configuration"
            )
        cls.tools_registry[cls.__name__] = cls

    def __init__(self, configuration: Configuration, **kwargs):
        super().__init__(**kwargs)
        self._configuration = configuration
        self._is_digital = True
        self.current_state: dict = {}

    def reinitialize(self):
        """
        Perform initializations that might be required each time the scene is restarted.
        """
        pass

    def on_io_stream_message(self, io_values: List[Dict[str, any]]):
        """
        Handle a message received via the IO stream.
        """
        tool_signals = self.filter_tool_signals(io_values)
        if (
            tool_signals != self.current_state
        ):  # cannot compare with mode because of analog signals
            if self._is_digital:
                self.send_digital_signals(tool_signals)
            else:
                self.send_analog_signals(tool_signals)
            self.current_state = tool_signals

    def filter_tool_signals(
        self, io_values: List[Dict[str, Union[bool, float]]]
    ) -> Dict[str, Union[bool, float]]:
        """
        From an IO message, filter out the relevant IOs in the correct order.
        """
        io_value_map = {val["io"]: list(val.values())[1] for val in io_values}
        try:
            filtered_signals = {
                signal_id: io_value_map[signal_id] for signal_id in self.signals
            }
        except KeyError as e:
            raise ValueError(f"A required tool signal {e.args[0]} could not be found")

        return filtered_signals

    def send_digital_signals(self, tool_signals: dict[str, Union[bool, float]]):
        """
        Sends digital signals mapping to the tool
        """
        try:
            mode = next(
                tool_state.mode
                for tool_state in self.configuration.states
                if tool_state.signals_mapping == tool_signals
            )
            carb.log_info(f"Setting {self.identifier} to {mode}")
            self.set_tool_state(mode)
        except StopIteration:
            pass

    def send_analog_signals(self, data: Dict[str, bool]):
        """
        Sends analog data signals mapping to the tool
        """
        raise NotImplementedError

    def set_tool_state(self, mode: str):
        """
        Sets tool state based on the mode given. Applicable for tools with digital states only
        """
        raise NotImplementedError

    def validate(self):
        stage_prims = [
            prim.GetPrimPath().pathString for prim in stage_utils.traverse_stage()
        ]
        if self.prim_path not in stage_prims:
            raise ValueError(
                f"Given {self.prim_path} is not a valid prim path in the stage for {self.identifier}"
            )

    @property
    def configuration(self) -> Configuration:
        return self._configuration

    @property
    def identifier(self) -> str:
        return self._configuration.identifier

    @property
    def prim_path(self) -> str:
        return self._configuration.prim_path

    @property
    def signals(self) -> list[str]:
        if self.is_digital:
            return self._configuration.signals
        else:
            return [each.id for each in self._configuration.signals]

    @property
    def is_digital(self) -> bool:
        return self._is_digital

    @property
    def modes(self) -> list[str]:
        return (
            [state.mode for state in self.configuration.states]
            if self.is_digital
            else []
        )

    @classmethod
    def from_dict(cls, config: Dict):
        return cls.Configuration.parse_obj(config)

    @property
    def to_dict(self) -> dict[str, Any]:
        return dict(self._configuration)
