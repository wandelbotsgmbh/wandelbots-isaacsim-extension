import json
import carb
from typing import Literal, Optional, final

import numpy as np
from pydantic import Field
import wandelbots_api_client.v2.models as wb_models
import wandelbots_api_client as wb
from omni.isaac.core.utils.types import ArticulationAction
from .base import StreamingConnector
from wandelbots.omni.environment import host_database
from wandelbots.omni.utils.api import get_api_client
from wandelbots.omni.utils.auth import get_auth_token


class RobotStateConnector(StreamingConnector):
    @final
    class Configuration(StreamingConnector.Configuration):
        identifier: str
        type: Literal["RobotStateConnector"] = "RobotStateConnector"
        robot: str
        robot_state: Optional[int] = 32
        use_external_joint_stream: bool = Field(
            default=False,
            description="If true the robot will use the external joint stream endpoint to synchronize its articulation state with Wandelbots NOVA.",
        )

        class Config:
            title = "Robot State Connector"

    def __init__(self, configuration=Configuration):
        super().__init__(configuration=configuration)
        self._robot_configuration = host_database[
            f"robots.{self.configuration.robot}.configuration"
        ]
        self.host = self._robot_configuration["host"]

        self.controller_id = self._robot_configuration["controller_id"]
        self.motion_group_id = self._robot_configuration["motion_group_id"]
        self.motion_group = f"{self.motion_group_id}@{self.controller_id}"
        self.cell = self._robot_configuration["cell"]
        self.default_DOFS = 6

    def is_external_joint_stream(self) -> bool:
        return self._configuration.use_external_joint_stream

    def enable_external_joint_stream(self, enable: bool):
        self._configuration.use_external_joint_stream = enable

    def _generate_websocket_uri(self):
        websocket_protocol = "wss" if self._robot_configuration["is_secured"] else "ws"
        base_url = f"{websocket_protocol}://{self.host}/api/v2/cells/{self.cell}"
        if self.is_external_joint_stream():
            return f"{base_url}/virtual-controllers/{self.controller_id}/external-joints-stream"
        return f"{base_url}/controllers/{self.controller_id}/motion-groups/{self.motion_group}/state-stream?response_rate={self.configuration.robot_state}"

    async def check_connection(self, token: str | None):
        """
        Tests if a connection can be established. Will throw an error if check failed
        """
        await self.get_motion_group_state(token)

    async def get_motion_group_state(self, token: str | None):
        api_client = get_api_client(
            self.host, self._robot_configuration["is_secured"], token
        )
        state = await wb.MotionGroupInfosApi(
            api_client=api_client
        ).get_current_motion_group_state(self.cell, self.motion_group)
        await api_client.close()
        return state

    async def open(self):
        self.websocket_uri = self._generate_websocket_uri()
        print("Connecting to websocket at:", self.websocket_uri)
        token = get_auth_token()
        await self._open_websocket_connection(uri=self.websocket_uri, token=token)

    async def close(self):
        await self._close_websocket_connection()

    async def receive(self):
        async with self.receive_lock:
            return json.loads(await self.websocket.recv())

    async def send(self, message: str):
        raise NotImplementedError

    async def start_stream(self, **kwargs):
        token = get_auth_token()
        result = await self.get_motion_group_state(token=token)

        joint_count = len(result.state.joint_position.joints)
        if joint_count != self.default_DOFS:
            carb.log_error("MotionState joint count does not match configuration DOF")
        robot = kwargs.get("robot")
        carb.log_info(
            f"Start {self.motion_group} with {joint_count} joints externalJoints={self.is_external_joint_stream()}"
        )

        if self.is_external_joint_stream():
            joint_positions = self.get_joint_positions(robot)
            # external joint stream requires the simulation to send its state first
            await self.send_joint_positions(joint_positions)
        await super().start_stream(**kwargs)

    @property
    def robot(self):
        return self.configuration.robot

    async def send_joint_positions(self, positions: list[float]):
        if positions is None:
            carb.log_warn(f"Cannot send {self.motion_group} position because its None")
            return

        zeros = list(np.zeros(self.default_DOFS))
        joint_state_request = wb_models.ExternalJointStreamDatapoint(
            id=self.motion_group_id,
            motion_group=self.motion_group,
            value=wb_models.ExternalJointStreamDatapointValue(
                positions=positions,
                velocities=zeros,
                accelerations=zeros,
                torques=zeros,
            ),
        ).to_dict()
        await self.websocket.send(json.dumps({"states": [joint_state_request]}))

    async def _parse(self, **kwargs):
        # Make sure robot with articulation root is present
        robot = kwargs.get("robot")
        if not robot:
            return

        if "error" in self.data:
            error_message = self.data["error"]["message"]
            carb.log_error(f"{self.motion_group_id} Received error {error_message}")
            return
        if not self.data:
            carb.log_warn(
                f"Received empty data from RobotState {self.motion_group} websocket stream"
            )
            return

        result = self.data["result"]

        if self.is_external_joint_stream():
            await self._update_joints_in_external_mode(robot, result)
        else:
            self._update_joints(robot, result)

    def _update_joints(self, robot, motion_response_result: dict):
        # Expected data format is {  "joint_position": { "joints": [...]} } }
        if "joint_position" not in motion_response_result:
            carb.log_error('"joint_position" not found in motion state response.')
            return
        result_joint_positions = motion_response_result["joint_position"]
        if "joints" not in result_joint_positions:
            carb.log_error('"joints" not found in motion state response.')
            return
        joint_positions = result_joint_positions["joints"]
        self._last_joints = joint_positions

        if self.timeline.is_stopped():
            return

        self.apply_joints(robot, joint_positions)

    async def _update_joints_in_external_mode(
        self,
        robot,
        motion_response_result: list[wb_models.ExternalJointStreamDatapoint],
    ):
        if len(motion_response_result) == 0:
            carb.log_warn(
                f"Received empty motion state response for {self.motion_group_id}"
            )
            return
        if len(motion_response_result) > 1:
            carb.log_warn(
                f"Received multiple motion states for {self.motion_group_id}, only using the first one"
            )

        first_datapoint = wb_models.ExternalJointStreamDatapoint.from_dict(
            motion_response_result[0]
        )
        joint_positions = first_datapoint.value.positions

        # Robot can only be updated if timeline is playing
        # Otherwise you will get something like the following error:
        # Physics Simulation View is not created yet in order to use apply_action/get_joint_positions
        if self.timeline.is_playing():
            self.apply_joints(robot, joint_positions)

            # Send feedback of articulation action
            self._last_joints = self.get_joint_positions(robot)

        await self.send_joint_positions(self._last_joints)

    def get_joint_positions(self, robot) -> list[float]:
        return [float(x) for x in list(robot.articulation.get_joint_positions())][
            : self.default_DOFS
        ]

    def apply_joints(self, robot, joint_positions: list[float]):
        """
        This function is called when the user changes one of the float fields
        to control a robot joint position target.  The index of the joint and the new
        desired value are passed in as arguments.

        This function assumes that there is a guarantee it is called safely.
        I.e. A valid Articulation has been selected and initialized
        and the timeline is playing.  These gurantees are given by careful UI
        programming.  The joint control frames are only visible to the user when
        these guarantees are met.

        Args:
            joint_positions (float): New position target for robot joints (needs to match joint count)
        """
        if not robot.articulation.is_valid():
            carb.log_error(
                f"Invalid articulation for {(self._configuration.identifier)}"
            )
            return

        if self.default_DOFS is None:
            carb.log_error(
                f'Attempted to set joints for "{self.controller_id}" but joint count is unknown'
            )
            return

        if self.default_DOFS < len(joint_positions):
            carb.log_error(
                f'Attempted to set "{self.controller_id}" joints with more joint values than known joint count'
            )
            return

        robot_action = ArticulationAction(
            joint_positions=np.array(joint_positions),
            joint_velocities=None,
            joint_indices=np.array(range(self.default_DOFS)),
        )

        robot.articulation.apply_action(robot_action)
