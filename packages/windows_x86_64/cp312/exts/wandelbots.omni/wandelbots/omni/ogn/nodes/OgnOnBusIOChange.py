"""
This is the implementation of the OGN node defined in OgnOnBusIOChange.ogn
"""

# Array or tuple values are accessed as numpy arrays so you probably need this import
import asyncio
from queue import Queue

import carb
import omni.timeline
import usdrt.Sdf
from omni.graph.action_core import get_interface
from wandelbots.omni.io.bus_io_stream_service import (
    BusIOStreamService,
    get_bus_io_stream_service,
)
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    MotionGroupService,
    get_motion_group_service,
)
from wandelbots_api_client.v2.models.io_value import (
    IOValue,
    IOBooleanValue,
    IOIntegerValue,
    IOFloatValue,
)
from wandelbots.omni.ogn.OgnOnBusIOChangeDatabase import OgnOnBusIOChangeDatabase

timeline = omni.timeline.get_timeline_interface()


class OgnOnBusIOChangeState:
    def __init__(self):
        self.bus_io_stream_manager: BusIOStreamService = None
        self.robot_service: MotionGroupService = None
        self.robot_prim: usdrt.Sdf.Path = None
        self.robot_config: MotionGroupConfiguration = None
        self.io_id = None
        self.io_change_queue = Queue()

    def on_change(self, io: str, new_value: IOValue):
        carb.log_verbose(f"Change Bus IO={io} value={new_value}")
        self.io_change_queue.put(new_value)

    def set_metadata(self, robot_prim: list[usdrt.Sdf.Path], io_id: str):
        self.bus_io_stream_manager = get_bus_io_stream_service()
        self.robot_service = get_motion_group_service()
        if len(robot_prim) == 0:
            raise ValueError("Robot prim is not set")
        if io_id is None:
            raise ValueError("Bus IO is None")
        self.io_id = io_id
        self.robot_prim = robot_prim[0]
        self.robot_config = self.robot_service.get_motion_group_configuration(
            self.robot_prim.pathString
        )
        if self.robot_config is None:
            raise ValueError(
                f"No robot configuration found for {self.robot_prim}. Make sure to create a robot for the selected prim"
            )

        if not self.robot_config.enabled:
            carb.log_verbose(
                f"Motion group for {self.robot_prim} is disabled. Skipping IO subscription."
            )
            return

        self.api_configuration = (
            self.robot_config.motion_stream_configuration.get_api_configuration()
        )

        self.io_sub = asyncio.get_event_loop().run_until_complete(
            get_bus_io_stream_service().subscribe(
                self.api_configuration,
                self.robot_config.motion_stream_configuration.cell,
                [self.io_id],
                on_change=self.on_change,
            )
        )

    def reset(self):
        self.io_sub = None
        self.robot_prim = None
        self.io_id = None

    @property
    def enabled(self) -> bool:
        return self.robot_config is not None and self.robot_config.enabled


class OgnOnBusIOChange:
    """
    Triggers once the selected Bus IO value changes.
    """

    @staticmethod
    def internal_state():
        """Returns an object that will contain per-node state information"""
        return OgnOnBusIOChangeState()

    @staticmethod
    def release(node):
        try:
            state: OgnOnBusIOChangeState = (
                OgnOnBusIOChangeDatabase.per_instance_internal_state(node)
            )
            state.io_sub = None
        except Exception as ex:
            carb.log_error(f"OgnOnBusIOChange release failed. {ex}")

    @staticmethod
    def compute(db: OgnOnBusIOChangeDatabase) -> bool:
        """Compute the outputs from the current input"""
        state: OgnOnBusIOChangeState = db.per_instance_state

        if not timeline.is_playing():
            state.reset()
            return

        if len(db.inputs.robot) == 0:
            db.log_error("Robot root prim not defined")
            return False

        try:
            if db.inputs.robot[0] != state.robot_prim or db.inputs.io_id != state.io_id:
                state.set_metadata(db.inputs.robot, db.inputs.io_id)

            if not state.enabled:
                return

            if state.io_change_queue.empty():
                return

            io_value = state.io_change_queue.get(block=True)
            carb.log_info(f"New Bus IO value {io_value}")

            if isinstance(io_value, IOBooleanValue):
                db.outputs.value_bool = bool(io_value.value)
            if isinstance(io_value, IOIntegerValue):
                db.outputs.value_int = int(io_value.value)
            if isinstance(io_value, IOFloatValue):
                db.outputs.value_float = float(io_value.value)
            get_interface().set_execution_enabled("outputs:exec_out")
        except Exception as error:
            db.log_error(str(error))
            return False

        return True
