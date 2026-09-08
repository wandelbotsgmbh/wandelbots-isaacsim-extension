"""Unit tests for ReachabilityService's per-pose collision setup grouping."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import omni.kit.test
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.reachability.reachability_service import (
    ReachabilityService,
    ReachabilitySession,
    TargetPose,
    attached_tool_colliders,
)

# A tetrahedron in meters, the smallest hull that spans a volume.
_TOOL_HULL = [(0.0, 0.0, 0.0), (0.1, 0.0, 0.0), (0.0, 0.1, 0.0), (0.0, 0.0, 0.1)]


def _make_ik_response(joints: list[list[list[float]]]) -> MagicMock:
    response = MagicMock()
    response.joints = joints
    return response


def _marker_collider(radius: float) -> wb_v2_models.Collider:
    """A collider told apart by its radius, so a sent request can be traced
    back to the collider source it was built from."""
    return wb_v2_models.Collider(
        shape=wb_v2_models.ColliderShape(
            wb_v2_models.Sphere(radius=radius, shape_type="sphere")
        ),
        pose=wb_v2_models.Pose(position=[0.0, 0.0, 0.0], orientation=[0.0, 0.0, 0.0]),
    )


def _stored_setup(colliders: dict, tool: dict) -> MagicMock:
    setup = MagicMock()
    setup.colliders = colliders
    setup.tool = tool
    return setup


class TestCheckSingleModelCollisionGrouping(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self.service = ReachabilityService()

    def _make_session(
        self,
        collision_setup_names: list[str | None],
        static_colliders: dict | None = None,
        stored_collision_setups: dict | None = None,
        link_chain: list | None = None,
    ) -> ReachabilitySession:
        kinematics_api = MagicMock()
        kinematics_api.inverse_kinematics = AsyncMock()
        models_api = MagicMock()
        models_api.get_motion_group_collision_model = AsyncMock(
            return_value=link_chain if link_chain is not None else []
        )
        return ReachabilitySession(
            api_client=MagicMock(),
            kinematics_api=kinematics_api,
            models_api=models_api,
            cell_id="cell-1",
            target_poses=[
                TargetPose(
                    pose=WSPose(pose=[float(index), 0.0, 0.0, 0.0, 0.0, 0.0]),
                    collision_setup_name=name,
                )
                for index, name in enumerate(collision_setup_names)
            ],
            nova_mounting_pose=None,
            nova_tcp_offset=None,
            static_colliders=static_colliders,
            joint_position_limits=[],  # skip the mechanical-limits transcript fetch
            stored_collision_setups=stored_collision_setups,
        )

    @staticmethod
    def _sent_requests(
        session: ReachabilitySession,
    ) -> list[wb_v2_models.InverseKinematicsRequest]:
        """The IK requests the session actually sent, in order."""
        return [
            call.kwargs["inverse_kinematics_request"]
            for call in session.kinematics_api.inverse_kinematics.call_args_list
        ]

    async def test_groups_poses_by_setup_and_scatters_results_in_order(self):
        # Poses 0 and 2 use the session's static colliders (the source for a
        # pose without a chosen setup); pose 1 uses a NOVA-stored setup. That
        # is exactly two IK calls, one per collider source, with the results
        # scattered back into the original pose order.
        link_chain = [{"link_0": _marker_collider(1.0)}]
        static_colliders = {"floor": _marker_collider(2.0)}
        stored_colliders = {"workpiece": _marker_collider(3.0)}
        session = self._make_session(
            [None, "upper_setup", None],
            static_colliders=static_colliders,
            stored_collision_setups={
                "upper_setup": _stored_setup(
                    stored_colliders, {"gripper": _marker_collider(4.0)}
                )
            },
            link_chain=link_chain,
        )
        session.kinematics_api.inverse_kinematics.side_effect = [
            _make_ik_response([[[1.0, 2.0]], [[3.0, 4.0]]]),  # poses 0 and 2
            _make_ik_response([[]]),  # pose 1, unreachable under the stored setup
        ]

        result = await self.service.check_single_model(session, "FANUC_TEST")

        default_request, stored_request = self._sent_requests(session)
        self.assertEqual(len(default_request.tcp_poses), 2)
        self.assertEqual(len(stored_request.tcp_poses), 1)

        # Each group is checked against its own collider source, never the
        # other group's, and both against the model's own link chain.
        default_setup = default_request.collision_setups["FANUC_TEST"]
        stored_request_setup = stored_request.collision_setups["FANUC_TEST"]
        self.assertEqual(default_setup.colliders, static_colliders)
        self.assertEqual(stored_request_setup.colliders, stored_colliders)
        self.assertEqual(default_setup.link_chain, link_chain)
        self.assertEqual(stored_request_setup.link_chain, link_chain)
        # A stored setup's tool colliders are deliberately ignored - tool
        # geometry comes only from an attached tool, and this session has
        # none, so neither group carries tool colliders.
        self.assertIsNone(default_setup.tool)
        self.assertIsNone(stored_request_setup.tool)

        self.assertEqual(result.joint_solutions[0], [1.0, 2.0])
        self.assertEqual(result.joint_solutions[1], [])
        self.assertEqual(result.joint_solutions[2], [3.0, 4.0])
        self.assertEqual(result.reachable_count, 2)
        self.assertFalse(result.reachable)
        self.assertIsNone(result.error)

    async def test_attached_tool_colliders_apply_to_every_group(self):
        # The attached tool's colliders ride the flange for every pose group,
        # the one with a stored setup and the one without, while the stored
        # setup's own tool colliders stay ignored.
        session = self._make_session(
            [None, "upper_setup", None],
            static_colliders=None,
            stored_collision_setups={
                "upper_setup": _stored_setup(
                    {"workpiece": _marker_collider(3.0)},
                    {"gripper": _marker_collider(4.0)},
                )
            },
            link_chain=[{"link_0": _marker_collider(1.0)}],
        )
        attached_tool = attached_tool_colliders([_TOOL_HULL])
        session.tool_colliders = attached_tool
        session.kinematics_api.inverse_kinematics.side_effect = [
            _make_ik_response([[[1.0]], [[2.0]]]),  # poses 0 and 2
            _make_ik_response([[[3.0]]]),  # pose 1
        ]

        await self.service.check_single_model(session, "FANUC_TEST")

        requests = self._sent_requests(session)
        self.assertEqual(len(requests), 2)
        for request in requests:
            # Even the group without static colliders gets a collision setup,
            # carrying the attached tool.
            self.assertEqual(request.collision_setups["FANUC_TEST"].tool, attached_tool)

    async def test_collision_setup_enables_self_collision(self):
        # Reachability sends self_collision_detection=True so robot-vs-robot
        # and tool-vs-robot collisions are rejected. The tool false positive
        # the flag would cause (geometry nesting over the flange counts as a
        # permanent self-collision) is prevented by the flange-plane clamp in
        # attached_tool_colliders instead.
        session = self._make_session(
            [None], link_chain=[{"link_0": _marker_collider(1.0)}]
        )
        session.tool_colliders = attached_tool_colliders([_TOOL_HULL])
        session.kinematics_api.inverse_kinematics.side_effect = [
            _make_ik_response([[[1.0]]]),
        ]

        await self.service.check_single_model(session, "FANUC_TEST")

        setup = self._sent_requests(session)[0].collision_setups["FANUC_TEST"]
        self.assertTrue(setup.self_collision_detection)

    async def test_no_setups_issue_a_single_ik_call(self):
        # Without per-pose setups every pose shares one collider source, so
        # the whole run is a single IK call.
        session = self._make_session(
            [None, None, None],
            static_colliders={"floor": _marker_collider(2.0)},
            link_chain=[{"link_0": _marker_collider(1.0)}],
        )
        session.kinematics_api.inverse_kinematics.side_effect = [
            _make_ik_response([[[1.0]], [[2.0]], [[3.0]]]),
        ]

        result = await self.service.check_single_model(session, "FANUC_TEST")

        self.assertEqual(len(self._sent_requests(session)), 1)
        self.assertEqual(result.reachable_count, 3)
        self.assertTrue(result.reachable)

    async def test_unresolved_setup_gets_no_collision_awareness_not_default(self):
        # A pose whose setup name failed to resolve (absent from
        # stored_collision_setups) must not silently fall back to the default
        # colliders - it is checked with no collision setup at all.
        session = self._make_session(
            ["missing_setup"],
            static_colliders={"floor": _marker_collider(2.0)},
            stored_collision_setups={},
            link_chain=[{"link_0": _marker_collider(1.0)}],
        )
        session.kinematics_api.inverse_kinematics.side_effect = [
            _make_ik_response([[[1.0]]]),
        ]

        await self.service.check_single_model(session, "FANUC_TEST")

        self.assertIsNone(self._sent_requests(session)[0].collision_setups)

    async def test_attached_tool_hulls_become_flange_frame_colliders(self):
        # Hull points arrive in meters relative to the tool root; the
        # colliders must be millimeter convex hulls at the flange (identity
        # pose). A degenerate hull (< 4 points) is skipped.
        colliders = attached_tool_colliders([_TOOL_HULL, [(0.0, 0.0, 0.0)]])

        self.assertEqual(list(colliders), ["attached_tool/0"])
        collider = colliders["attached_tool/0"]
        shape = collider.shape.actual_instance
        self.assertEqual(shape.shape_type, "convex_hull")
        self._assert_vertex(shape.vertices[1], [100.0, 0.0, 0.0])
        self._assert_vertex(list(collider.pose.position), [0.0, 0.0, 0.0])

    async def test_no_attached_tool_yields_no_colliders(self):
        self.assertIsNone(attached_tool_colliders(None))
        self.assertIsNone(attached_tool_colliders([]))
        self.assertIsNone(attached_tool_colliders([[(0.0, 0.0, 0.0)]]))

    async def test_sub_flange_geometry_is_clamped_onto_the_plane(self):
        # A hull reaching behind the flange plane (into the wrist) gets those
        # vertices projected onto it; geometry above the plane passes through
        # untouched. A hull entirely behind the plane is dropped, since
        # flattened it would be a zero-volume plate.
        adapter = [
            (0.0, 0.0, -0.005),
            (0.1, 0.0, -0.005),
            (0.0, 0.1, 0.02),
            (0.0, 0.0, 0.02),
        ]
        behind_the_flange = [
            (0.0, 0.0, -0.01),
            (0.1, 0.0, -0.01),
            (0.0, 0.1, -0.02),
            (0.0, 0.0, -0.03),
        ]
        colliders = attached_tool_colliders([adapter, behind_the_flange])

        self.assertEqual(list(colliders), ["attached_tool/0"])
        vertices = colliders["attached_tool/0"].shape.actual_instance.vertices
        self._assert_vertex(vertices[0], [0.0, 0.0, 0.0])
        self._assert_vertex(vertices[1], [100.0, 0.0, 0.0])
        self._assert_vertex(vertices[2], [0.0, 100.0, 20.0])
        self._assert_vertex(vertices[3], [0.0, 0.0, 20.0])

    def _assert_vertex(self, actual: list[float], expected: list[float]) -> None:
        self.assertEqual(len(actual), len(expected))
        for actual_value, expected_value in zip(actual, expected):
            self.assertAlmostEqual(actual_value, expected_value, places=6)
