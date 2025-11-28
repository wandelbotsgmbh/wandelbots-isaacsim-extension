"""
This is the implementation of the OGN node defined in OgnReadIO.ogn
"""

# Array or tuple values are accessed as numpy arrays so you probably need this import
import asyncio

import carb
import omni.timeline
import usdrt.Sdf
from omni.graph.action_core import get_interface
from wandelbots.omni.io import (
    IOValue,
    get_io_stream_service,
)
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    get_motion_group_service,
)
from wandelbots.omni.ogn.OgnReadIODatabase import OgnReadIODatabase
from wandelbots.omni.utils.api import ApiConfiguration
from wandelbots.omni.utils.auth import get_auth_token

timeline = omni.timeline.get_timeline_interface()


class OgnReadIOState:
    def __init__(self):
        self.robot_prim: usdrt.Sdf.Path = None
        self.robot_configuration: MotionGroupConfiguration = None
        self.io_id = None
        self.io_value: IOValue = None

    def on_init(self, ios: dict[str, IOValue]):
        self.io_value = ios[self.io_id]

    def on_change(self, io: str, new_value: IOValue):
        self.io_value = new_value

    def set_metadata(self, robot_prim: list[usdrt.Sdf.Path], io_id: str):
        if len(robot_prim) == 0:
            raise ValueError("Robot prim is not set")
        if io_id is None:
            raise ValueError("IO is None")
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
        motion_stream_config = self.robot_configuration.motion_stream_configuration
        self.api_configuration = ApiConfiguration(
            host=motion_stream_config.host,
            secure_connection=motion_stream_config.secure_connection,
            access_token=get_auth_token(),
        )
        self.io_id = io_id
        self.io_sub = asyncio.get_event_loop().run_until_complete(
            get_io_stream_service().subscribe(
                self.api_configuration,
                motion_stream_config.cell,
                motion_stream_config.controller,
                [self.io_id],
                on_change=self.on_change,
                on_init=self.on_init,
            )
        )

    def reset(self):
        self.io_sub = None
        self.robot_prim = None
        self.io_id = None


class OgnReadIO:
    """
    Read an IO value from a controller.
    """

    @staticmethod
    def internal_state():
        """Returns an object that will contain per-node state information"""
        return OgnReadIOState()

    @staticmethod
    def release(node):
        try:
            state: OgnReadIOState = OgnReadIODatabase.per_instance_internal_state(node)
            state.io_sub = None
        except Exception as ex:
            carb.log_error(f"OgnReadIO release failed. {ex}")

    @staticmethod
    def compute(db: OgnReadIODatabase) -> bool:
        """Compute the outputs from the current input"""
        state: OgnReadIOState = db.per_instance_state

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

            read_value = state.io_value
            if read_value is None:
                return

            if isinstance(read_value, bool):
                db.outputs.value_bool = read_value
            if isinstance(read_value, int):
                db.outputs.value_int = read_value
            if isinstance(read_value, float):
                db.outputs.value_float = read_value
            get_interface().set_execution_enabled("outputs:exec_out")
        except Exception as error:
            db.log_error(str(error))
            return False

        return True
