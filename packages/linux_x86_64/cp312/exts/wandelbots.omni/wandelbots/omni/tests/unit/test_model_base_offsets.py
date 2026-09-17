import math

import omni.kit.test

from wandelbots.omni.manipulators.model_base_offsets import MODEL_BASE_OFFSETS
from wandelbots.omni.reachability import reachability_service
from wandelbots.omni.ui.create_context_menu.cell import cell_preview
from wandelbots.omni.ui.create_context_menu.robot import robot_download, robot_preview
from wandelbots.omni.ui.tool.reachability import (
    reachability_preview,
    reachability_window,
)

CONSUMERS = (
    reachability_service,
    reachability_preview,
    reachability_window,
    robot_download,
    robot_preview,
    cell_preview,
)


class TestModelBaseOffsets(omni.kit.test.AsyncTestCase):
    async def test_base_frame_at_joint_1_lifts_the_plate(self):
        self.assertAlmostEqual(0.245, MODEL_BASE_OFFSETS["FANUC_CRX10iA"], places=4)
        self.assertAlmostEqual(0.65, MODEL_BASE_OFFSETS["Yaskawa_GP225"], places=4)

    async def test_base_frame_on_the_plate_needs_no_lift(self):
        self.assertAlmostEqual(0.0, MODEL_BASE_OFFSETS["KUKA_KR500_L340_3"], places=4)
        self.assertAlmostEqual(0.0, MODEL_BASE_OFFSETS["STAUBLI_tx2_40"], places=4)

    async def test_hull_noise_below_the_threshold_is_zero(self):
        self.assertAlmostEqual(0.0, MODEL_BASE_OFFSETS["Techman_TM30S"], places=4)
        self.assertAlmostEqual(0.0, MODEL_BASE_OFFSETS["KUKA_KR270_R2700"], places=4)

    async def test_values_are_plate_heights_in_metres(self):
        for model, value in MODEL_BASE_OFFSETS.items():
            self.assertTrue(math.isfinite(value), model)
            self.assertGreaterEqual(value, 0.0, model)
            self.assertLessEqual(value, 1.5, model)

    async def test_consumers_share_one_table(self):
        for consumer in CONSUMERS:
            self.assertIs(
                MODEL_BASE_OFFSETS, consumer.MODEL_BASE_OFFSETS, consumer.__name__
            )
