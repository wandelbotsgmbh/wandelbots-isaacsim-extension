"""Unit tests for ExecutionOrchestrator state machine."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import omni.kit.test

from wandelbots.omni.tests.unit.test_fixtures import make_mock_api_configuration
from wandelbots.omni.ui.tool.trajectory_planner.events import TrajectoryPlannerEvents
from wandelbots.omni.ui.tool.trajectory_planner.execution_orchestrator import (
    ExecutionOrchestrator,
    ExecutionState,
)


def _close_coro_side_effect(coro):
    """Close the coroutine to suppress 'was never awaited' warnings."""
    coro.close()
    return MagicMock()


class TestExecutionOrchestrator(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.api_config = make_mock_api_configuration()
        self.callbacks = {
            "on_progress": MagicMock(),
            "on_joint_update": MagicMock(),
            "on_complete": MagicMock(),
            "on_cancelled": MagicMock(),
            "on_failed": MagicMock(),
            "on_started": MagicMock(),
            "on_paused": MagicMock(),
        }
        self.events = TrajectoryPlannerEvents()
        self.events.execution_progress.connect(self.callbacks["on_progress"])
        self.events.execution_joint_update.connect(self.callbacks["on_joint_update"])
        self.events.execution_complete.connect(self.callbacks["on_complete"])
        self.events.execution_cancelled.connect(self.callbacks["on_cancelled"])
        self.events.execution_failed.connect(self.callbacks["on_failed"])
        self.events.execution_started.connect(self.callbacks["on_started"])
        self.events.execution_paused.connect(self.callbacks["on_paused"])

    async def tearDown(self):
        pass

    def _create_orchestrator(self) -> ExecutionOrchestrator:
        return ExecutionOrchestrator(
            get_api_config=lambda: self.api_config,
            get_stream_params=lambda: ("cell", "ur10e", "0@ur10e"),
            get_selected_tcp=lambda: "tcp_flange",
            get_move_to_start=lambda: False,
            events=self.events,
        )

    # -- Initial state ---------------------------------------------------------

    async def test_initial_state_is_idle(self):
        orch = self._create_orchestrator()
        self.assertEqual(orch.state, ExecutionState.IDLE)
        self.assertTrue(orch.is_idle)
        self.assertFalse(orch.is_executing)
        self.assertFalse(orch.is_paused)

    # -- State properties ------------------------------------------------------

    async def test_is_executing_includes_paused(self):
        orch = self._create_orchestrator()
        orch._state = ExecutionState.PAUSED
        self.assertTrue(orch.is_executing)
        self.assertTrue(orch.is_paused)

    async def test_is_executing_when_executing(self):
        orch = self._create_orchestrator()
        orch._state = ExecutionState.EXECUTING
        self.assertTrue(orch.is_executing)
        self.assertFalse(orch.is_paused)

    # -- Execute ---------------------------------------------------------------

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.execution_orchestrator.run_coroutine"
    )
    async def test_execute_transitions_to_executing(self, mock_run_coroutine):
        mock_run_coroutine.side_effect = _close_coro_side_effect
        orch = self._create_orchestrator()
        trajectory = MagicMock()

        orch.execute(trajectory, num_commands=3)

        self.assertEqual(orch.state, ExecutionState.EXECUTING)
        mock_run_coroutine.assert_called_once()

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.execution_orchestrator.run_coroutine"
    )
    async def test_execute_does_nothing_when_already_executing(
        self, mock_run_coroutine
    ):
        orch = self._create_orchestrator()
        orch._state = ExecutionState.EXECUTING
        trajectory = MagicMock()

        orch.execute(trajectory, num_commands=3)

        mock_run_coroutine.assert_not_called()

    # -- Pause -----------------------------------------------------------------

    async def test_pause_sets_event(self):
        orch = self._create_orchestrator()
        orch._state = ExecutionState.EXECUTING
        orch._pause_event = asyncio.Event()

        orch.pause()

        self.assertTrue(orch._pause_event.is_set())

    async def test_pause_does_nothing_when_not_executing(self):
        orch = self._create_orchestrator()
        orch._state = ExecutionState.IDLE
        orch._pause_event = asyncio.Event()

        orch.pause()

        self.assertFalse(orch._pause_event.is_set())

    # -- Resume ----------------------------------------------------------------

    async def test_resume_sets_event(self):
        orch = self._create_orchestrator()
        orch._state = ExecutionState.PAUSED
        orch._resume_event = asyncio.Event()

        orch.resume()

        self.assertTrue(orch._resume_event.is_set())

    async def test_resume_does_nothing_when_not_paused(self):
        orch = self._create_orchestrator()
        orch._state = ExecutionState.EXECUTING
        orch._resume_event = asyncio.Event()

        orch.resume()

        self.assertFalse(orch._resume_event.is_set())

    # -- Stop ------------------------------------------------------------------

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.execution_orchestrator.run_coroutine"
    )
    async def test_stop_transitions_to_tearing_down(self, mock_run_coroutine):
        mock_run_coroutine.side_effect = _close_coro_side_effect
        orch = self._create_orchestrator()
        orch._state = ExecutionState.EXECUTING
        orch._stop_event = asyncio.Event()
        orch._pause_event = asyncio.Event()
        mock_task = MagicMock()
        orch._execute_task = mock_task

        orch.stop()

        self.assertEqual(orch.state, ExecutionState.TEARING_DOWN)
        self.callbacks["on_cancelled"].assert_called_once()

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.execution_orchestrator.run_coroutine"
    )
    async def test_stop_detaches_from_task(self, mock_run_coroutine):
        mock_run_coroutine.side_effect = _close_coro_side_effect
        orch = self._create_orchestrator()
        orch._state = ExecutionState.EXECUTING
        orch._stop_event = asyncio.Event()
        orch._pause_event = asyncio.Event()
        original_task = MagicMock()
        orch._execute_task = original_task

        orch.stop()

        # Task should be detached and saved as cleanup_task
        self.assertIsNone(orch._execute_task)
        self.assertEqual(orch._cleanup_task, original_task)
        # Events should be cleared
        self.assertIsNone(orch._pause_event)
        self.assertIsNone(orch._resume_event)
        self.assertIsNone(orch._stop_event)

    async def test_stop_does_nothing_when_idle(self):
        orch = self._create_orchestrator()
        orch._state = ExecutionState.IDLE

        orch.stop()

        self.assertEqual(orch.state, ExecutionState.IDLE)
        self.callbacks["on_cancelled"].assert_not_called()

    # -- Destroy ---------------------------------------------------------------

    async def test_destroy_cancels_tasks_and_resets(self):
        orch = self._create_orchestrator()
        orch._state = ExecutionState.EXECUTING
        orch._stop_event = asyncio.Event()
        orch._pause_event = asyncio.Event()
        mock_task = MagicMock()
        orch._execute_task = mock_task

        orch.destroy()

        self.assertEqual(orch.state, ExecutionState.IDLE)
        self.assertIsNone(orch._execute_task)
        self.assertIsNone(orch._pause_event)
        self.assertIsNone(orch._resume_event)
        self.assertIsNone(orch._stop_event)
        mock_task.cancel.assert_called_once()

    # -- Execute from TEARING_DOWN state ---------------------------------------

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.execution_orchestrator.run_coroutine"
    )
    async def test_execute_allowed_during_tearing_down(self, mock_run_coroutine):
        mock_run_coroutine.side_effect = _close_coro_side_effect
        orch = self._create_orchestrator()
        orch._state = ExecutionState.TEARING_DOWN
        teardown_task = MagicMock()
        orch._teardown_task = teardown_task
        trajectory = MagicMock()

        orch.execute(trajectory, num_commands=1)

        self.assertEqual(orch.state, ExecutionState.EXECUTING)
        teardown_task.cancel.assert_called_once()
        self.assertIsNone(orch._teardown_task)
