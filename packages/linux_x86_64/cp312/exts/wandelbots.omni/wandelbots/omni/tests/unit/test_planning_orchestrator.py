"""Unit tests for PlanningOrchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import omni.kit.test

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.tests.unit.test_fixtures import (
    SAMPLE_JOINT_CONFIGS,
    make_mock_api_configuration,
)
from wandelbots.omni.ui.tool.trajectory_planner.events import TrajectoryPlannerEvents
from wandelbots.omni.ui.tool.trajectory_planner.planning_orchestrator import (
    PlanningOrchestrator,
)
from wandelbots.omni.ui.tool.trajectory_planner.pose_tree_widget import (
    PoseItem,
    PoseModel,
)


def _close_coro_side_effect(coro):
    """Close the coroutine to suppress 'was never awaited' warnings."""
    coro.close()
    return MagicMock()


class TestPlanningOrchestrator(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.pose_model = PoseModel()
        self.api_config = make_mock_api_configuration()
        self.callbacks = {
            "on_plan_started": MagicMock(),
            "on_plan_progress": MagicMock(),
            "on_plan_complete": MagicMock(),
            "on_plan_failed": MagicMock(),
        }
        self.events = TrajectoryPlannerEvents()
        self.events.plan_started.connect(self.callbacks["on_plan_started"])
        self.events.plan_progress.connect(self.callbacks["on_plan_progress"])
        self.events.plan_complete.connect(self.callbacks["on_plan_complete"])
        self.events.plan_failed.connect(self.callbacks["on_plan_failed"])

    async def tearDown(self):
        pass

    def _create_orchestrator(self) -> PlanningOrchestrator:
        return PlanningOrchestrator(
            pose_model=self.pose_model,
            get_api_config=lambda: self.api_config,
            get_stream_params=lambda: ("cell", "ur10e", "0@ur10e"),
            get_mg_prim_path=lambda: "/World/robot",
            get_selected_tcp=lambda: "tcp_flange",
            get_collision_setup=lambda: None,
            get_settings=lambda: {"tcp_velocity": 500.0, "tcp_acceleration": 2000.0},
            events=self.events,
        )

    def _add_poses_to_model(self, count: int = 3) -> list[PoseItem]:
        items = []
        for i in range(count):
            item = self.pose_model.add_pose(
                f"/World/p{i}",
                f"Pose_{i}",
                WSPose(pose=[100.0 * i, 200.0, 300.0, 0.0, 3.14, 0.0]),
            )
            item.joint_configs = [SAMPLE_JOINT_CONFIGS[0]]
            item.selected_config_idx = 0
            items.append(item)
        return items

    # -- Initial state ---------------------------------------------------------

    async def test_initial_state(self):
        orch = self._create_orchestrator()
        self.assertFalse(orch.trajectory_planned)
        self.assertIsNone(orch.planned_joint_trajectory)
        self.assertIsNone(orch.trajectory_name)

    # -- plan() validation -----------------------------------------------------

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.planning_orchestrator.run_coroutine"
    )
    async def test_plan_requires_at_least_two_poses(self, mock_run_coroutine):
        orch = self._create_orchestrator()
        self.pose_model.add_pose("/World/p0", "P0", WSPose(pose=[0, 0, 0, 0, 0, 0]))

        orch.plan()

        mock_run_coroutine.assert_not_called()
        self.callbacks["on_plan_started"].assert_not_called()

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.planning_orchestrator.run_coroutine"
    )
    async def test_plan_requires_stream_params(self, mock_run_coroutine):
        orch = PlanningOrchestrator(
            pose_model=self.pose_model,
            get_api_config=lambda: self.api_config,
            get_stream_params=lambda: None,  # No stream params
            get_mg_prim_path=lambda: None,
            get_selected_tcp=lambda: None,
            get_collision_setup=lambda: None,
            get_settings=lambda: {},
            events=TrajectoryPlannerEvents(),
        )
        self._add_poses_to_model(3)

        orch.plan()

        mock_run_coroutine.assert_not_called()

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.planning_orchestrator.run_coroutine"
    )
    async def test_plan_starts_task_with_valid_poses(self, mock_run_coroutine):
        mock_run_coroutine.side_effect = _close_coro_side_effect
        orch = self._create_orchestrator()
        self._add_poses_to_model(3)

        orch.plan()

        mock_run_coroutine.assert_called_once()
        self.callbacks["on_plan_started"].assert_called_once()

    # -- invalidate() ----------------------------------------------------------

    async def test_invalidate_clears_trajectory(self):
        orch = self._create_orchestrator()
        orch._trajectory_planned = True
        orch._planned_joint_trajectory = MagicMock()
        items = self._add_poses_to_model(2)
        items[0].reachable = True
        items[1].planned = True

        orch.invalidate()

        self.assertFalse(orch.trajectory_planned)
        self.assertIsNone(orch.planned_joint_trajectory)
        for item in items:
            self.assertIsNone(item.reachable)
            self.assertIsNone(item.planned)

    async def test_invalidate_does_nothing_when_not_planned(self):
        orch = self._create_orchestrator()
        items = self._add_poses_to_model(2)
        items[0].reachable = True

        orch.invalidate()

        # Still clears reachable state on poses
        self.assertIsNone(items[0].reachable)

    # -- set_planned() ---------------------------------------------------------

    async def test_set_planned(self):
        orch = self._create_orchestrator()
        orch.set_planned(True)
        self.assertTrue(orch.trajectory_planned)
        orch.set_planned(False)
        self.assertFalse(orch.trajectory_planned)

    # -- restore_trajectory() --------------------------------------------------

    async def test_restore_trajectory(self):
        orch = self._create_orchestrator()
        trajectory = MagicMock()

        orch.restore_trajectory(trajectory)

        self.assertTrue(orch.trajectory_planned)
        self.assertEqual(orch.planned_joint_trajectory, trajectory)

    # -- set_skill_name() ------------------------------------------------------

    async def test_set_skill_name(self):
        orch = self._create_orchestrator()
        orch.set_skill_name("my_skill")
        self.assertEqual(orch._skill_name, "my_skill")

    # -- destroy() -------------------------------------------------------------

    async def test_destroy_cancels_running_task(self):
        orch = self._create_orchestrator()
        mock_task = MagicMock()
        orch._plan_task = mock_task

        orch.destroy()

        mock_task.cancel.assert_called_once()
        self.assertIsNone(orch._plan_task)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.planning_orchestrator.run_coroutine"
    )
    async def test_plan_cancels_existing_task(self, mock_run_coroutine):
        orch = self._create_orchestrator()
        self._add_poses_to_model(3)

        existing_task = MagicMock()
        existing_task.done.return_value = False
        orch._plan_task = existing_task

        orch.plan()

        existing_task.cancel.assert_called_once()
        # Should NOT start a new task — just cancel
        mock_run_coroutine.assert_not_called()
