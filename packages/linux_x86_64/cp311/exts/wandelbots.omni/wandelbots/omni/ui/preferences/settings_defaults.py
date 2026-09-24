"""Writing the extension's setting defaults into carb at startup.

A setting that no tool has saved yet does not exist in carb, and the
preferences page can only list what is there - so on a fresh install most of
the extension's settings would be missing from it until the user had opened
each tool once.

``set_default_*`` writes a value only where there is none, so this changes
nothing for anyone who has already configured something. The values are
imported from the code that owns them rather than repeated here; a second copy
would drift the moment one of them changed.
"""

from __future__ import annotations

import carb.settings

from wandelbots.omni.ui.colors import float_array_to_hex


def register_setting_defaults() -> None:
    """Materialize every setting the preferences page should be able to show."""
    settings = carb.settings.get_settings()
    _register_ghost_teaching(settings)
    _register_overlay_colors(settings)
    _register_diagnostics(settings)


def _register_ghost_teaching(settings: carb.settings.ISettings) -> None:
    from wandelbots.omni.ui.tool.ghost_teaching.widgets.ghost_teaching_settings_window import (
        CARB_MAX_JOINT_CONFIGS,
        CARB_MOTION_COMMAND,
        CARB_OPEN_WITH_GHOST_OBJECT,
        CARB_OVERLAY_COLOR,
        CARB_OVERLAY_VISIBLE,
        CARB_SELECT_GHOST_OBJECT_IN_SCENE,
        CARB_STAY_OPEN,
        SettingsModel,
    )

    defaults = SettingsModel()
    settings.set_default_bool(CARB_STAY_OPEN, defaults.stay_open)
    settings.set_default_bool(
        CARB_OPEN_WITH_GHOST_OBJECT, defaults.open_with_ghost_object
    )
    settings.set_default_bool(
        CARB_SELECT_GHOST_OBJECT_IN_SCENE, defaults.select_ghost_object_in_scene
    )
    settings.set_default_string(CARB_MOTION_COMMAND, defaults.motion_command)
    settings.set_default_bool(CARB_OVERLAY_VISIBLE, defaults.overlay_visible)
    settings.set_default_string(CARB_OVERLAY_COLOR, defaults.overlay_color)
    settings.set_default_int(CARB_MAX_JOINT_CONFIGS, defaults.max_joint_configs)


def _register_overlay_colors(settings: carb.settings.ISettings) -> None:
    from wandelbots.omni.ui.overlay.collision_world.utils import (
        CARB_OVERLAY_COLOR as COLLISION_OVERLAY_COLOR,
        DEFAULT_OVERLAY_COLOR as COLLISION_DEFAULT,
    )
    from wandelbots.omni.ui.tool.mounting_assistant.mounting_assistant_window import (
        CARB_MOUNTING_OVERLAY_COLOR,
        DEFAULT_OVERLAY_COLOR as MOUNTING_DEFAULT,
    )
    from wandelbots.omni.ui.tool.reachability.reachability_window import (
        CARB_REACHABILITY_PREVIEW_COLOR,
        DEFAULT_PREVIEW_COLOR as REACHABILITY_DEFAULT,
    )

    settings.set_default_string(COLLISION_OVERLAY_COLOR, COLLISION_DEFAULT)
    # These two keep a float array in code and a hex string in carb, so the
    # default has to be written the way their own save path writes it.
    settings.set_default_string(
        CARB_MOUNTING_OVERLAY_COLOR, float_array_to_hex(list(MOUNTING_DEFAULT))
    )
    settings.set_default_string(
        CARB_REACHABILITY_PREVIEW_COLOR, float_array_to_hex(list(REACHABILITY_DEFAULT))
    )


def _register_diagnostics(settings: carb.settings.ISettings) -> None:
    from wandelbots.omni.ui.tool.animation_recorder.mdl_to_usd_preview import (
        CARB_DEBUG_DUMP,
    )

    settings.set_default_bool(CARB_DEBUG_DUMP, False)
