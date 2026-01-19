"""
This is the implementation of the OGN node defined in OgnReadBusIO.ogn
"""

# Array or tuple values are accessed as numpy arrays so you probably need this import
import asyncio

import carb
import omni.timeline
import usdrt.Sdf
from omni.graph.action_core import get_interface
from wandelbots.omni.io.bus_io_stream_service import (
    get_bus_io_stream_service,
)
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    get_motion_group_service,
)
from wandelbots_api_client.v2.models.io_value import (
    IOValue,
    IOBooleanValue,
    IOIntegerValue,
    IOFloatValue,
)
from wandelbots.omni.ogn.OgnReadBusIODatabase import OgnReadBusIODatabase

timeline = omni.timeline.get_timeline_interface()


class OgnReadBusIOState:
    def __init__(self):
        self.robot_prim: usdrt.Sdf.Path = None
        self.robot_configuration: MotionGroupConfiguration = None
        self.io_id = None
        self.io_value: IOValue = None

    def on_init(self, ios: dict[str, IOValue]):
        try:
            self.io_value = ios[self.io_id]
        except KeyError:
            carb.log_warn(f"IO ID '{self.io_id}' not found in available IOs")
            self.io_value = None

    def on_change(self, io: str, new_value: IOValue):
        self.io_value = new_value

    def set_metadata(self, robot_prim: list[usdrt.Sdf.Path], io_id: str):
        if len(robot_prim) == 0:
            raise ValueError("Robot prim is not set")
        if io_id is None:
            raise ValueError("Bus IO is None")
        self.io_id = io_id
        self.robot_prim: usdrt.Sdf.Path = robot_prim[0]
        self.robot_configuration = (
            get_motion_group_service().get_motion_group_configuration(
                self.robot_prim.pathString
            )
        )
        if self.robot_configuration is None:
            raise ValueError(
                f"No robot configuration found for {self.robot_prim}. Make sure to create a robot for the selected prim"
            )
        if not self.robot_configuration.enabled:
            carb.log_verbose(
                f"Motion group for {self.robot_prim} is disabled. Skipping IO subscription."
            )
            return

        self.api_configuration = (
            self.robot_configuration.motion_stream_configuration.get_api_configuration()
        )

        self.io_sub = asyncio.get_event_loop().run_until_complete(
            get_bus_io_stream_service().subscribe(
                self.api_configuration,
                self.robot_configuration.motion_stream_configuration.cell,
                [self.io_id],
                on_change=self.on_change,
                on_init=self.on_init,
            )
        )

    @property
    def enabled(self) -> bool:
        return self.robot_configuration is not None and self.robot_configuration.enabled

    def reset(self):
        self.io_sub = None
        self.robot_prim = None
        self.io_id = None


class OgnReadBusIO:
    """
    Read a Bus IO value from a cell.
    """

    @staticmethod
    def internal_state():
        """Returns an object that will contain per-node state information"""
        return OgnReadBusIOState()

    @staticmethod
    def release(node):
        try:
            state: OgnReadBusIOState = OgnReadBusIODatabase.per_instance_internal_state(
                node
            )
            state.io_sub = None
        except Exception as ex:
            carb.log_error(f"OgnReadBusIO release failed. {ex}")

    @staticmethod
    def compute(db: OgnReadBusIODatabase) -> bool:
        """Compute the outputs from the current input"""
        state: OgnReadBusIOState = db.per_instance_state

        if not timeline.is_playing():
            state.reset()
            return

        if len(db.inputs.robot) == 0:
            db.log_error("Robot root prim not defined")
            return False

        try:
            if db.inputs.robot[0] != state.robot_prim or db.inputs.io_id != state.io_id:
                state.set_metadata(db.inputs.robot, db.inputs.io_id)

            if not get_interface().get_execution_enabled("inputs:exec_in"):
                return
            if not state.enabled:
                return

            # Fetch current value from cache on every exec
            read_value = asyncio.get_event_loop().run_until_complete(
                get_bus_io_stream_service().get_io_value(
                    state.api_configuration,
                    state.robot_configuration.motion_stream_configuration.cell,
                    state.io_id,
                )
            )

            if read_value is None:
                return

            if isinstance(read_value, IOBooleanValue):
                db.outputs.value_bool = bool(read_value.value)
            if isinstance(read_value, IOIntegerValue):
                db.outputs.value_int = int(read_value.value)
            if isinstance(read_value, IOFloatValue):
                db.outputs.value_float = float(read_value.value)
            get_interface().set_execution_enabled("outputs:exec_out")
        except Exception as error:
            db.log_error(str(error))
            return False

        return True
