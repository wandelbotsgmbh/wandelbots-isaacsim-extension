"""Async trajectory execution orchestration with pause / resume / stop."""

from __future__ import annotations

import asyncio
import enum
import time
from typing import TYPE_CHECKING, Callable

import carb
import omni.kit.notification_manager as nm
import omni.timeline
from omni.kit.async_engine import run_coroutine

import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.ui.tool.trajectory_planner.service import (
    get_trajectory_planner_service,
)
from wandelbots.omni.ui.tool.trajectory_planner.service.execution_service import (
    ExecutionCallbacks,
    ExecutionLifecycle,
)
from wandelbots.omni.utils.api import ApiConfiguration, get_api_client_from_config

if TYPE_CHECKING:
    from wandelbots.omni.ui.tool.trajectory_planner.events import (
        TrajectoryPlannerEvents,
    )

_CLEANUP_TIMEOUT = 8.0  # seconds to wait for previous execution to release resources
# The timeout must exceed: pause-ack (~100 ms) + 2 × sock.close timeout (3 s each).


class ExecutionState(enum.Enum):
    """Explicit lifecycle states for the execution orchestrator."""

    IDLE = "idle"
    EXECUTING = "executing"
    PAUSED = "paused"
    TEARING_DOWN = "tearing_down"


class ExecutionOrchestrator:
    """Manages trajectory execution lifecycle: execute, pause, resume, stop."""

    def __init__(
        self,
        get_api_config: Callable[[], ApiConfiguration | None],
        get_stream_params: Callable[[], tuple[str, str, str] | None],
        get_selected_tcp: Callable[[], str | None],
        get_move_to_start: Callable[[], bool],
        events: "TrajectoryPlannerEvents",
    ) -> None:
        self._get_api_config = get_api_config
        self._get_stream_params = get_stream_params
        self._get_selected_tcp = get_selected_tcp
        self._get_move_to_start = get_move_to_start
        self._events = events

        self._state: ExecutionState = ExecutionState.IDLE
        self._execute_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._teardown_task: asyncio.Task | None = None
        self._pause_event: asyncio.Event | None = None
        self._resume_event: asyncio.Event | None = None
        self._stop_event: asyncio.Event | None = None

    @property
    def state(self) -> ExecutionState:
        return self._state

    @property
    def is_executing(self) -> bool:
        return self._state in (ExecutionState.EXECUTING, ExecutionState.PAUSED)

    @property
    def is_paused(self) -> bool:
        return self._state == ExecutionState.PAUSED

    @property
    def is_idle(self) -> bool:
        return self._state == ExecutionState.IDLE

    def destroy(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._pause_event:
            self._pause_event.set()
        if self._execute_task is not None:
            self._execute_task.cancel()
            self._execute_task = None
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        if self._teardown_task is not None:
            self._teardown_task.cancel()
            self._teardown_task = None
        self._pause_event = None
        self._resume_event = None
        self._stop_event = None
        self._state = ExecutionState.IDLE

    def execute(
        self, joint_trajectory: wb_v2_models.JointTrajectory, num_commands: int
    ) -> None:
        if self._state not in (ExecutionState.IDLE, ExecutionState.TEARING_DOWN):
            carb.log_info(f"execute() ignored: state={self._state.value}")
            return
        carb.log_info(
            f"execute() called: {len(joint_trajectory.joint_positions)} waypoints, "
            f"{num_commands} motion commands, state={self._state.value}"
        )
        # Cancel the background teardown-to-IDLE transition; _do_execute takes
        # over cleanup responsibility.
        if self._teardown_task is not None:
            self._teardown_task.cancel()
            self._teardown_task = None
        self._state = ExecutionState.EXECUTING
        self._execute_task = run_coroutine(
            self._do_execute(joint_trajectory, num_commands)
        )

    def pause(self) -> None:
        if self._state != ExecutionState.EXECUTING:
            return
        carb.log_info("pause() requested.")
        if self._pause_event:
            self._pause_event.set()

    def resume(self) -> None:
        if self._state != ExecutionState.PAUSED:
            return
        carb.log_info("resume() requested.")
        if self._resume_event:
            self._resume_event.set()

    def stop(self) -> None:
        """Fully stop execution and return to idle.

        Detaches the running task so the orchestrator is immediately ready for
        a new execute() call.  The orphaned task cleans up in the background.
        """
        if self.is_idle:
            return
        carb.log_info(f"stop() called: state={self._state.value}")
        if self._stop_event:
            self._stop_event.set()
        if self._pause_event:
            self._pause_event.set()
        self._cleanup_task = self._execute_task
        # Do NOT cancel the task here.  Setting stop_event is enough: on the
        # next Kit frame _await_completion will detect it, send a
        # PauseMovement command to the robot, then raise CancelledError so the
        # execute_trajectory finally-block can close the WebSocket cleanly and
        # release ROBOT_MODE_CONTROL.  Cancelling the task at the same time
        # would cause a double-cancel that interrupts sock.close() before the
        # WS handshake completes, leaking the connection.
        # _await_cleanup will force-cancel if the task doesn't finish in time.
        # Detach so the orchestrator is immediately ready for a new execute()
        self._pause_event = None
        self._resume_event = None
        self._stop_event = None
        self._execute_task = None
        self._state = ExecutionState.TEARING_DOWN
        self._teardown_task = run_coroutine(self._finish_teardown())
        self._events.execution_cancelled.emit()

    async def _do_execute(
        self, joint_trajectory: wb_v2_models.JointTrajectory, num_commands: int
    ) -> None:
        await self._await_cleanup()

        # stop() may have detached us - cancelled event already emitted
        if self._execute_task is not asyncio.current_task():
            carb.log_info("Execution detached during cleanup wait, skipping.")
            return

        api_config = self._get_api_config()
        params = self._get_stream_params()
        if not api_config or not params:
            carb.log_warn("Cannot execute: missing API config or motion group.")
            self._state = ExecutionState.IDLE
            self._events.execution_failed.emit(
                "Missing API configuration or motion group."
            )
            return

        cell, controller, motion_group = params
        self._pause_event = asyncio.Event()
        self._resume_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        # Used to detect if stop() detached us while we were running
        my_stop_event = self._stop_event

        timeline = omni.timeline.get_timeline_interface()
        if not timeline.is_playing():
            timeline.play()

        self._events.execution_started.emit()

        try:
            service = get_trajectory_planner_service()

            start_joints = joint_trajectory.joint_positions[0]
            carb.log_info(
                f"Execution starting: cell={cell}, controller={controller}, "
                f"motion_group={motion_group}, waypoints={len(joint_trajectory.joint_positions)}"
            )
            carb.log_verbose(
                f"Start joints: [{', '.join(f'{v:.4f}' for v in start_joints)}]"
            )

            if self._get_move_to_start():
                self._events.execution_progress.emit(
                    0.05, "Moving to start position..."
                )
                carb.log_info("Moving robot to start position before execution.")
                await service.move_to_start(
                    api_configuration=api_config,
                    cell=cell,
                    controller=controller,
                    motion_group=motion_group,
                    target_joint_position=start_joints,
                    tcp_name=self._get_selected_tcp(),
                    stop_event=self._stop_event,
                )
            else:
                self._events.execution_progress.emit(0.05, "Setting start position...")
                async with get_api_client_from_config(api_config) as api_client:
                    controller_api = wb_v2.ControllerApi(api_client)
                    state = await controller_api.get_current_robot_controller_state(
                        cell=cell,
                        controller=controller,
                    )
                    if state.mode != wb_v2_models.RobotSystemMode.MODE_MONITOR:
                        carb.log_info(
                            f"Controller in {state.mode}, switching to monitor before teleport"
                        )
                        await controller_api.set_default_mode(
                            cell=cell,
                            controller=controller,
                            mode=wb_v2_models.SettableRobotSystemMode.MODE_MONITOR,
                        )
                    await wb_v2.VirtualControllerApi(api_client).set_motion_group_state(
                        cell=cell,
                        controller=controller,
                        motion_group=motion_group,
                        motion_group_joints=wb_v2_models.MotionGroupJoints(
                            positions=start_joints
                        ),
                    )

            self._events.execution_progress.emit(0.1, "Executing trajectory...")
            planned_duration = 0.0
            if joint_trajectory.times and len(joint_trajectory.times) >= 2:
                planned_duration = (
                    joint_trajectory.times[-1] - joint_trajectory.times[0]
                )
            execution_start = time.perf_counter()
            await service.execute_trajectory(
                api_configuration=api_config,
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                joint_trajectory=joint_trajectory,
                tcp_name=self._get_selected_tcp(),
                callbacks=ExecutionCallbacks(
                    on_location=self._handle_location,
                ),
                lifecycle=ExecutionLifecycle(
                    pause_event=self._pause_event,
                    resume_event=self._resume_event,
                    stop_event=self._stop_event,
                    on_paused=self._handle_paused,
                    on_resumed=self._handle_resumed,
                ),
            )
            actual_duration = time.perf_counter() - execution_start

            if self._stop_event is not my_stop_event:
                return  # Detached by stop(); skip callbacks.
            self._events.execution_progress.emit(1.0, "")
            carb.log_info(
                f"Execution complete: planned={planned_duration:.1f}s, actual={actual_duration:.1f}s"
            )
            nm.post_notification(
                f"Trajectory execution complete. "
                f"Planned: {planned_duration:.1f}s | Actual: {actual_duration:.1f}s",
                duration=4.0,
                status=nm.NotificationStatus.INFO,
            )
            self._events.execution_complete.emit()
        except asyncio.CancelledError:
            carb.log_info("Trajectory execution cancelled by user.")
            if self._stop_event is my_stop_event:
                self._events.execution_cancelled.emit()
        except Exception as exc:
            carb.log_warn(f"Execute trajectory failed: {exc}")
            if self._stop_event is not my_stop_event:
                return  # Detached by stop(); skip callbacks.
            nm.post_notification(
                "Execution failed. See log for details.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            self._events.execution_failed.emit(str(exc))
        finally:
            if self._stop_event is my_stop_event:
                self._pause_event = None
                self._resume_event = None
                self._stop_event = None
                self._execute_task = None
                self._state = ExecutionState.IDLE

    async def _finish_teardown(self) -> None:
        """Background task: wait for cleanup to finish, then transition to IDLE."""
        await self._await_cleanup()
        if self._state == ExecutionState.TEARING_DOWN:
            self._state = ExecutionState.IDLE

    async def _await_cleanup(self) -> None:
        """Wait for a previous orphaned execution task to release its resources."""
        if self._cleanup_task is None:
            return
        carb.log_info("Waiting for previous execution to release ROBOT_MODE_CONTROL...")
        try:
            # Give the task time to detect the stop_event, send PauseMovement,
            # and close the WebSocket cleanly.  Only force-cancel if it hangs.
            await asyncio.wait_for(self._cleanup_task, timeout=_CLEANUP_TIMEOUT)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            carb.log_warn(
                f"Execution cleanup did not finish within {_CLEANUP_TIMEOUT}s "
                "— force-cancelling to prevent WS leak."
            )
            if not self._cleanup_task.done():
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            self._cleanup_task = None
            carb.log_info("Previous execution cleanup finished.")

    def _handle_location(self, loc: float, total: float) -> None:
        self._events.execution_location.emit(loc, total)
        self._events.execution_progress.emit(
            min(loc / total, 1.0),
            f"Executing {int(loc)}/{int(total)}...",
        )

    def _handle_paused(self) -> None:
        """Called by the execution service when the robot has acknowledged the pause."""
        carb.log_info("Execution paused.")
        self._state = ExecutionState.PAUSED
        self._events.execution_paused.emit()

    def _handle_resumed(self) -> None:
        """Called by the execution service when the robot has resumed."""
        carb.log_info("Execution resumed.")
        self._state = ExecutionState.EXECUTING
        self._events.execution_started.emit()
