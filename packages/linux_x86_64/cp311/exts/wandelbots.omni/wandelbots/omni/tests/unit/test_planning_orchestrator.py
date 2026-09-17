"""Unit tests for PlanningOrchestrator."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import omni.kit.test
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.tests.unit.test_fixtures import (
    SAMPLE_JOINT_CONFIGS,
    make_mock_api_configuration,
)
from wandelbots.omni.ui.tool.planner_utils import PlanFailure
from wandelbots.omni.ui.tool.trajectory_planner.events import TrajectoryPlannerEvents
from wandelbots.omni.ui.tool.trajectory_planner.planning_orchestrator import (
    PlanningOrchestrator,
    failed_pose_index,
    mark_planning_failure,
)
from wandelbots.omni.ui.tool.trajectory_planner.pose_tree_widget import (
    PoseItem,
    PoseModel,
)

_ORCHESTRATOR = "wandelbots.omni.ui.tool.trajectory_planner.planning_orchestrator"


def _close_coro_side_effect(coro):
    """Close the coroutine to suppress 'was never awaited' warnings."""
    coro.close()
    return MagicMock()


def _trajectory(sample_count: int) -> wb_v2_models.JointTrajectory:
    return wb_v2_models.JointTrajectory(
        joint_positions=SAMPLE_JOINT_CONFIGS[:sample_count],
        locations=[float(i) for i in range(sample_count)],
        times=[float(i) for i in range(sample_count)],
    )


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

    def _create_orchestrator(
        self, collision_setup: str | None = None, settings: dict | None = None
    ) -> PlanningOrchestrator:
        all_settings = {"tcp_velocity": 500.0, "tcp_acceleration": 2000.0}
        all_settings.update(settings or {})
        return PlanningOrchestrator(
            pose_model=self.pose_model,
            get_api_config=lambda: self.api_config,
            get_stream_params=lambda: ("cell", "ur10e", "0@ur10e"),
            get_mg_prim_path=lambda: "/World/robot",
            get_selected_tcp=lambda: "tcp_flange",
            get_collision_setup=lambda: collision_setup,
            get_settings=lambda: all_settings,
            events=self.events,
        )

    async def _run_plan(self, orch: PlanningOrchestrator) -> None:
        """Start plan() and wait for its task instead of Kit's async engine."""
        with patch(f"{_ORCHESTRATOR}.run_coroutine", side_effect=asyncio.ensure_future):
            orch.plan()
            task = orch._plan_task
        await task

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

    async def test_invalidate_keeps_trajectory_but_marks_stale(self):
        # By default invalidate() must NOT delete the planned trajectory; it keeps
        # the curve/result and only marks it stale so the user still sees the last
        # result until they re-plan. (Per-pose reachable/planned state is cleared.)
        orch = self._create_orchestrator()
        orch._trajectory_planned = True
        trajectory = MagicMock()
        orch._planned_joint_trajectory = trajectory
        items = self._add_poses_to_model(2)
        items[0].reachable = True
        items[1].planned = True

        orch.invalidate()

        self.assertTrue(orch.trajectory_planned)
        self.assertEqual(orch.planned_joint_trajectory, trajectory)
        self.assertTrue(orch.trajectory_stale)
        for item in items:
            self.assertIsNone(item.reachable)
            self.assertIsNone(item.planned)

    async def test_invalidate_with_remove_visualization_clears_trajectory(self):
        orch = self._create_orchestrator()
        orch._trajectory_planned = True
        orch._planned_joint_trajectory = MagicMock()
        items = self._add_poses_to_model(2)
        items[0].reachable = True
        items[1].planned = True

        orch.invalidate(remove_visualization=True)

        self.assertFalse(orch.trajectory_planned)
        self.assertIsNone(orch.planned_joint_trajectory)
        self.assertFalse(orch.trajectory_stale)
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

    async def test_invalidate_emits_plan_invalidated(self):
        on_invalidated = MagicMock()
        self.events.plan_invalidated.connect(on_invalidated)
        orch = self._create_orchestrator()

        orch.invalidate()

        on_invalidated.assert_called_once()

    # -- plan failure ----------------------------------------------------------

    async def test_plan_failed_payload_is_plan_failure_when_no_collision_scene(self):
        orch = self._create_orchestrator(settings={"plan_collision_free": True})
        self._add_poses_to_model(2)

        await self._run_plan(orch)

        failure = self.callbacks["on_plan_failed"].call_args.args[0]
        self.assertIsInstance(failure, PlanFailure)
        self.assertEqual(failure.error, "No collision scene selected")

    @patch(f"{_ORCHESTRATOR}.plan_trajectory_segments")
    async def test_plan_failure_marks_failing_pose_red(self, mock_plan_segments):
        failure = PlanFailure(
            error="FeedbackCollision",
            failed_joint_position=SAMPLE_JOINT_CONFIGS[1],
            error_location_on_trajectory=0.5,
            segment_index=0,
        )
        mock_plan_segments.return_value = failure
        orch = self._create_orchestrator()
        items = self._add_poses_to_model(3)

        await self._run_plan(orch)

        self.assertEqual([item.planned for item in items], [True, False, None])
        self.callbacks["on_plan_failed"].assert_called_once_with(failure)
        self.assertFalse(orch.trajectory_planned)

    @patch(f"{_ORCHESTRATOR}.plan_collision_free")
    async def test_collision_free_failure_shows_unreachable_target(self, mock_plan_cf):
        mock_plan_cf.return_value = PlanFailure(error="max iterations", segment_index=1)
        orch = self._create_orchestrator(
            collision_setup="scene", settings={"plan_collision_free": True}
        )
        items = self._add_poses_to_model(3)
        items[2].joint_configs = [SAMPLE_JOINT_CONFIGS[2]]

        await self._run_plan(orch)

        failure = self.callbacks["on_plan_failed"].call_args.args[0]
        self.assertEqual(failure.failed_joint_position, SAMPLE_JOINT_CONFIGS[2])
        self.assertEqual([item.planned for item in items], [True, True, False])

    # -- render helpers --------------------------------------------------------

    @contextlib.contextmanager
    def _render_patches(self, service, builder, started: list | None = None):
        """Patch the render path: run on this loop, use the given FK service and
        curve builder, and draw without a USD stage. Tasks the orchestrator starts
        are appended to ``started`` when a list is given."""

        def start(render):
            task = asyncio.ensure_future(render)
            if started is not None:
                started.append(task)
            return task

        no_stage = MagicMock()
        no_stage.get_stage.return_value = None
        with (
            patch(f"{_ORCHESTRATOR}.run_coroutine", side_effect=start),
            patch(
                f"{_ORCHESTRATOR}.get_trajectory_planner_service", return_value=service
            ),
            patch(f"{_ORCHESTRATOR}.get_trajectory_builder", return_value=builder),
            patch(f"{_ORCHESTRATOR}.omni.usd.get_context", return_value=no_stage),
        ):
            yield

    async def test_visualize_failed_trajectory_draws_tracked_red_curve(self):
        orch = self._create_orchestrator()
        orch.set_skill_name("my skill")
        service = MagicMock()
        service.forward_kinematics = AsyncMock(return_value=[[0.0] * 6, [1.0] * 6])
        builder = MagicMock()
        started: list = []

        with self._render_patches(service, builder, started):
            orch.visualize_failed_trajectory(SAMPLE_JOINT_CONFIGS[:2])
            await started[0]
            builder.create_trajectory.assert_called_once()
            data = builder.create_trajectory.call_args.args[0]
            self.assertEqual(data.name, "my_skill_failed")
            self.assertEqual(tuple(data.options.color), (255, 0, 0))

            orch.invalidate()

        builder.remove_trajectory.assert_called_once_with("my_skill_failed")

    async def test_remove_segment_trajectories_cancels_pending_render(self):
        orch = self._create_orchestrator()
        forward_kinematics_started = asyncio.Event()
        never_finished = asyncio.Event()

        async def blocked_forward_kinematics(**_kwargs):
            forward_kinematics_started.set()
            await never_finished.wait()

        service = MagicMock()
        service.forward_kinematics = blocked_forward_kinematics
        builder = MagicMock()
        started: list = []

        with self._render_patches(service, builder, started):
            orch.visualize_failed_trajectory(SAMPLE_JOINT_CONFIGS[:2])
            await forward_kinematics_started.wait()
            # A replan or edit clears the segment curves while FK is in flight.
            orch.invalidate()
            await asyncio.sleep(0)

        self.assertTrue(started[0].cancelled())
        builder.create_trajectory.assert_not_called()
        builder.remove_trajectory.assert_not_called()

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
        self.assertIsNone(orch.planned_via_joint_positions)

    async def test_restore_trajectory_keeps_via_points(self):
        orch = self._create_orchestrator()

        orch.restore_trajectory(
            MagicMock(), via_joint_positions=[SAMPLE_JOINT_CONFIGS[1]]
        )

        self.assertEqual(orch.planned_via_joint_positions, [SAMPLE_JOINT_CONFIGS[1]])

    async def test_invalidate_with_remove_visualization_clears_via_points(self):
        orch = self._create_orchestrator()
        orch.restore_trajectory(
            MagicMock(), via_joint_positions=[SAMPLE_JOINT_CONFIGS[1]]
        )

        orch.invalidate(remove_visualization=True)

        self.assertIsNone(orch.planned_via_joint_positions)

    # -- via-point markers -----------------------------------------------------

    def _restored_orchestrator(self, via_joint_positions) -> PlanningOrchestrator:
        orch = self._create_orchestrator()
        orch.set_skill_name("my skill")
        orch.restore_trajectory(_trajectory(3), via_joint_positions=via_joint_positions)
        return orch

    async def test_visualize_trajectory_marks_via_points(self):
        orch = self._restored_orchestrator([SAMPLE_JOINT_CONFIGS[1]])
        curve_poses = [[0.0] * 6, [100.0] * 6, [200.0] * 6]
        via_poses = [[100.0, 0.0, 300.0, 0.0, 0.0, 0.0]]
        service = MagicMock()
        service.forward_kinematics = AsyncMock(side_effect=[curve_poses, via_poses])
        builder = MagicMock()
        builder.create_trajectory_async = AsyncMock()

        with self._render_patches(service, builder):
            drawn = await orch.visualize_trajectory([0.5, 0.5, 0.5])

        self.assertTrue(drawn)
        builder.create_marker.assert_called_once()
        name, marker = builder.create_marker.call_args.args
        self.assertEqual(name, "my_skill_trajectory")
        self.assertEqual(marker.prim.type, "sphere")
        self.assertEqual(marker.poses, via_poses)

    async def test_visualize_trajectory_without_via_points_adds_no_markers(self):
        orch = self._restored_orchestrator(None)
        service = MagicMock()
        service.forward_kinematics = AsyncMock(
            return_value=[[0.0] * 6, [100.0] * 6, [200.0] * 6]
        )
        builder = MagicMock()
        builder.create_trajectory_async = AsyncMock()

        with self._render_patches(service, builder):
            self.assertTrue(await orch.visualize_trajectory([0.5, 0.5, 0.5]))

        builder.create_marker.assert_not_called()
        service.forward_kinematics.assert_awaited_once()

    async def test_marker_failure_keeps_curve_visualized(self):
        orch = self._restored_orchestrator([SAMPLE_JOINT_CONFIGS[1]])
        service = MagicMock()
        service.forward_kinematics = AsyncMock(
            side_effect=[[[0.0] * 6, [100.0] * 6, [200.0] * 6], RuntimeError("fk down")]
        )
        builder = MagicMock()
        builder.create_trajectory_async = AsyncMock()

        with self._render_patches(service, builder):
            self.assertTrue(await orch.visualize_trajectory([0.5, 0.5, 0.5]))

        builder.create_marker.assert_not_called()

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


class TestFailedPoseIndex(omni.kit.test.AsyncTestCase):
    # Two TCP runs over five target poses; poses[0] is the start pose.
    RUNS = [[0, 1], [2, 3, 4]]

    def _failure(self, segment_index, location) -> PlanFailure:
        return PlanFailure(
            error="x",
            segment_index=segment_index,
            error_location_on_trajectory=location,
        )

    async def test_fractional_location_marks_pose_being_approached(self):
        self.assertEqual(failed_pose_index(self.RUNS, self._failure(1, 1.3)), 4)

    async def test_integer_location_marks_arrival_pose(self):
        self.assertEqual(failed_pose_index(self.RUNS, self._failure(1, 2.0)), 4)

    async def test_zero_location_marks_first_pose_of_run(self):
        self.assertEqual(failed_pose_index(self.RUNS, self._failure(1, 0.0)), 3)

    async def test_location_past_run_clamps_to_last_pose(self):
        self.assertEqual(failed_pose_index(self.RUNS, self._failure(1, 7.0)), 5)

    async def test_missing_location_marks_last_pose_of_run(self):
        runs = [[0], [1], [2]]
        self.assertEqual(failed_pose_index(runs, self._failure(1, None)), 2)

    async def test_missing_or_invalid_segment_returns_none(self):
        self.assertIsNone(failed_pose_index(self.RUNS, self._failure(None, 1.0)))
        self.assertIsNone(failed_pose_index(self.RUNS, self._failure(5, 1.0)))


class TestMarkPlanningFailure(omni.kit.test.AsyncTestCase):
    def _items(self, count: int) -> list:
        return [SimpleNamespace(planned=True) for _ in range(count)]

    async def test_marks_failing_pose_and_resets_later_ones(self):
        items = self._items(4)

        mark_planning_failure(items, 2)

        self.assertEqual([item.planned for item in items], [True, True, False, None])

    async def test_unknown_failure_resets_all(self):
        items = self._items(3)

        mark_planning_failure(items, None)

        self.assertEqual([item.planned for item in items], [None, None, None])
