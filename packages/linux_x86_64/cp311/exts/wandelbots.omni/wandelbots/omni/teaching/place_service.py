"""Place a motion group at a joint position directly, without a trajectory.

Planning and executing a trajectory is the right way to *move* a robot, but it
is a poor way to *look* at a configuration: it needs a reachable path, a live
motion stream and the simulation playing, and any of those missing leaves the
robot where it was. For inspecting an IK solution none of that is wanted - the
question is only "show me the robot standing there".

So this teleports instead. Two halves, because they answer to different
owners:

- NOVA's own state, through the virtual controller. Everything downstream
  (further IK seeds, planning, the motion stream) reads the robot's position
  from there, so it has to agree with what is on screen.
- the articulation in the scene, which follows NOVA through the motion stream.
  The stream needs a running simulation, so placing starts it, the same way a
  ghost-teaching move does. Posing the links through USD instead depends on
  how each asset nests and bakes its links, and pulls some robots apart.

Only a virtual controller can be teleported. A real robot is told, not placed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import carb
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.manipulators import MotionStreamConfiguration
from wandelbots.omni.teaching.move_to_service import ensure_simulation_streaming
from wandelbots.omni.utils.api import get_api_client_from_config

# The teleport is a state write, not a motion: the controller answers in
# milliseconds or something is wrong.
_REQUEST_TIMEOUT_S = 10.0
# How close the read-back joint state has to be to count as placed. A teleport
# lands within ~1e-8 rad; this only has to exclude a write that did not take.
_PLACED_TOLERANCE_RAD = 1e-4


@dataclass
class PlacementResult:
    """What placing the robot actually achieved."""

    ok: bool = False
    # NOVA reports the requested joint position for the motion group.
    controller_placed: bool = False
    # A live motion stream carries the new state into the scene.
    stream_live: bool = False
    max_joint_residual: float | None = None
    message: str = ""

    @property
    def summary(self) -> str:
        """Short form for a value row - the message is the sentence."""
        if not self.ok:
            return "not placed"
        if self.max_joint_residual is None:
            return "placed (not verified)"
        return f"{self.max_joint_residual:.2e} rad - placed"


async def _is_virtual_controller(api_client, cell: str, controller: str) -> bool:
    robot_controller = await asyncio.wait_for(
        wb_v2.ControllerApi(api_client).get_robot_controller(
            cell=cell, controller=controller
        ),
        timeout=_REQUEST_TIMEOUT_S,
    )
    configuration = getattr(robot_controller, "configuration", None)
    actual = getattr(configuration, "actual_instance", configuration)
    return isinstance(actual, wb_v2_models.VirtualController)


async def _set_joint_position(
    api_client, stream_config: MotionStreamConfiguration, joint_position: list[float]
) -> None:
    """Teleport the virtual robot, switching to monitor mode if it is not there.

    A controller in another mode rejects the state write, so the mode is
    checked first rather than letting the write fail.
    """
    controller_api = wb_v2.ControllerApi(api_client)
    state = await asyncio.wait_for(
        controller_api.get_current_robot_controller_state(
            cell=stream_config.cell, controller=stream_config.controller
        ),
        timeout=_REQUEST_TIMEOUT_S,
    )
    if state.mode != wb_v2_models.RobotSystemMode.MODE_MONITOR:
        carb.log_info(
            f"Place robot: controller in {state.mode}, switching to monitor first"
        )
        await asyncio.wait_for(
            controller_api.set_default_mode(
                cell=stream_config.cell,
                controller=stream_config.controller,
                mode=wb_v2_models.SettableRobotSystemMode.MODE_MONITOR,
            ),
            timeout=_REQUEST_TIMEOUT_S,
        )
    await asyncio.wait_for(
        wb_v2.VirtualControllerApi(api_client).set_motion_group_state(
            cell=stream_config.cell,
            controller=stream_config.controller,
            motion_group=stream_config.motion_group,
            motion_group_joints=wb_v2_models.MotionGroupJoints(
                positions=list(joint_position)
            ),
        ),
        timeout=_REQUEST_TIMEOUT_S,
    )


async def _read_joint_position(
    api_client, stream_config: MotionStreamConfiguration
) -> list[float] | None:
    try:
        state = await asyncio.wait_for(
            wb_v2.MotionGroupApi(api_client).get_current_motion_group_state(
                cell=stream_config.cell,
                controller=stream_config.controller,
                motion_group=stream_config.motion_group,
            ),
            timeout=_REQUEST_TIMEOUT_S,
        )
        return list(state.joint_position)
    except Exception as exc:
        # Verification failing is not the placement failing.
        carb.log_warn(f"Place robot: could not read the joint state back: {exc}")
        return None


async def place_motion_group_at_joints(
    stream_config: MotionStreamConfiguration,
    joint_position: list[float],
) -> PlacementResult:
    """Put *stream_config*'s motion group at *joint_position*, no motion planned.

    Starts the simulation if it is stopped: the scene only shows the new state
    once the motion stream carries it.
    """
    result = PlacementResult()
    if not joint_position:
        result.message = "No joint position to place the robot at."
        return result

    try:
        async with get_api_client_from_config(
            stream_config.get_api_configuration()
        ) as api_client:
            if not await _is_virtual_controller(
                api_client, stream_config.cell, stream_config.controller
            ):
                result.message = (
                    f"Controller '{stream_config.controller}' is a real robot - "
                    "it cannot be placed, only moved."
                )
                return result

            await _set_joint_position(api_client, stream_config, joint_position)
            result.controller_placed = True

            actual = await _read_joint_position(api_client, stream_config)
    except Exception as exc:
        carb.log_warn(f"Place robot failed: {exc}")
        result.message = f"Could not place the robot: {exc}"
        return result

    if actual and len(actual) == len(joint_position):
        result.max_joint_residual = max(
            abs(float(a) - float(b)) for a, b in zip(joint_position, actual)
        )
        if result.max_joint_residual > _PLACED_TOLERANCE_RAD:
            result.message = (
                "The controller accepted the position but reports "
                f"{result.max_joint_residual:.4f} rad away from it."
            )
            return result

    result.ok = True
    result.stream_live = await ensure_simulation_streaming(stream_config)
    if result.stream_live:
        result.message = "Robot placed."
    else:
        result.message = (
            "Robot placed on the controller. The robot in the scene will not "
            "follow: its motion stream did not come up."
        )
    return result
