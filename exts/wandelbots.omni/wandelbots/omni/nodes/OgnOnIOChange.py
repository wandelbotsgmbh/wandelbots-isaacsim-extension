"""
This is the implementation of the OGN node defined in OgnOnIOChange.ogn
"""

# Array or tuple values are accessed as numpy arrays so you probably need this import
from wandelbots.omni.base import omniservice_app
from wandelbots.omni.core.networks.io_stream_service import (
    get_io_stream_service,
    IOStreamService,
    IOValue,
)
from wandelbots.omni.core.robot import ConfigurableRobot
from wandelbots.omni.utils.auth import get_auth_token
from wandelbots.omni.utils.robot import get_robot_by_prim_path
from wandelbots.omni.utils.api import ApiConfiguration
from omni.graph.action_core import get_interface
from wandelbots.omni.ogn.OgnOnIOChangeDatabase import OgnOnIOChangeDatabase
from queue import Queue
import asyncio
import carb
import pxr.Sdf
import omni.timeline


timeline = omni.timeline.get_timeline_interface()


class OgnOnIOChangeState:
    def __init__(self):
        self.io_stream_service: IOStreamService = omniservice_app.dependency_overrides[
            get_io_stream_service
        ]()
        self.robot_prim = None
        self.robot: ConfigurableRobot.Configuration = None
        self.io_id = None
        self.io_change_queue = Queue()

    def on_change(self, io: str, new_value: IOValue):
        carb.log_verbose(f"Change io={io} value={new_value}")
        self.io_change_queue.put(new_value)

    def set_metadata(self, robot_prim: list[pxr.Sdf.Path], io_id: str):
        if len(robot_prim) == 0:
            raise ValueError("Robot prim is not set")
        if io_id is None:
            raise ValueError("IO is None")
        self.robot_prim = robot_prim[0]
        self.robot = get_robot_by_prim_path(self.robot_prim)
        if self.robot is None:
            raise ValueError(
                f"No robot configuration found for {self.robot_prim}. Make sure to create a robot for the selected prim"
            )
        self.api_configuration = ApiConfiguration(
            host=self.robot.host,
            secure_connection=self.robot.is_secured,
            access_token=get_auth_token(),
        )
        self.io_id = io_id
        self.io_sub = asyncio.get_event_loop().run_until_complete(
            self.io_stream_service.subscribe(
                self.api_configuration,
                self.robot.cell,
                self.robot.controller_id,
                [self.io_id],
                on_change=self.on_change,
            )
        )


class OgnOnIOChange:
    """
    Triggers once the selected IO value changes.
    """

    @staticmethod
    def internal_state():
        """Returns an object that will contain per-node state information"""
        return OgnOnIOChangeState()

    @staticmethod
    def release(node):
        try:
            state: OgnOnIOChangeState = (
                OgnOnIOChangeDatabase.per_instance_internal_state(node)
            )
            state.io_sub = None
        except Exception as ex:
            carb.log_error(f"OgnReadIO release failed. {ex}")

    @staticmethod
    def compute(db: OgnOnIOChangeDatabase) -> bool:
        """Compute the outputs from the current input"""

        if not timeline.is_playing():
            return

        if len(db.inputs.robot) == 0:
            db.log_error("Robot root prim not defined")
            return False

        try:
            state: OgnOnIOChangeState = db.per_instance_state

            if db.inputs.robot[0] != state.robot_prim or db.inputs.io_id != state.io_id:
                state.set_metadata(db.inputs.robot, db.inputs.io_id)

            if state.io_change_queue.empty():
                return

            value = state.io_change_queue.get(block=True)
            carb.log_info(f"New io value {value}")

            if isinstance(value, bool):
                db.outputs.value_bool = value
            if isinstance(value, int):
                db.outputs.value_int = value
            if isinstance(value, float):
                db.outputs.value_float = value
            get_interface().set_execution_enabled("outputs:exec_out")
        except Exception as error:
            # If anything causes your compute to fail report the error and return False
            db.log_error(str(error))
            return False

        # Even if inputs were edge cases like empty arrays, correct outputs mean success
        return True
