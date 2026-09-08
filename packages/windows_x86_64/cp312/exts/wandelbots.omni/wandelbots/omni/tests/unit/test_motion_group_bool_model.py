from contextlib import contextmanager

import omni.kit.test
from pxr import Sdf, Usd

from wandelbots.omni.tests.stage_utils import use_stage
from wandelbots.omni.ui.instances.models.motion_group_enabled_model import (
    MotionGroupEnabledModel,
)


class TestMotionGroupBoolModelPathGuard(omni.kit.test.AsyncTestCase):
    @contextmanager
    def use_test_stage(self):
        stage: Usd.Stage = Usd.Stage.CreateInMemory("TestMotionGroupBoolModel")
        self.assertIsNotNone(stage)
        with use_stage(stage):
            yield stage

    async def test_ill_formed_path_never_subscribes_and_reads_off(self):
        # The stored configuration may carry a motion group id where a prim
        # path is expected; such strings are not valid Sdf paths.
        ill_formed_path = "0123-motion-group-id"
        self.assertFalse(bool(Sdf.Path.IsValidPathString(ill_formed_path)))

        with self.use_test_stage():
            model = MotionGroupEnabledModel(ill_formed_path)

            self.assertFalse(model.is_backed)
            self.assertIsNone(model._change_subscription)
            self.assertFalse(model.get_value_as_bool())

    async def test_ill_formed_path_write_snaps_back(self):
        with self.use_test_stage():
            model = MotionGroupEnabledModel("motion group 1")

            # A user (or a still-enabled widget) flips the switch: the write
            # is a no-op, so the model must snap back instead of rendering a
            # state that never reached USD.
            model.set_value(True)

            self.assertFalse(model.get_value_as_bool())

    async def test_valid_path_without_prim_write_snaps_back(self):
        with self.use_test_stage():
            model = MotionGroupEnabledModel("/World/does_not_exist")

            self.assertTrue(model.is_backed)
            model.set_value(True)  # must neither raise nor stick

            self.assertFalse(model.get_value_as_bool())

    async def test_valid_path_without_prim_reads_off_without_raising(self):
        # A deleted motion group prim: HasAPI on the invalid prim raises
        # "Accessed invalid null prim" unless the model guards the lookup.
        with self.use_test_stage():
            model = MotionGroupEnabledModel("/World/deleted_motion_group")

            self.assertIsNone(model.motion_group_configuration)
            self.assertFalse(model._get_prim_value())
