"""Unit tests for IKManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import omni.kit.test

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.tests.unit.test_fixtures import (
    SAMPLE_JOINT_CONFIGS,
    make_mock_api_configuration,
)
from wandelbots.omni.ui.tool.trajectory_planner.events import TrajectoryPlannerEvents
from wandelbots.omni.ui.tool.trajectory_planner.ik_manager import IKManager
from wandelbots.omni.ui.tool.trajectory_planner.pose_tree_widget import (
    PoseItem,
    PoseModel,
)
from wandelbots.omni.ui.tool.trajectory_planner.service.ik_service import IKResult


def _close_coro_side_effect(coro):
    """Close the coroutine to suppress 'was never awaited' warnings."""
    coro.close()
    return MagicMock()


class TestIKManager(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.pose_model = PoseModel()
        self.api_config = make_mock_api_configuration()
        self.callbacks = {
            "on_ik_progress": MagicMock(),
            "on_ik_complete": MagicMock(),
            "on_reachability_complete": MagicMock(),
        }
        self.events = TrajectoryPlannerEvents()
        self.events.ik_progress.connect(self.callbacks["on_ik_progress"])
        self.events.ik_complete.connect(self.callbacks["on_ik_complete"])
        self.events.reachability_complete.connect(
            self.callbacks["on_reachability_complete"]
        )

    async def tearDown(self):
        pass

    def _create_manager(self, stream_params=("cell", "ur10e", "0@ur10e")) -> IKManager:
        return IKManager(
            pose_model=self.pose_model,
            get_api_config=lambda: self.api_config,
            get_stream_params=lambda: stream_params,
            get_selected_tcp=lambda: "tcp_flange",
            get_collision_setup=lambda: None,
            events=self.events,
        )

    def _add_pose(self, idx: int = 0) -> PoseItem:
        return self.pose_model.add_pose(
            f"/World/p{idx}",
            f"Pose_{idx}",
            WSPose(pose=[100.0 * idx, 200.0, 300.0, 0.0, 3.14, 0.0]),
        )

    # -- Initial state ---------------------------------------------------------

    async def test_initial_state(self):
        mgr = self._create_manager()
        self.assertEqual(mgr.ik_pending_count, 0)

    # -- fetch_ik_for_pose -----------------------------------------------------

    @patch("wandelbots.omni.ui.tool.trajectory_planner.ik_manager.run_coroutine")
    async def test_fetch_ik_increments_pending_count(self, mock_run_coroutine):
        mock_run_coroutine.side_effect = _close_coro_side_effect
        mgr = self._create_manager()
        item = self._add_pose()

        mgr.fetch_ik_for_pose(item)

        self.assertEqual(mgr.ik_pending_count, 1)
        self.callbacks["on_ik_progress"].assert_called()
        mock_run_coroutine.assert_called_once()

    @patch("wandelbots.omni.ui.tool.trajectory_planner.ik_manager.run_coroutine")
    async def test_fetch_ik_skips_without_stream_params(self, mock_run_coroutine):
        mgr = self._create_manager(stream_params=None)
        item = self._add_pose()

        mgr.fetch_ik_for_pose(item)

        mock_run_coroutine.assert_not_called()
        self.assertEqual(mgr.ik_pending_count, 0)

    # -- refresh_ik_for_pose ---------------------------------------------------

    @patch("wandelbots.omni.ui.tool.trajectory_planner.ik_manager.run_coroutine")
    async def test_refresh_ik_clears_configs_and_refetches(self, mock_run_coroutine):
        mock_run_coroutine.side_effect = _close_coro_side_effect
        mgr = self._create_manager()
        item = self._add_pose()
        item.joint_configs = [SAMPLE_JOINT_CONFIGS[0]]
        item.selected_config_idx = 1

        mgr.refresh_ik_for_pose(item)

        self.assertEqual(item.joint_configs, [])
        self.assertEqual(item.selected_config_idx, 0)
        mock_run_coroutine.assert_called_once()

    # -- refresh_all_ik --------------------------------------------------------

    @patch("wandelbots.omni.ui.tool.trajectory_planner.ik_manager.run_coroutine")
    async def test_refresh_all_ik_sets_loading_state(self, mock_run_coroutine):
        mock_run_coroutine.side_effect = _close_coro_side_effect
        mgr = self._create_manager()
        items = [self._add_pose(i) for i in range(3)]

        mgr.refresh_all_ik()

        for item in items:
            self.assertTrue(item.ik_loading)
            self.assertEqual(item.joint_configs, [])
        self.assertEqual(mgr.ik_pending_count, 3)

    @patch("wandelbots.omni.ui.tool.trajectory_planner.ik_manager.run_coroutine")
    async def test_refresh_all_ik_cancels_previous_task(self, mock_run_coroutine):
        mock_run_coroutine.side_effect = _close_coro_side_effect
        mgr = self._create_manager()
        self._add_pose(0)

        old_task = MagicMock()
        mgr._ik_task = old_task

        mgr.refresh_all_ik()

        old_task.cancel.assert_called_once()

    @patch("wandelbots.omni.ui.tool.trajectory_planner.ik_manager.run_coroutine")
    async def test_refresh_all_ik_does_nothing_without_params(self, mock_run_coroutine):
        mgr = self._create_manager(stream_params=None)
        self._add_pose(0)

        mgr.refresh_all_ik()

        mock_run_coroutine.assert_not_called()

    # -- check_reachability ----------------------------------------------------

    @patch("wandelbots.omni.ui.tool.trajectory_planner.ik_manager.run_coroutine")
    async def test_check_reachability_starts_task(self, mock_run_coroutine):
        mock_run_coroutine.side_effect = _close_coro_side_effect
        mgr = self._create_manager()
        self._add_pose(0)

        mgr.check_reachability()

        mock_run_coroutine.assert_called_once()

    @patch("wandelbots.omni.ui.tool.trajectory_planner.ik_manager.run_coroutine")
    async def test_check_reachability_does_nothing_for_empty_poses(
        self, mock_run_coroutine
    ):
        mgr = self._create_manager()
        # No poses added

        mgr.check_reachability()

        mock_run_coroutine.assert_not_called()

    @patch("wandelbots.omni.ui.tool.trajectory_planner.ik_manager.run_coroutine")
    async def test_check_reachability_cancels_previous(self, mock_run_coroutine):
        mock_run_coroutine.side_effect = _close_coro_side_effect
        mgr = self._create_manager()
        self._add_pose(0)
        old_task = MagicMock()
        mgr._reachability_task = old_task

        mgr.check_reachability()

        old_task.cancel.assert_called_once()

    # -- destroy ---------------------------------------------------------------

    async def test_destroy_cancels_all_tasks(self):
        mgr = self._create_manager()
        ik_task = MagicMock()
        reach_task = MagicMock()
        mgr._ik_task = ik_task
        mgr._reachability_task = reach_task

        mgr.destroy()

        ik_task.cancel.assert_called_once()
        reach_task.cancel.assert_called_once()
        self.assertIsNone(mgr._ik_task)
        self.assertIsNone(mgr._reachability_task)

    # -- _do_fetch_ik (async) --------------------------------------------------

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.ik_manager.get_trajectory_planner_service"
    )
    async def test_do_fetch_ik_updates_item_on_success(self, mock_get_service):
        mock_service = AsyncMock()
        mock_get_service.return_value = mock_service
        mock_service.fetch_ik.return_value = IKResult(
            joint_configs=[SAMPLE_JOINT_CONFIGS[0], SAMPLE_JOINT_CONFIGS[1]]
        )

        mgr = self._create_manager()
        item = self._add_pose()
        mgr._ik_pending_count = 1

        await mgr._do_fetch_ik(item)

        self.assertEqual(len(item.joint_configs), 2)
        self.assertEqual(item.selected_config_idx, 0)
        self.assertFalse(item.ik_loading)
        self.assertEqual(mgr.ik_pending_count, 0)
        self.callbacks["on_ik_complete"].assert_called_once_with(item)

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.ik_manager.get_trajectory_planner_service"
    )
    async def test_do_fetch_ik_handles_exception(self, mock_get_service):
        mock_service = AsyncMock()
        mock_get_service.return_value = mock_service
        mock_service.fetch_ik.side_effect = Exception("Network error")

        mgr = self._create_manager()
        item = self._add_pose()
        mgr._ik_pending_count = 1

        await mgr._do_fetch_ik(item)

        self.assertEqual(item.joint_configs, [])
        self.assertFalse(item.ik_loading)
        self.assertEqual(mgr.ik_pending_count, 0)
        self.callbacks["on_ik_complete"].assert_called_once_with(item)

    # -- _do_check_reachability (async) ----------------------------------------

    @patch(
        "wandelbots.omni.ui.tool.trajectory_planner.ik_manager.get_trajectory_planner_service"
    )
    async def test_do_check_reachability_sets_reachable_flags(self, mock_get_service):
        mock_service = AsyncMock()
        mock_get_service.return_value = mock_service

        items = [self._add_pose(i) for i in range(3)]

        # Simulate on_result callback during fetch_ik_batch
        async def mock_fetch_ik_batch(**kwargs):
            on_result = kwargs.get("on_result")
            results = [
                IKResult(joint_configs=[SAMPLE_JOINT_CONFIGS[0]]),  # reachable
                IKResult(joint_configs=[]),  # unreachable
                IKResult(joint_configs=[SAMPLE_JOINT_CONFIGS[1]]),  # reachable
            ]
            for idx, result in enumerate(results):
                if on_result:
                    on_result(idx, result)
            return results

        mock_service.fetch_ik_batch = mock_fetch_ik_batch

        mgr = self._create_manager()
        await mgr._do_check_reachability()

        self.assertTrue(items[0].reachable)
        self.assertFalse(items[1].reachable)
        self.assertTrue(items[2].reachable)
        self.callbacks["on_reachability_complete"].assert_called_once_with(2, 1)
