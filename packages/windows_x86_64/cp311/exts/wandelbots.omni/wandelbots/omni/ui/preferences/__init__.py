"""Preferences - the extension's carb settings as a page in Kit's own window."""

from wandelbots.omni.ui.preferences.settings_catalog import (
    BOOL,
    COLOR,
    FOREIGN_GROUP,
    FOREIGN_SETTINGS,
    NUMBER,
    READ_ONLY,
    SETTING_PREFIX,
    STRING,
    TEXT,
    ForeignSetting,
    Setting,
    classify_setting,
    collect_foreign_settings,
    collect_settings,
    duplicate_keys,
    flatten_settings,
    group_by_extension,
    group_title,
    is_hex_color,
    is_secret,
    readable_words,
    split_extension_path,
)
from wandelbots.omni.ui.preferences.preferences_page import WandelbotsPreferencesPage
from wandelbots.omni.ui.preferences.registration import register_preferences_page
from wandelbots.omni.ui.preferences.settings_defaults import (
    register_setting_defaults,
)

__all__ = [
    "BOOL",
    "COLOR",
    "FOREIGN_GROUP",
    "FOREIGN_SETTINGS",
    "NUMBER",
    "READ_ONLY",
    "SETTING_PREFIX",
    "STRING",
    "TEXT",
    "ForeignSetting",
    "Setting",
    "WandelbotsPreferencesPage",
    "classify_setting",
    "collect_foreign_settings",
    "collect_settings",
    "duplicate_keys",
    "flatten_settings",
    "group_by_extension",
    "group_title",
    "is_hex_color",
    "is_secret",
    "readable_words",
    "register_preferences_page",
    "register_setting_defaults",
    "split_extension_path",
]
