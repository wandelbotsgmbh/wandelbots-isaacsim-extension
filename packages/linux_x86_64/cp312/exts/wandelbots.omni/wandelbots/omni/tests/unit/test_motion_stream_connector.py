from unittest import mock

import omni.kit.test
import omni.usd
from pxr import PhysicsSchemaTools, UsdGeom, UsdPhysics
from wandelbots_api_client.v2.models import JointTypeEnum

import wandelbots.omni.manipulators.motion_stream_connector as msc
from wandelbots.omni.manipulators.motion_stream_connector import (
    _DIRECT_PHYSICS_WRITE_UNAVAILABLE,
    _JOINT_TARGET_EPSILON,
    MotionStreamConnector,
)


def _bare_connector() -> MotionStreamConnector:
    """Connector without running __init__ (which wires websocket/API config):
    only the joint-application state exercised by these tests is populated."""
    connector = MotionStreamConnector.__new__(MotionStreamConnector)
    connector.motion_group = mock.MagicMock()
    connector.motion_group.articulation.is_valid.return_value = True
    # A live physics view: _refresh_measured_state reads back only with one.
    connector.motion_group.articulation._articulation_view._physics_view = (
        mock.MagicMock()
    )
    connector.motion_group.motion_group_dh_parameters = []
    # Real bool: is_external_joint_stream gates the measured-state readback,
    # and a bare MagicMock attribute would be truthy.
    connector.motion_group.configuration.motion_stream_configuration.use_external_joint_stream = False
    connector.timeline = mock.MagicMock()
    connector.timeline.is_stopped.return_value = False
    connector.stream_joint_count = 6
    connector.joint_indices = None
    connector._last_applied_joints = None
    connector._pending_joint_positions = None
    connector._last_joints = None
    connector._joint_indices_tensor = None
    connector._has_prismatic_joints = False
    connector._stream_zeros = [0.0] * 6
    connector._measured_positions = None
    connector._measured_velocities = None
    connector._readback_failure_logged = False
    connector._physics_view_probe_logged = False
    connector._physics_feedback = True
    # Force the apply_action fallback so the dedupe tests below can count
    # calls on the mocked articulation; the direct physics-view path is
    # exercised against a real articulation, not in these unit tests.
    connector._direct_physics_write = _DIRECT_PHYSICS_WRITE_UNAVAILABLE
    # Idle-sleep talks to the omni.physx interface; disabled for unit tests.
    connector._sleep_unavailable = True
    connector._sleep_body_ids = None
    connector._sleep_paths = None
    connector._sleep_physics_view = None
    connector._sleep_failure_logged = False
    connector._idle_frames = 0
    connector._apply_rejection_logged = False
    return connector


class TestPendingJointCoalescing(omni.kit.test.AsyncTestCase):
    async def test_pending_target_is_applied_once_and_cleared(self):
        connector = _bare_connector()
        applied: list[list[float]] = []
        connector.apply_joints = applied.append

        connector._pending_joint_positions = [1.0] * 6
        connector.apply_pending_joints()
        connector.apply_pending_joints()  # nothing pending on the second frame

        self.assertEqual([[1.0] * 6], applied)
        self.assertIsNone(connector._pending_joint_positions)

    async def test_newest_received_target_wins(self):
        connector = _bare_connector()
        applied: list[list[float]] = []
        connector.apply_joints = applied.append

        connector._update_joints([0.0] * 6)
        connector._update_joints([0.5] * 6)
        connector.apply_pending_joints()

        self.assertEqual([[0.5] * 6], applied)

    async def test_pending_target_is_dropped_while_timeline_stopped(self):
        connector = _bare_connector()
        applied: list[list[float]] = []
        connector.apply_joints = applied.append
        connector.timeline.is_stopped.return_value = True

        connector._pending_joint_positions = [1.0] * 6
        connector.apply_pending_joints()

        self.assertEqual([], applied)
        self.assertIsNone(connector._pending_joint_positions)


class TestApplyJointsDedupe(omni.kit.test.AsyncTestCase):
    def _apply_action_count(self, connector: MotionStreamConnector) -> int:
        return connector.motion_group.articulation.apply_action.call_count

    async def test_unchanged_target_is_not_reapplied(self):
        connector = _bare_connector()

        connector.apply_joints([0.1] * 6)
        connector.apply_joints([0.1] * 6)

        self.assertEqual(1, self._apply_action_count(connector))

    async def test_target_above_epsilon_is_reapplied(self):
        connector = _bare_connector()

        connector.apply_joints([0.1] * 6)
        connector.apply_joints([0.1] * 5 + [0.1 + 1e-3])

        self.assertEqual(2, self._apply_action_count(connector))

    async def test_epsilon_compares_against_last_applied_not_last_received(self):
        # Slow drift must not be swallowed: within-epsilon targets do not
        # update the snapshot, so accumulated drift eventually exceeds the
        # threshold relative to the last APPLIED target and gets applied.
        connector = _bare_connector()
        step = _JOINT_TARGET_EPSILON * 0.6

        connector.apply_joints([0.1] * 6)
        connector.apply_joints([0.1 + step] * 6)  # within epsilon: skipped
        self.assertEqual(1, self._apply_action_count(connector))

        connector.apply_joints([0.1 + 2 * step] * 6)  # drift now exceeds it
        self.assertEqual(2, self._apply_action_count(connector))


def _external_connector() -> MotionStreamConnector:
    """External-joint-stream connector with a playing timeline; replies are
    captured through the async send stubs the individual tests install."""
    connector = _bare_connector()
    connector.motion_group.configuration.motion_stream_configuration.use_external_joint_stream = True
    connector.timeline.is_playing.return_value = True
    return connector


class TestExternalStreamPhysicsFeedback(omni.kit.test.AsyncTestCase):
    """Measured-state feedback: external-stream replies carry the PhysX
    readback sample instead of echoing the commanded positions."""

    @staticmethod
    def _capture_sends(connector) -> list[tuple[list[float], list[float] | None]]:
        sent: list[tuple[list[float], list[float] | None]] = []

        async def capture(positions, velocities=None):
            sent.append((positions, velocities))

        connector._send_positions_raw = capture
        connector.send_joint_positions = capture
        return sent

    async def test_reply_uses_measured_state_when_available(self):
        connector = _external_connector()
        sent = self._capture_sends(connector)
        connector._measured_positions = [0.5] * 6
        connector._measured_velocities = [0.1] * 6

        await connector._parse({"result": [{"value": {"positions": [1.0] * 6}}]})

        self.assertEqual(sent, [([0.5] * 6, [0.1] * 6)])
        # The command is still applied as the physics target.
        self.assertEqual(connector._pending_joint_positions, [1.0] * 6)

    async def test_reply_echoes_command_before_first_readback(self):
        connector = _external_connector()
        sent = self._capture_sends(connector)

        await connector._parse({"result": [{"value": {"positions": [1.0] * 6}}]})

        self.assertEqual(sent, [([1.0] * 6, None)])

    async def test_paused_reply_prefers_measured_positions(self):
        connector = _external_connector()
        connector.timeline.is_playing.return_value = False
        sent = self._capture_sends(connector)
        connector._measured_positions = [0.5] * 6
        connector._last_joints = [9.0] * 6

        await connector._parse({"result": [{"value": {"positions": [1.0] * 6}}]})

        self.assertEqual(sent, [([0.5] * 6, None)])

    async def test_refresh_selects_stream_joints_in_index_order(self):
        connector = _external_connector()
        connector.stream_joint_count = 2
        connector.joint_indices = [2, 0]
        connector.motion_group.articulation.get_joint_positions.return_value = [
            10.0,
            11.0,
            12.0,
        ]
        connector.motion_group.articulation.get_joint_velocities.return_value = [
            1.0,
            2.0,
            3.0,
        ]

        connector._refresh_measured_state()

        self.assertEqual(connector._measured_positions, [12.0, 10.0])
        self.assertEqual(connector._measured_velocities, [3.0, 1.0])

    async def test_refresh_scales_prismatic_joints_to_mm(self):
        connector = _external_connector()
        connector.stream_joint_count = 2
        connector.joint_indices = [0, 1]
        connector._has_prismatic_joints = True
        connector.motion_group.motion_group_dh_parameters = [
            mock.MagicMock(type=JointTypeEnum.PRISMATIC_JOINT),
            mock.MagicMock(type="revolute"),
        ]
        connector.motion_group.articulation.get_joint_positions.return_value = [
            0.5,
            1.0,
        ]
        connector.motion_group.articulation.get_joint_velocities.return_value = [
            0.2,
            2.0,
        ]

        with (
            mock.patch.object(msc.omni.usd, "get_context"),
            mock.patch.object(msc.UsdGeom, "GetStageMetersPerUnit", return_value=1.0),
        ):  # stage in meters
            connector._refresh_measured_state()

        self.assertEqual(connector._measured_positions, [500.0, 1.0])
        self.assertEqual(connector._measured_velocities, [200.0, 2.0])

    async def test_readback_failure_drops_the_sample_and_logs_once(self):
        """A sample that can no longer be refreshed must go, or replies would
        repeat a frozen state the controller waits on forever."""
        connector = _external_connector()
        connector._measured_positions = [0.5] * 6
        connector._measured_velocities = [0.1] * 6
        connector.motion_group.articulation.get_joint_positions.side_effect = (
            RuntimeError("physics view torn down")
        )

        with mock.patch.object(msc.carb, "log_warn") as log_warn:
            connector._refresh_measured_state()
            connector._refresh_measured_state()

        self.assertIsNone(connector._measured_positions)
        self.assertIsNone(connector._measured_velocities)
        self.assertEqual(1, log_warn.call_count)

    async def test_reply_echoes_command_again_after_readback_failure(self):
        """The reply degrades to the command echo, rather than repeating the
        last measured sample."""
        connector = _external_connector()
        sent = self._capture_sends(connector)
        connector._measured_positions = [0.5] * 6
        connector._measured_velocities = [0.1] * 6
        connector.motion_group.articulation.get_joint_positions.side_effect = (
            RuntimeError("physics view torn down")
        )

        connector._refresh_measured_state()
        await connector._parse({"result": [{"value": {"positions": [1.0] * 6}}]})

        self.assertEqual(sent, [([1.0] * 6, None)])

    async def test_missing_physics_view_drops_the_sample(self):
        connector = _external_connector()
        connector._measured_positions = [0.5] * 6
        connector.motion_group.articulation._articulation_view._physics_view = None

        connector._refresh_measured_state()

        self.assertIsNone(connector._measured_positions)

    async def test_changed_isaacsim_internals_are_reported_once(self):
        """The physics-view check reads a private attribute; if that ever
        disappears the feature must say so, not fail silently."""
        connector = _external_connector()
        del connector.motion_group.articulation._articulation_view

        with mock.patch.object(msc.carb, "log_warn") as log_warn:
            self.assertIsNone(
                connector._live_physics_view(connector.motion_group.articulation)
            )
            connector._live_physics_view(connector.motion_group.articulation)

        self.assertEqual(1, log_warn.call_count)

    async def test_apply_pending_joints_skips_readback_when_disabled(self):
        connector = _external_connector()
        connector._physics_feedback = False
        connector._refresh_measured_state = mock.MagicMock()

        connector.apply_pending_joints()

        connector._refresh_measured_state.assert_not_called()


ROBOT_PATH = "/World/SleepRobot"
LINK_0_PATH = f"{ROBOT_PATH}/link_0"


class TestIdleSleepTargets(omni.kit.test.AsyncTestCase):
    """Sleep targets must follow a rebuilt simulation view, and a target that
    is only temporarily gone must not disable idle-sleep for good.

    Runs against the context stage because that is what the encoding reads.
    """

    async def setUp(self):
        await omni.usd.get_context().new_stage_async()
        self.stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(self.stage, "/World")
        for path in (ROBOT_PATH, LINK_0_PATH):
            UsdPhysics.RigidBodyAPI.Apply(
                UsdGeom.Xform.Define(self.stage, path).GetPrim()
            )

    async def tearDown(self):
        await omni.usd.get_context().new_stage_async()

    def _connector(self, live_view: object) -> MotionStreamConnector:
        """Idle-sleep enabled, targets already resolved against another view."""
        connector = _bare_connector()
        connector.motion_group.identifier = ROBOT_PATH
        connector.motion_group.articulation.prim_path = ROBOT_PATH
        connector.motion_group.articulation._articulation_view._physics_view = live_view
        connector._sleep_unavailable = False
        connector._sleep_physics_view = object()
        # Live paths, but ids that no real encoding produces: that is what a
        # rebuilt simulation view has to replace.
        connector._sleep_paths = [ROBOT_PATH, LINK_0_PATH]
        connector._sleep_body_ids = [1234, 5678]
        return connector

    async def test_rebuilt_physics_view_re_resolves_targets(self):
        live_view = object()
        connector = self._connector(live_view)
        expected = [
            PhysicsSchemaTools.sdfPathToInt(ROBOT_PATH),
            PhysicsSchemaTools.sdfPathToInt(LINK_0_PATH),
        ]

        with mock.patch.object(msc, "get_physx_simulation_interface") as physx:
            connector._set_articulation_sleep(True)

        self.assertIs(live_view, connector._sleep_physics_view)
        self.assertEqual([ROBOT_PATH, LINK_0_PATH], connector._sleep_paths)
        self.assertEqual(expected, connector._sleep_body_ids)
        slept = [
            call.args[1] for call in physx.return_value.put_to_sleep.call_args_list
        ]
        self.assertEqual(expected, slept)
        self.assertNotIn(1234, slept)

    async def test_unchanged_physics_view_keeps_resolved_targets(self):
        live_view = object()
        connector = self._connector(live_view)
        connector._sleep_physics_view = live_view

        with mock.patch.object(msc, "get_physx_simulation_interface") as physx:
            connector._set_articulation_sleep(True)

        # No rebuild, so the cached encoding is reused untouched.
        self.assertEqual([1234, 5678], connector._sleep_body_ids)
        slept = [
            call.args[1] for call in physx.return_value.put_to_sleep.call_args_list
        ]
        self.assertEqual([1234, 5678], slept)

    async def test_disabled_body_does_not_disable_idle_sleep(self):
        live_view = object()
        connector = self._connector(live_view)
        self.stage.GetPrimAtPath(LINK_0_PATH).GetAttribute(
            "physics:rigidBodyEnabled"
        ).Set(False)
        self.assertIn(LINK_0_PATH, connector._sleep_paths)

        with mock.patch.object(msc, "get_physx_simulation_interface") as physx:
            connector._set_articulation_sleep(True)

        self.assertFalse(connector._sleep_unavailable)
        self.assertIsNone(connector._sleep_body_ids)
        physx.return_value.put_to_sleep.assert_not_called()

    async def test_interface_failure_logs_once_and_keeps_retrying(self):
        live_view = object()
        connector = self._connector(live_view)
        connector._sleep_physics_view = live_view

        with (
            mock.patch.object(msc, "get_physx_simulation_interface") as physx,
            mock.patch.object(msc.carb, "log_warn") as log_warn,
        ):
            physx.return_value.put_to_sleep.side_effect = RuntimeError("no such body")
            connector._set_articulation_sleep(True)
            connector._set_articulation_sleep(True)

        # Still retrying rather than latched off after the first failure. The
        # first pass raises on its first body, the second re-resolves and
        # raises again, and only the first one is logged.
        self.assertEqual(2, physx.return_value.put_to_sleep.call_count)
        self.assertEqual(1, log_warn.call_count)
        self.assertFalse(connector._sleep_unavailable)

    async def test_recovered_sleep_allows_the_next_failure_to_log(self):
        live_view = object()
        connector = self._connector(live_view)
        connector._sleep_physics_view = live_view

        with (
            mock.patch.object(msc, "get_physx_simulation_interface") as physx,
            mock.patch.object(msc.carb, "log_warn") as log_warn,
        ):
            physx.return_value.put_to_sleep.side_effect = [
                RuntimeError("no such body"),
                None,
                None,
                RuntimeError("no such body"),
            ]
            connector._set_articulation_sleep(True)
            connector._set_articulation_sleep(True)
            connector._set_articulation_sleep(True)

        # The middle pass worked, so the later failure is reported again.
        self.assertEqual(2, log_warn.call_count)

    async def test_missing_interface_disables_idle_sleep(self):
        live_view = object()
        connector = self._connector(live_view)
        connector._sleep_physics_view = live_view

        with mock.patch.object(msc, "get_physx_simulation_interface") as physx:
            physx.return_value.put_to_sleep.side_effect = AttributeError("gone")
            connector._set_articulation_sleep(True)

        # A changed interface shape never recovers, so this one does latch.
        self.assertTrue(connector._sleep_unavailable)


class TestWakeForReset(omni.kit.test.AsyncTestCase):
    """A STOP has to wake the articulation or its reset never reaches the stage.

    Idle-sleep suppresses PhysX's per-frame transform writeback, which is the
    same path reset-on-stop uses to publish the restored joint values to USD
    and Fabric alike.
    """

    async def setUp(self):
        await omni.usd.get_context().new_stage_async()
        self.stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(self.stage, "/World")
        for path in (ROBOT_PATH, LINK_0_PATH):
            UsdPhysics.RigidBodyAPI.Apply(
                UsdGeom.Xform.Define(self.stage, path).GetPrim()
            )

    async def tearDown(self):
        await omni.usd.get_context().new_stage_async()

    def _slept_connector(self) -> MotionStreamConnector:
        """Asleep, with targets already resolved against the live view."""
        connector = _bare_connector()
        live_view = object()
        connector.motion_group.identifier = ROBOT_PATH
        connector.motion_group.articulation.prim_path = ROBOT_PATH
        connector.motion_group.articulation._articulation_view._physics_view = live_view
        connector._sleep_unavailable = False
        connector._sleep_physics_view = live_view
        connector._sleep_paths = [ROBOT_PATH, LINK_0_PATH]
        connector._sleep_body_ids = [1234, 5678]
        connector._idle_frames = msc._IDLE_SLEEP_FRAMES
        return connector

    async def test_the_articulation_is_woken(self):
        connector = self._slept_connector()
        with mock.patch.object(msc, "get_physx_simulation_interface") as physx:
            connector.wake_for_reset()
        self.assertEqual(
            [1234, 5678], [call.args[1] for call in physx().wake_up.call_args_list]
        )
        physx().put_to_sleep.assert_not_called()

    async def test_the_idle_counter_is_cleared(self):
        """Left set, the connector believes it slept and the next target after
        play would skip its own wake."""
        connector = self._slept_connector()
        with mock.patch.object(msc, "get_physx_simulation_interface"):
            connector.wake_for_reset()
        self.assertEqual(0, connector._idle_frames)

    async def test_a_wake_that_did_not_happen_keeps_the_retry(self):
        """The counter is what makes the next target retry the wake, so a wake
        that issued nothing must not look like one that worked."""
        connector = self._slept_connector()
        # Targets no longer live: the documented playback-merge case.
        self.stage.GetPrimAtPath(LINK_0_PATH).GetAttribute(
            "physics:rigidBodyEnabled"
        ).Set(False)
        with mock.patch.object(msc, "get_physx_simulation_interface") as physx:
            connector.wake_for_reset()
        physx().wake_up.assert_not_called()
        self.assertEqual(msc._IDLE_SLEEP_FRAMES, connector._idle_frames)

    async def test_waking_does_not_need_a_playing_timeline(self):
        connector = self._slept_connector()
        connector.timeline.is_playing.return_value = False
        connector.timeline.is_stopped.return_value = True
        with mock.patch.object(msc, "get_physx_simulation_interface") as physx:
            connector.wake_for_reset()
        self.assertEqual(
            [1234, 5678], [call.args[1] for call in physx().wake_up.call_args_list]
        )

    async def test_an_unavailable_sleep_interface_is_not_an_error(self):
        connector = self._slept_connector()
        connector._sleep_unavailable = True
        with mock.patch.object(msc, "get_physx_simulation_interface") as physx:
            connector.wake_for_reset()
        physx.assert_not_called()
        # Same rule as the case above: nothing was issued, so the counter must
        # not read as a wake that worked.
        self.assertEqual(msc._IDLE_SLEEP_FRAMES, connector._idle_frames)


class TestOnTimelineStop(omni.kit.test.AsyncTestCase):
    """The STOP reaches every stream, and one bad stream must not stop it."""

    def _service(self, *streams):
        from wandelbots.omni.manipulators.motion_group_service import MotionGroupService

        service = MotionGroupService.__new__(MotionGroupService)
        service._streams = {f"/World/mg_{i}": s for i, s in enumerate(streams)}
        return service

    async def test_every_stream_is_woken(self):
        first, second = mock.MagicMock(), mock.MagicMock()
        self._service(first, second).on_timeline_stop()
        first.wake_for_reset.assert_called_once_with()
        second.wake_for_reset.assert_called_once_with()

    async def test_a_failing_stream_does_not_block_the_others(self):
        broken, healthy = mock.MagicMock(), mock.MagicMock()
        broken.wake_for_reset.side_effect = RuntimeError("no physics view")
        self._service(broken, healthy).on_timeline_stop()
        healthy.wake_for_reset.assert_called_once_with()

    async def test_no_streams_is_not_an_error(self):
        self._service().on_timeline_stop()
