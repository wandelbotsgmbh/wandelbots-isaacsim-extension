"""Execution service: WebSocket trajectory execution, FK, and move-to-start."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import carb
import omni.kit.app
import websockets
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.core.networks.reconnecting_websocket import _to_header_params
from wandelbots.omni.utils.api import (
    ApiConfiguration,
    get_api_client_from_config,
    get_base_headers,
)

from .helpers import _REQUEST_TIMEOUT, fetch_motion_group_context

_INIT_MAX_RETRIES = 3
_INIT_RETRY_DELAY = 0.5  # seconds, doubled on each retry
_SERVER_CANCEL_MARKER = "Cancelled on the server side"
_STATE_STREAM_RESPONSE_RATE_MS = 250


@dataclass
class ExecutionCallbacks:
    """Callbacks invoked during trajectory execution."""

    on_location: Callable[[float, float], None] | None = None
    on_progress: Callable[[float], None] | None = None
    on_joint_position: Callable[[list[float]], None] | None = None


@dataclass
class ExecutionLifecycle:
    """Pause / resume / stop events and acknowledgement callbacks."""

    pause_event: asyncio.Event | None = None
    resume_event: asyncio.Event | None = None
    stop_event: asyncio.Event | None = None
    on_paused: Callable[[], None] | None = None
    on_resumed: Callable[[], None] | None = None


@dataclass
class _CompletionState:
    """Mutable state tracked across frames while awaiting completion."""

    last_location: float | None = None
    paused: bool = False
    execution_recv_task: asyncio.Task | None = field(default=None, repr=False)
    state_recv_task: asyncio.Task | None = field(default=None, repr=False)


class ExecutionService:
    """Trajectory execution and forward kinematics operations."""

    async def forward_kinematics(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
        motion_group: str,
        joint_positions: list[list[float]],
        tcp_name: str | None = None,
    ) -> list[list[float]]:
        """Compute FK for a list of joint positions, returning TCP poses as [x,y,z,rx,ry,rz]."""
        async with get_api_client_from_config(api_configuration) as api_client:
            context = await fetch_motion_group_context(
                api_client,
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                tcp_name=tcp_name,
            )

            kinematics_api = wb_v2.KinematicsApi(api_client)
            response = await kinematics_api.forward_kinematics(
                cell=cell,
                forward_kinematics_request=wb_v2_models.ForwardKinematicsRequest(
                    motion_group_model=context.model_name,
                    joint_positions=joint_positions,
                    tcp_offset=context.tcp_offset,
                    mounting=context.description.mounting,
                ),
                _request_timeout=_REQUEST_TIMEOUT,
            )
            poses = []
            for tcp_pose in response.tcp_poses:
                position = tcp_pose.position
                orientation = (
                    tcp_pose.orientation if tcp_pose.orientation else [0, 0, 0]
                )
                poses.append(list(position) + list(orientation))
            return poses

    async def move_to_start(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
        motion_group: str,
        target_joint_position: list[float],
        tcp_name: str | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Plan and execute a PTP move to the target joint position."""
        carb.log_info(f"Move to start: target={target_joint_position}")

        async with get_api_client_from_config(api_configuration) as api_client:
            motion_group_api = wb_v2.MotionGroupApi(api_client)
            state = await motion_group_api.get_current_motion_group_state(
                cell=cell,
                controller=controller,
                motion_group=motion_group,
            )
            current_joints = list(state.joint_position)
            carb.log_info(f"Move to start: current={current_joints}")

            # Skip if already at target (within tolerance)
            max_diff = max(
                abs(a - b) for a, b in zip(current_joints, target_joint_position)
            )
            if max_diff < 0.01:  # ~0.57 degrees
                carb.log_info(
                    f"Move to start: already at target (max_diff={max_diff:.6f}), skipping."
                )
                return

            planning_api = wb_v2.TrajectoryPlanningApi(api_client)
            motion_command = wb_v2_models.MotionCommand(
                path=wb_v2_models.MotionCommandPath(
                    wb_v2_models.PathJointPTP(
                        target_joint_position=target_joint_position
                    )
                )
            )
            context = await fetch_motion_group_context(
                api_client,
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                tcp_name=tcp_name,
            )

            setup = wb_v2_models.MotionGroupSetup(
                motion_group_model=context.model_name,
                tcp_offset=context.tcp_offset,
                cycle_time=8,
                global_limits=context.description.operation_limits.auto_limits,
            )
            request = wb_v2_models.PlanTrajectoryRequest(
                start_joint_position=current_joints,
                motion_commands=[motion_command],
                motion_group_setup=setup,
            )
            response = await planning_api.plan_trajectory_without_preload_content(
                cell=cell,
                plan_trajectory_request=request,
            )
            data = await response.json()
            if response.status != 200 or "error_feedback" in data.get("response", {}):
                error = data.get("response", {}).get("error_feedback", data)
                raise RuntimeError(f"Failed to plan move-to-start: {error}")

            move_trajectory = wb_v2_models.JointTrajectory.from_dict(data["response"])
            carb.log_info(
                f"Move to start planned: {len(move_trajectory.joint_positions)} waypoints"
            )

        if stop_event and stop_event.is_set():
            raise asyncio.CancelledError("Stopped before move-to-start execution")

        await self.execute_trajectory(
            api_configuration=api_configuration,
            cell=cell,
            controller=controller,
            motion_group=motion_group,
            joint_trajectory=move_trajectory,
            tcp_name=tcp_name,
            lifecycle=ExecutionLifecycle(stop_event=stop_event),
        )
        carb.log_info("Move to start completed")

    async def execute_trajectory(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
        motion_group: str,
        joint_trajectory: wb_v2_models.JointTrajectory,
        tcp_name: str | None = None,
        callbacks: ExecutionCallbacks | None = None,
        lifecycle: ExecutionLifecycle | None = None,
        patch_start: bool = True,
    ) -> None:
        """Execute a planned trajectory on the robot via websocket.

        *callbacks* -- optional progress / location / joint-position reporters.
        *lifecycle* -- optional pause / resume / stop events and acknowledgement
          callbacks.
        *patch_start* -- if True (default), set the virtual controller's motion
          group state to the first trajectory waypoint and patch the trajectory
          start to match the actual robot position.
        """
        callbacks = callbacks or ExecutionCallbacks()
        lifecycle = lifecycle or ExecutionLifecycle()

        if patch_start:
            await self._patch_trajectory_start(
                api_configuration, cell, controller, motion_group, joint_trajectory
            )

        execution_url = (
            f"{api_configuration.base_url_websocket}/cells/{cell}"
            f"/controllers/{controller}/execution/trajectory"
        )
        state_stream_url = (
            f"{api_configuration.base_url_websocket}/cells/{cell}"
            f"/controllers/{controller}/motion-groups/{motion_group}"
            f"/state-stream?response_rate={_STATE_STREAM_RESPONSE_RATE_MS}"
        )

        initialize_message = wb_v2_models.InitializeMovementRequest(
            message_type="InitializeMovementRequest",
            trajectory=wb_v2_models.InitializeMovementRequestTrajectory(
                wb_v2_models.TrajectoryData(
                    message_type="TrajectoryData",
                    data=joint_trajectory,
                    motion_group=motion_group,
                    tcp=tcp_name,
                )
            ),
        ).to_json()

        start_message = wb_v2_models.StartMovementRequest().to_json()
        pause_message = wb_v2_models.PauseMovementRequest().to_json()

        websocket_kwargs = _to_header_params(
            get_base_headers(api_configuration.access_token)
        )

        # locations[-1] is ground truth for the location range (0 -> n).
        total_location = (
            max(joint_trajectory.locations[-1], 1) if joint_trajectory.locations else 1
        )
        carb.log_verbose(
            f"Progress tracking: total_location={total_location}, "
            f"locations=[{joint_trajectory.locations[0] if joint_trajectory.locations else '?'}"
            f"..{joint_trajectory.locations[-1] if joint_trajectory.locations else '?'}]"
        )

        carb.log_verbose(f"Connecting state-stream WS: {state_stream_url}")
        state_stream_ws = await websockets.connect(
            state_stream_url,
            open_timeout=10,
            ping_interval=30,
            ping_timeout=60,
            **websocket_kwargs,
        )
        carb.log_verbose("State-stream WS connected.")

        carb.log_verbose(f"Connecting execution WS: {execution_url}")
        try:
            execution_ws = await self._connect_and_init(
                execution_url,
                websocket_kwargs,
                initialize_message,
                lifecycle.stop_event,
            )
        except Exception:
            carb.log_verbose("Init failed — closing state-stream WS.")
            await state_stream_ws.close()
            raise
        carb.log_verbose("Execution WS connected and initialised.")

        try:
            carb.log_verbose("Sending StartMovementRequest...")
            await execution_ws.send(start_message)
            _ = await self._recv_ws_response(execution_ws, "StartMovement")
            carb.log_verbose("StartMovement acknowledged.")
            execution_start_time = time.perf_counter()

            cycle_time = 300.0
            if joint_trajectory.times and len(joint_trajectory.times) >= 2:
                cycle_time = joint_trajectory.times[-1] - joint_trajectory.times[0]
            execution_timeout = max(cycle_time * 1.5, 10.0)
            carb.log_info(
                f"Execution timeout: {execution_timeout:.1f}s "
                f"(cycle_time={cycle_time:.1f}s)"
            )

            try:
                await self._await_completion(
                    execution_ws=execution_ws,
                    state_stream_ws=state_stream_ws,
                    timeout=execution_timeout,
                    total_location=total_location,
                    callbacks=callbacks,
                    lifecycle=lifecycle,
                    pause_message=pause_message,
                    start_message=start_message,
                )
            except asyncio.CancelledError:
                duration = time.perf_counter() - execution_start_time
                carb.log_info(f"Execution stopped after {duration:.2f}s")
                raise
        finally:
            carb.log_verbose("Closing websockets...")
            for label, socket in [
                ("execution", execution_ws),
                ("state-stream", state_stream_ws),
            ]:
                try:
                    socket.close_timeout = 0.5
                    await socket.close()
                    carb.log_verbose(f"{label.capitalize()} WS closed.")
                except Exception as exc:
                    carb.log_verbose(f"{label.capitalize()} WS close error: {exc}")
            carb.log_verbose("All websockets closed.")

        duration = time.perf_counter() - execution_start_time
        carb.log_info(f"Execution completed in {duration:.2f}s")

    async def _patch_trajectory_start(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
        motion_group: str,
        joint_trajectory: wb_v2_models.JointTrajectory,
    ) -> None:
        async with get_api_client_from_config(api_configuration) as api_client:
            await wb_v2.VirtualControllerApi(api_client).set_motion_group_state(
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                motion_group_joints=wb_v2_models.MotionGroupJoints(
                    positions=joint_trajectory.joint_positions[0]
                ),
            )
            current_state = await wb_v2.MotionGroupApi(
                api_client
            ).get_current_motion_group_state(
                cell=cell,
                controller=controller,
                motion_group=motion_group,
            )
            actual_joints = list(current_state.joint_position)
            carb.log_info(
                f"Patching trajectory start: "
                f"planned={joint_trajectory.joint_positions[0]}, "
                f"actual={actual_joints}"
            )
            joint_trajectory.joint_positions[0] = actual_joints

    async def _await_completion(
        self,
        execution_ws,
        state_stream_ws,
        timeout: float,
        total_location: float,
        callbacks: ExecutionCallbacks,
        lifecycle: ExecutionLifecycle,
        pause_message: str,
        start_message: str,
    ) -> None:
        """Poll both websockets each Kit frame until the trajectory ends.

        Completion signals (checked in order of authority):
        1. State-stream: location field disappears after being present.
        2. State-stream: END_OF_TRAJECTORY kind.
        3. Execution WS: ConnectionClosed.
        4. Execution WS: server-cancel marker string.
        """
        deadline = time.perf_counter() + timeout
        completion = _CompletionState(
            execution_recv_task=asyncio.ensure_future(execution_ws.recv()),
            state_recv_task=asyncio.ensure_future(state_stream_ws.recv()),
        )
        carb.log_verbose("Awaiting trajectory completion...")

        try:
            while True:
                if time.perf_counter() >= deadline:
                    carb.log_warn(f"Execution timed out after {timeout:.1f}s")
                    return

                if lifecycle.stop_event and lifecycle.stop_event.is_set():
                    carb.log_verbose("Stop event detected.")
                    completion.execution_recv_task = (
                        await self._send_pause_and_restart_recv(
                            execution_ws,
                            completion.execution_recv_task,
                            pause_message,
                        )
                    )
                    raise asyncio.CancelledError("Execution stopped by user")

                if (
                    not completion.paused
                    and lifecycle.pause_event
                    and lifecycle.pause_event.is_set()
                ):
                    completion.paused = True
                    completion.last_location = None
                    pause_started_at = time.perf_counter()
                    completion.execution_recv_task = (
                        await self._send_pause_and_restart_recv(
                            execution_ws, completion.execution_recv_task, pause_message
                        )
                    )
                    if lifecycle.on_paused:
                        lifecycle.on_paused()
                    carb.log_info("Execution paused — waiting for resume or stop.")

                if completion.paused:
                    if lifecycle.stop_event and lifecycle.stop_event.is_set():
                        raise asyncio.CancelledError("Execution stopped while paused")
                    if lifecycle.resume_event and lifecycle.resume_event.is_set():
                        lifecycle.pause_event.clear()
                        lifecycle.resume_event.clear()
                        completion.paused = False
                        deadline += time.perf_counter() - pause_started_at
                        if not completion.execution_recv_task.done():
                            completion.execution_recv_task.cancel()
                            try:
                                await completion.execution_recv_task
                            except (asyncio.CancelledError, Exception):
                                pass
                        await execution_ws.send(start_message)
                        _ = await self._recv_ws_response(execution_ws, "ResumeMovement")
                        completion.execution_recv_task = asyncio.ensure_future(
                            execution_ws.recv()
                        )
                        if lifecycle.on_resumed:
                            lifecycle.on_resumed()
                        carb.log_info("Execution resumed.")
                    else:
                        await omni.kit.app.get_app().next_update_async()
                        continue

                if (
                    completion.execution_recv_task
                    and completion.execution_recv_task.done()
                ):
                    try:
                        raw = completion.execution_recv_task.result()
                    except websockets.exceptions.ConnectionClosed:
                        carb.log_info("Execution WS closed by server — completed.")
                        return
                    except Exception as exc:
                        carb.log_warn(f"Execution WS recv error: {exc}")
                        return

                    if isinstance(raw, str) and _SERVER_CANCEL_MARKER in raw:
                        carb.log_info("Server cancelled execution.")
                        return

                    try:
                        message = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        carb.log_verbose(f"Execution WS raw (unparseable): {raw[:500]}")
                        completion.execution_recv_task = asyncio.ensure_future(
                            execution_ws.recv()
                        )
                        await omni.kit.app.get_app().next_update_async()
                        continue

                    result = message.get("result", {})
                    if isinstance(result, dict):
                        carb.log_verbose(
                            f"Execution WS message: "
                            f"kind={result.get('kind', '')!r} "
                            f"keys={list(result.keys())}"
                        )
                    else:
                        carb.log_verbose(
                            f"Execution WS result: {type(result)} = {result}"
                        )
                    completion.execution_recv_task = asyncio.ensure_future(
                        execution_ws.recv()
                    )

                if completion.state_recv_task and completion.state_recv_task.done():
                    try:
                        raw = completion.state_recv_task.result()
                        end_of_trajectory, location = _parse_state_message(
                            raw, total_location, callbacks
                        )
                        if end_of_trajectory:
                            carb.log_info("Trajectory ended (END_OF_TRAJECTORY).")
                            return
                        if location is not None:
                            completion.last_location = location
                        elif (
                            completion.last_location is not None
                            and completion.last_location / total_location >= 0.95
                        ):
                            if callbacks.on_progress:
                                callbacks.on_progress(1.0)
                            if callbacks.on_location:
                                callbacks.on_location(total_location, total_location)
                            carb.log_info(
                                "Trajectory completed (location disappeared near end)."
                            )
                            return
                        completion.state_recv_task = asyncio.ensure_future(
                            state_stream_ws.recv()
                        )
                    except Exception:
                        carb.log_verbose(
                            "State-stream recv failed, disabling state tracking."
                        )
                        completion.state_recv_task = None

                await omni.kit.app.get_app().next_update_async()

        finally:
            carb.log_verbose("Cleaning up recv tasks...")
            for task in [completion.execution_recv_task, completion.state_recv_task]:
                if task is None:
                    continue
                if not task.done():
                    task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            carb.log_verbose("Recv tasks cleaned up.")

    async def _send_pause_and_restart_recv(
        self,
        execution_ws,
        execution_recv_task: asyncio.Task,
        pause_message: str,
    ) -> asyncio.Task:
        """Send pause, cancel the current recv task, return a fresh one.

        The pause ack will be consumed by the caller's next recv cycle.
        We must send *before* cancelling so the message goes out while
        the connection is still healthy.
        """
        try:
            await execution_ws.send(pause_message)
            carb.log_info("Sent PauseMovementRequest.")
        except Exception as exc:
            carb.log_warn(f"Failed to send PauseMovementRequest: {exc}")
        if not execution_recv_task.done():
            execution_recv_task.cancel()
            try:
                await execution_recv_task
            except (asyncio.CancelledError, Exception):
                pass
        return asyncio.ensure_future(execution_ws.recv())

    async def _connect_and_init(
        self,
        websocket_url: str,
        websocket_kwargs: dict,
        initialize_message: str,
        stop_event: asyncio.Event | None,
    ):
        """Connect, send InitializeMovementRequest, retry on control-claim failures."""
        delay = _INIT_RETRY_DELAY
        last_error: Exception | None = None
        for attempt in range(_INIT_MAX_RETRIES):
            if stop_event and stop_event.is_set():
                raise asyncio.CancelledError("Stopped before movement started")
            websocket = await websockets.connect(
                websocket_url, open_timeout=10, **websocket_kwargs
            )
            try:
                await websocket.send(initialize_message)
                await self._recv_ws_response(websocket, "InitializeMovement")
                return websocket
            except RuntimeError as exc:
                await websocket.close()
                last_error = exc
                if "Failed to claim control" not in str(exc):
                    raise
                if attempt >= _INIT_MAX_RETRIES - 1:
                    raise
                carb.log_info(
                    f"InitializeMovement attempt {attempt + 1}/{_INIT_MAX_RETRIES} "
                    f"failed (control not released yet), retrying in {delay:.0f}s..."
                )
                await asyncio.sleep(delay)
                delay *= 2
        raise last_error  # pragma: no cover

    @staticmethod
    def _check_ws_response(response: dict, step_name: str) -> None:
        if "error" in response:
            error = response["error"]
            error_message = (
                error.get("message", error) if isinstance(error, dict) else str(error)
            )
            raise RuntimeError(f"{step_name} failed: {error_message}")
        if "result" in response:
            result = response["result"]
            if isinstance(result, dict):
                trajectory_error = result.get("add_trajectory_error")
                if trajectory_error:
                    error_message = trajectory_error.get(
                        "message", str(trajectory_error)
                    )
                    raise RuntimeError(f"{step_name} failed: {error_message}")
                if result.get("message"):
                    raise RuntimeError(f"{step_name} failed: {result['message']}")

    @staticmethod
    async def _recv_ws_response(websocket, step_name: str) -> dict:
        """Read websocket messages until we get a response with 'result' or 'error'."""
        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout=30.0)
            response = json.loads(raw)
            carb.log_info(f"Execute {step_name} ws message: {response}")
            if "result" in response or "error" in response:
                ExecutionService._check_ws_response(response, step_name)
                return response


def _parse_state_message(
    raw: str,
    total_location: float,
    callbacks: ExecutionCallbacks,
) -> tuple[bool, float | None]:
    """Parse a state-stream message and invoke callbacks.

    Returns ``(end_of_trajectory, location)``.
    *end_of_trajectory* is ``True`` only for an explicit ``END_OF_TRAJECTORY``
    signal.  *location* is the current trajectory location or ``None`` when the
    message carries no location data (e.g. during pause/resume transitions).
    The caller is responsible for deciding whether a ``None`` location implies
    trajectory completion (the "location disappeared near end" heuristic).
    """
    try:
        parsed = json.loads(raw)
        state = wb_v2_models.MotionGroupState.from_dict(parsed["result"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return False, None

    end_of_trajectory = False
    location: float | None = None

    if state.execute and state.execute.details:
        trajectory_details = state.execute.details.actual_instance
        if hasattr(trajectory_details, "location"):
            location = trajectory_details.location
        if hasattr(trajectory_details, "state") and trajectory_details.state:
            traj_state = trajectory_details.state.actual_instance
            if hasattr(traj_state, "kind") and traj_state.kind == "END_OF_TRAJECTORY":
                end_of_trajectory = True

    if callbacks.on_joint_position and state.joint_position:
        try:
            callbacks.on_joint_position(list(state.joint_position))
        except Exception as exc:
            carb.log_warn(f"on_joint_position raised: {exc}")

    if location is not None and location > 0:
        progress = min(location / total_location, 1.0)
        carb.log_verbose(
            f"State-stream location={location:.3f}/{total_location} "
            f"progress={progress:.1%}"
        )
        if callbacks.on_progress:
            callbacks.on_progress(progress)
        if callbacks.on_location:
            try:
                callbacks.on_location(location, total_location)
            except Exception as exc:
                carb.log_warn(f"on_location raised: {exc}")

    return end_of_trajectory, location
