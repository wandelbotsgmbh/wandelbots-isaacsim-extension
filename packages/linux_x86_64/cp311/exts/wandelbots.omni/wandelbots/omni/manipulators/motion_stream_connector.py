import asyncio
import json

import carb
import omni.timeline
import omni.usd
import torch
from pxr import UsdGeom
import wandelbots_api_client.v2 as wb
import wandelbots_api_client.v2.models as wb_models
from wandelbots_api_client.v2.models import JointTypeEnum
from isaacsim.core.utils.types import ArticulationAction

from wandelbots.omni.core.networks import ReconnectingWebsocket
from wandelbots.omni.manipulators.utils import get_articulation_joint_indices
from wandelbots.omni.utils.api import ApiConfiguration, get_api_client_from_config

from .motion_group import MotionGroup, MotionStreamConfiguration


class MotionStreamConnector:
    def __init__(self, motion_group: MotionGroup):
        self.motion_group = motion_group
        self.receive_lock = asyncio.Lock()

        # This configuration is updated with every connect/state call which needs a token
        self.api_configuration: ApiConfiguration = (
            self.configuration.get_api_configuration()
        )
        self.stream: ReconnectingWebsocket | None = None

        self.stream_joint_count: int = None
        self.joint_indices: list[int] | None = None
        self.timeline = omni.timeline.get_timeline_interface()

    @property
    def configuration(self) -> MotionStreamConfiguration:
        return self.motion_group.configuration.motion_stream_configuration

    @property
    def is_external_joint_stream(self) -> bool:
        return self.configuration.use_external_joint_stream

    @property
    def _websocket_uri(self):
        base_url = self.api_configuration.base_url_websocket
        if self.is_external_joint_stream:
            return f"{base_url}/cells/{self.configuration.cell}/virtual-controllers/{self.configuration.controller}/external-joints-stream"
        return f"{base_url}/cells/{self.configuration.cell}/controllers/{self.configuration.controller}/motion-groups/{self.configuration.motion_group}/state-stream?response_rate={self.configuration.response_rate}"

    async def check_connection(self):
        """
        Tests if a connection can be established. Will throw an error if check failed
        """
        self.api_configuration = self.configuration.get_api_configuration()
        await self.get_motion_group_state()

    async def get_motion_group_state(self) -> wb_models.MotionGroupState:
        async with get_api_client_from_config(self.api_configuration) as api_client:
            state = await wb.MotionGroupApi(
                api_client=api_client
            ).get_current_motion_group_state(
                self.configuration.cell,
                self.configuration.controller,
                self.configuration.motion_group,
            )
            return state

    async def close(self):
        if self.stream:
            await self.stream.close()

    async def _receive_data(self, data: str):
        async with self.receive_lock:
            return await self._parse(json.loads(data))

    async def open(self):
        self.api_configuration = self.configuration.get_api_configuration()

        if self.stream and self.stream.streaming:
            carb.log_warn(
                f"Websocket for MotionStreamConnector {self.configuration.motion_group} {self.api_configuration} is already open"
            )
            return
        self.stream = ReconnectingWebsocket(
            self._websocket_uri,
            on_receive=self._receive_data,
            token=self.api_configuration.access_token,
        )

        state = await self.get_motion_group_state()
        self.stream_joint_count = len(state.joint_position)

        self.joint_indices = get_articulation_joint_indices(self.motion_group)
        await self.motion_group.get_dh_parameters()

        carb.log_info(
            f"Start {self.configuration.motion_group} jointCount={self.stream_joint_count} externalJoints={self.is_external_joint_stream}"
        )

        await self.stream.open()
        if not self.is_external_joint_stream:
            return

        # If we are in external joint stream mode, we need to ensure the controller is in control mode
        # otherwise the backend will not send joint states
        await self._ensure_control_mode()

        joint_positions = self.get_joint_positions()
        # external joint stream requires the simulation to send its state first
        await self.send_joint_positions(joint_positions)

    async def _ensure_control_mode(self) -> None:
        async with get_api_client_from_config(self.api_configuration) as api_client:
            controller_api = wb.ControllerApi(api_client=api_client)

            controller_state = await controller_api.get_current_robot_controller_state(
                self.configuration.cell, self.configuration.controller
            )

            controller_mode = controller_state.mode
            if controller_mode == wb_models.RobotSystemMode.MODE_MONITOR:
                carb.log_info(
                    f"MotionGroup {self.configuration.motion_group} is in monitor mode, switching to control mode"
                )
                await controller_api.set_default_mode(
                    self.configuration.cell,
                    self.configuration.controller,
                    wb_models.SettableRobotSystemMode.ROBOT_SYSTEM_MODE_CONTROL,
                )
            elif controller_mode != wb_models.RobotSystemMode.MODE_CONTROL:
                carb.log_warn(
                    f"MotionGroup {self.configuration.motion_group} is in unexpected mode: {controller_mode}, expected control mode"
                )

    async def send_joint_positions(self, positions: list[float]):
        if positions is None:
            carb.log_warn(
                f"Cannot send {self.configuration.motion_group} position because its None"
            )
            return

        zeros = list(torch.zeros(self.stream_joint_count))
        joint_state_request = wb_models.ExternalJointStreamDatapoint(
            motion_group=self.configuration.motion_group,
            value=wb_models.MotionGroupJoints(
                positions=positions,
                velocities=zeros,
                accelerations=zeros,
                torques=zeros,
            ),
        ).to_dict()
        await self.stream.send(json.dumps({"states": [joint_state_request]}))

    async def _parse(self, data: dict):
        if "error" in data:
            error_message = data["error"]["message"]
            carb.log_error(
                f"{self.configuration.motion_group_id} Received error {error_message}"
            )
            return
        if not data:
            carb.log_warn(
                f"Received empty data from RobotState {self.configuration.motion_group} websocket stream"
            )
            return

        result = data["result"]

        if self.is_external_joint_stream:
            await self._update_joints_in_external_mode(
                [
                    wb_models.ExternalJointStreamDatapoint.from_dict(datapoint)
                    for datapoint in result
                ]
            )
        else:
            self._update_joints(
                motion_response_result=wb_models.MotionGroupState.from_dict(result)
            )

    def _update_joints(self, motion_response_result: wb_models.MotionGroupState):
        self._last_joints = motion_response_result.joint_position

        if self.timeline.is_stopped():
            return

        self.apply_joints(motion_response_result.joint_position)

    async def _update_joints_in_external_mode(
        self, motion_response_result: list[wb_models.ExternalJointStreamDatapoint]
    ):
        if len(motion_response_result) == 0:
            carb.log_warn(
                f"Received empty motion state response for {self.configuration.motion_group_id}"
            )
            return

        if len(motion_response_result) > 1:
            carb.log_warn(
                f"Received multiple motion states for {self.configuration.motion_group_id}, only using the first one"
            )

        # Robot can only be updated if timeline is playing
        # Otherwise you will get something like the following error:
        # Physics Simulation View is not created yet in order to use apply_action/get_joint_positions
        if self.timeline.is_playing():
            self.apply_joints(motion_response_result[0].value.positions)

            # Send feedback of articulation action
            self._last_joints = self.get_joint_positions()

        await self.send_joint_positions(self._last_joints)

    def get_joint_positions(self) -> list[float]:
        all_positions = self.motion_group.articulation.get_joint_positions()
        if self.joint_indices is None:
            return [float(x) for x in list(all_positions)][: self.stream_joint_count]
        return [float(all_positions[i]) for i in self.joint_indices]

    def apply_joints(self, joint_positions: list[float]):
        """
        This function is called when the user changes one of the float fields
        to control a motion_group joint position target. The index of the joint and the new
        desired value are passed in as arguments.

        This function assumes that there is a guarantee it is called safely.
        I.e. A valid Articulation has been selected and initialized
        and the timeline is playing.  These gurantees are given by careful UI
        programming.  The joint control frames are only visible to the user when
        these guarantees are met.

        Args:
            joint_positions (float): New position target for motion_group joints (needs to match joint count)
        """
        if (
            not self.motion_group.articulation
            or not self.motion_group.articulation.is_valid()
        ):
            carb.log_error(f"Invalid articulation for {(self.motion_group.identifier)}")
            return

        if self.stream_joint_count is None:
            carb.log_error(
                f'Attempted to set joints for "{self.configuration.controller}" but joint count is unknown'
            )
            return

        if self.stream_joint_count < len(joint_positions):
            carb.log_verbose(
                f'Attempted to set "{self.configuration.controller}" joints with more joint values than known joint count {self.stream_joint_count}'
            )
            return

        # scale joint values from mm to stage units for isaac sim if there are any prismatic joints in the motion group (not necessary for revolute joints, since they are represented in radians which is the same in the API and Isaac Sim)
        dh_parameters = self.motion_group.motion_group_dh_parameters
        if any(
            dh_param.type == JointTypeEnum.PRISMATIC_JOINT for dh_param in dh_parameters
        ):
            stage = omni.usd.get_context().get_stage()
            meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
            mm_to_stage_units = 0.001 / meters_per_unit
            for i, dh_param in enumerate(dh_parameters):
                if dh_param.type == JointTypeEnum.PRISMATIC_JOINT:
                    joint_positions[i] *= mm_to_stage_units
        joint_positions_array = torch.tensor(joint_positions, dtype=torch.float32)

        # Use computed joint_indices for merged articulation, or sequential if not computed
        if self.joint_indices is not None:
            joint_indices_array = torch.tensor(self.joint_indices, dtype=torch.long)
        else:
            joint_indices_array = torch.tensor(
                range(len(joint_positions)), dtype=torch.long
            )

        motion_group_action = ArticulationAction(
            joint_positions=joint_positions_array,
            joint_indices=joint_indices_array,
        )

        self.motion_group.articulation.apply_action(motion_group_action)
