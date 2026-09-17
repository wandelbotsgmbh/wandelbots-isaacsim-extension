"""Which articulation dof each of a motion group's joints is written to.

``apply_action`` pairs ``joint_positions[k]`` with ``joint_indices[k]``. The
positions come from the motion group in its own joint order, so the indices have
to follow that same order. Reading them off the articulation's dof order instead
happens to agree only while the motion group is the whole articulation - the
moment several groups share one, that order interleaves them.
"""

import omni.kit.test

from wandelbots.omni.manipulators.utils import (
    joint_indices_in_motion_group_order,
    joint_positions_in_motion_group_order,
)


class TestJointIndicesInMotionGroupOrder(omni.kit.test.AsyncTestCase):
    async def test_a_group_that_is_the_whole_articulation(self):
        dof_paths = ["/Arm/J1", "/Arm/J2", "/Arm/J3"]

        indices = joint_indices_in_motion_group_order(dof_paths, dof_paths)

        self.assertEqual([0, 1, 2], indices)

    async def test_a_group_is_a_subset_of_a_shared_articulation(self):
        dof_paths = ["/Lift/G1", "/Lift/G2", "/Arm/J1", "/Arm/J2", "/Head/H1"]

        indices = joint_indices_in_motion_group_order(dof_paths, ["/Arm/J1", "/Arm/J2"])

        self.assertEqual([2, 3], indices)

    async def test_the_motion_group_order_wins_over_the_dof_order(self):
        """The case that misdrives a robot: same joints, different order."""
        dof_paths = ["/Arm/J2", "/Arm/J1"]

        indices = joint_indices_in_motion_group_order(dof_paths, ["/Arm/J1", "/Arm/J2"])

        self.assertEqual([1, 0], indices)

    async def test_joints_without_a_degree_of_freedom_are_skipped(self):
        """root_joint and the link_0 weld are fixed, so they have no dof."""
        dof_paths = ["/Arm/J1", "/Arm/J2"]

        indices = joint_indices_in_motion_group_order(
            dof_paths, ["/Arm/root_joint", "/Arm/J1", "/Arm/link_0_joint", "/Arm/J2"]
        )

        self.assertEqual([0, 1], indices)

    async def test_a_group_with_no_joints_maps_to_nothing(self):
        self.assertEqual([], joint_indices_in_motion_group_order(["/Arm/J1"], []))

    async def test_an_articulation_without_dofs_maps_to_nothing(self):
        self.assertEqual([], joint_indices_in_motion_group_order([], ["/Arm/J1"]))


class TestJointPositionsInMotionGroupOrder(omni.kit.test.AsyncTestCase):
    """What a motion group reads back out of a shared articulation.

    Ghost teaching and the mounting assistant rank IK solutions against this
    vector, so it has to carry this group's joints and no others.
    """

    async def test_a_group_reads_only_its_own_joints(self):
        positions = [0.1, 0.2, 0.3, 0.4, 0.5]

        selected = joint_positions_in_motion_group_order(positions, [2, 3])

        self.assertEqual([0.3, 0.4], selected)

    async def test_the_group_order_wins_over_the_dof_order(self):
        positions = [0.1, 0.2]

        selected = joint_positions_in_motion_group_order(positions, [1, 0])

        self.assertEqual([0.2, 0.1], selected)

    async def test_without_a_mapping_the_whole_articulation_is_returned(self):
        """No robot joints authored: narrowing to nothing would hand callers
        an empty pose, so the pre-shared-articulation behaviour stands."""
        positions = [0.1, 0.2]

        self.assertEqual(
            positions, joint_positions_in_motion_group_order(positions, [])
        )

    async def test_an_index_past_the_articulation_is_dropped(self):
        """A stale mapping must not raise inside the pose read."""
        self.assertEqual([0.1], joint_positions_in_motion_group_order([0.1], [0, 7]))
