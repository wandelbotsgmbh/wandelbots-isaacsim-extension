"""The ghost-teaching settings stay in step with carb, which the Preferences
page writes directly."""

import carb.settings
import omni.kit.test

from wandelbots.omni.ui.preferences import register_setting_defaults
from wandelbots.omni.ui.tool.animation_recorder.mdl_to_usd_preview import (
    CARB_DEBUG_DUMP,
)
from wandelbots.omni.ui.tool.ghost_teaching.ghost_teaching_tool_bar import (
    GhostTeachingToolBar,
)
from wandelbots.omni.ui.tool.ghost_teaching.widgets.ghost_teaching_settings_window import (
    CARB_MOTION_COMMAND,
    CARB_OVERLAY_VISIBLE,
    CARB_SELECT_GHOST_OBJECT_IN_SCENE,
    CARB_STAY_OPEN,
    SettingsModel,
    apply_ghost_teaching_carb_settings,
    load_ghost_teaching_carb_settings,
)

_TOUCHED_PATHS = (
    CARB_MOTION_COMMAND,
    CARB_OVERLAY_VISIBLE,
    CARB_SELECT_GHOST_OBJECT_IN_SCENE,
    CARB_STAY_OPEN,
)


class _RestoresCarbSettings(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._settings = carb.settings.get_settings()
        self._saved = {path: self._settings.get(path) for path in _TOUCHED_PATHS}

    async def tearDown(self):
        for path, value in self._saved.items():
            if value is None:
                self._settings.destroy_item(path)
            else:
                self._settings.set(path, value)


class TestApplyGhostTeachingCarbSettings(_RestoresCarbSettings):
    async def test_an_existing_model_takes_the_carb_value_and_reports_it(self):
        model = SettingsModel()
        changes = []
        model.property_changed_fn = lambda name, old, new: changes.append(name)
        self._settings.set(CARB_MOTION_COMMAND, "line")

        apply_ghost_teaching_carb_settings(model)

        self.assertEqual("line", model.motion_command)
        self.assertIn("motion_command", changes)

    async def test_stay_open_and_select_in_scene_read_their_own_keys(self):
        self._settings.set(CARB_STAY_OPEN, True)
        self._settings.set(CARB_SELECT_GHOST_OBJECT_IN_SCENE, False)

        model = load_ghost_teaching_carb_settings()

        self.assertTrue(model.stay_open)
        self.assertFalse(model.select_ghost_object_in_scene)


class TestGhostTeachingToolBarFollowsCarb(_RestoresCarbSettings):
    async def setUp(self):
        await super().setUp()
        self._settings.set(CARB_MOTION_COMMAND, "joint_p2p")
        self._toolbar = GhostTeachingToolBar()

    async def tearDown(self):
        if self._toolbar is not None:
            self._toolbar.destroy()
        self._toolbar = None
        await super().tearDown()

    async def test_a_carb_write_reaches_a_registered_toolbar(self):
        self._settings.set(CARB_MOTION_COMMAND, "cartesian_p2p")

        self.assertEqual("cartesian_p2p", self._toolbar.settings.motion_command)

    async def test_a_later_toolbar_save_keeps_the_carb_write(self):
        self._settings.set(CARB_MOTION_COMMAND, "line")

        overlay_visible = self._toolbar.settings.overlay_visible
        self._toolbar.settings.overlay_visible = not overlay_visible

        self.assertEqual("line", self._settings.get_as_string(CARB_MOTION_COMMAND))

    async def test_a_toolbar_change_still_reaches_carb(self):
        """A save fires one carb event per key it writes, changed or not. None
        of them may read the pending field back out of the model first."""
        self._toolbar.settings.motion_command = "cartesian_p2p"

        self.assertEqual(
            "cartesian_p2p", self._settings.get_as_string(CARB_MOTION_COMMAND)
        )

    async def test_a_destroyed_toolbar_stops_listening(self):
        self._toolbar.destroy()

        self._settings.set(CARB_MOTION_COMMAND, "line")

        self.assertEqual("joint_p2p", self._toolbar.settings.motion_command)


class TestRegisterSettingDefaults(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._settings = carb.settings.get_settings()
        self._saved_debug_dump = self._settings.get(CARB_DEBUG_DUMP)

    async def tearDown(self):
        if self._saved_debug_dump is None:
            self._settings.destroy_item(CARB_DEBUG_DUMP)
        else:
            self._settings.set(CARB_DEBUG_DUMP, self._saved_debug_dump)

    async def test_the_mdl_debug_dump_switch_exists_on_a_fresh_install(self):
        self._settings.destroy_item(CARB_DEBUG_DUMP)

        register_setting_defaults()

        self.assertIs(False, self._settings.get(CARB_DEBUG_DUMP))

    async def test_a_value_already_set_is_kept(self):
        self._settings.set(CARB_DEBUG_DUMP, True)

        register_setting_defaults()

        self.assertIs(True, self._settings.get(CARB_DEBUG_DUMP))
