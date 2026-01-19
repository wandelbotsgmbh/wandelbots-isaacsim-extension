"""
This is the implementation of the OGN node defined in OgnWriteIO.ogn
"""

import asyncio

import carb
import omni.timeline
import usdrt.Sdf
from omni.graph.action_core import get_interface
from wandelbots.omni.io import (
    IOValue,
    IOValueType,
    get_io_stream_service,
)
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    get_motion_group_service,
)
from wandelbots.omni.ogn.OgnWriteIODatabase import OgnWriteIODatabase
from wandelbots_api_client.v2.exceptions import NotFoundException

timeline = omni.timeline.get_timeline_interface()


class OgnWriteIOState:
    def __init__(self):
        self.robot_prim = None
        self.robot_config: MotionGroupConfiguration = None
        self.io_id = None
        self.io_value_type = None

    def set_metadata(self, robot_prim: list[usdrt.Sdf.Path], io_id: str):
        if len(robot_prim) == 0:
            raise ValueError("Robot prim is not set")
        if io_id is None:
            raise ValueError("IO is None")
        self.io_id = io_id
        self.robot_prim = robot_prim[0]
        self.robot_config = get_motion_group_service().get_motion_group_configuration(
            self.robot_prim.pathString
        )
        if self.robot_config is None:
            raise ValueError(
                f"No robot configuration found for {self.robot_prim}. Make sure to create a robot for the selected prim"
            )

        if not self.robot_config.enabled:
            carb.log_verbose(
                f"Motion group for {self.robot_prim} is disabled. Skipping IO query."
            )
            return
        self.api_configuration = (
            self.robot_config.motion_stream_configuration.get_api_configuration()
        )

        self.io_value_type = None
        self.io_value_type = asyncio.get_event_loop().run_until_complete(
            get_io_stream_service().get_io_type(
                self.api_configuration,
                self.robot_config.motion_stream_configuration.cell,
                self.robot_config.motion_stream_configuration.controller,
                self.io_id,
            )
        )

    def reset(self):
        self.io_sub = None
        self.robot_prim = None
        self.io_id = None

    @property
    def enabled(self) -> bool:
        return self.robot_config is not None and self.robot_config.enabled


def get_input_value(db: OgnWriteIODatabase, value_type: IOValueType) -> IOValue:
    if value_type == IOValueType.BOOL:
        return db.inputs.value_bool
    if value_type == IOValueType.INTEGER:
        return db.inputs.value_int
    if value_type == IOValueType.FLOAT:
        return db.inputs.value_float
    raise ValueError(f"{value_type} io value type is not supported")


def set_output_value(db: OgnWriteIODatabase, value: IOValue):
    if isinstance(value, bool):
        db.outputs.value_bool = value
        return
    if isinstance(value, int):
        db.outputs.value_int = value
        return
    if isinstance(value, float):
        db.outputs.value_float = value
        return
    raise ValueError(f"{type(value)}={value} io value type is not supported")


class OgnWriteIO:
    """
    Write an IO value to a controller.
    """

    @staticmethod
    def internal_state():
        """Returns an object that will contain per-node state information"""
        return OgnWriteIOState()

    @staticmethod
    def compute(db: OgnWriteIODatabase) -> bool:
        """Compute the outputs from the current input"""
        state: OgnWriteIOState = db.per_instance_state

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

            if state.io_value_type is None:
                return

            input_value = get_input_value(db, state.io_value_type)
            if input_value is None:
                return

            asyncio.get_event_loop().run_until_complete(
                get_io_stream_service().set_io_value(
                    state.api_configuration,
                    state.robot_config.motion_stream_configuration.cell,
                    state.robot_config.motion_stream_configuration.controller,
                    state.io_id,
                    input_value,
                )
            )
            set_output_value(db, input_value)
            get_interface().set_execution_enabled("outputs:exec_out")
        except NotFoundException:
            db.log_warn(f"{state.robot_prim} {state.io_id} not found")
            return False
        except ValueError as error:
            db.log_warn(str(error))  # Most likely due to missing robot configuration
            return False
        except Exception as error:
            db.log_error(str(error))
            return False

        return True
