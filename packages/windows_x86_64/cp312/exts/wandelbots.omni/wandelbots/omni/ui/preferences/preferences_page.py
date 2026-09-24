"""A "Wandelbots NOVA" page in Isaac Sim's own Preferences window.

Kit already owns a preferences window with a page list, so the extension's
settings belong in it rather than in a window of their own. Kit's
``create_setting_widget`` binds a widget straight to a carb path and routes the
write through ``ChangeSettingCommand``, so those rows also undo and redo; the
rows this module builds by hand are the ones Kit has no widget for.
"""

from __future__ import annotations

from typing import Optional

import carb
import carb.settings
import omni.kit.app
import omni.ui as ui
from omni.kit.window.preferences import PreferenceBuilder, SettingType

from wandelbots.omni.constants import EXTENSION_WINDOW_MENU_ROOT
from wandelbots.omni.ui.colors import float_array_to_hex, hex_to_float_array
from wandelbots.omni.ui.preferences.settings_registry import Constraint, constraint_for
from wandelbots.omni.ui.preferences.settings_catalog import (
    BOOL,
    COLOR,
    NUMBER,
    STRING,
    TEXT,
    Setting,
    collect_foreign_settings,
    collect_settings,
    duplicate_keys,
    group_by_extension,
    group_title,
)

PAGE_TITLE = EXTENSION_WINDOW_MENU_ROOT

# What create_setting_widget lays a row out with: 24 for most types, but 20
# plus a 3 px trailing spacer for the FLOAT/INT/STRING widgets. A hand-built
# row has to pick the same pair or it sits off the generated ones.
_ROW_HEIGHT = 24
_FIELD_ROW_HEIGHT = 20
_FIELD_ROW_PADDING = 3
_TEXT_FIELD_HEIGHT = 72
# Square, like every other colour swatch in the extension.
_COLOR_SIDE = 20
_HEADLINE = (
    "Settings the Wandelbots NOVA extension stores, and the Isaac Sim settings "
    "its workflows depend on. Every change is written immediately and applies "
    "the next time the setting is read."
)


class WandelbotsPreferencesPage(PreferenceBuilder):
    """The extension's carb settings, one frame per extension."""

    def __init__(self):
        super().__init__(PAGE_TITLE)
        self._settings = carb.settings.get_settings()

    def build(self):
        settings = collect_settings(self._settings.get)
        for key in duplicate_keys(settings):
            carb.log_warn(
                f"The setting {key} exists in both the volatile and the "
                "persistent tree of the same extension. The page shows one row "
                "per key, so two rows with this label write to different places."
            )
        with ui.VStack(height=0):
            # No style of its own: the page inherits the preferences window's
            # theme, like every other page in the list.
            ui.Label(_HEADLINE, word_wrap=True, height=0)
            self.spacer()
            foreign = collect_foreign_settings(self._settings.get)
            if not settings and not foreign:
                ui.Label("No settings are registered.", height=_ROW_HEIGHT)
                return
            # The extension's own groups first, the foreign ones after them in
            # the order they are declared.
            groups = sorted(group_by_extension(settings).items())
            groups += group_by_extension(foreign).items()
            for extension_name, group in groups:
                with self.add_frame(
                    group_title(extension_name, _extension_title(extension_name))
                ):
                    with ui.VStack():
                        for setting in group:
                            self._build_row(setting)

    def _build_row(self, setting: Setting):
        constraint = constraint_for(setting.path)
        if constraint is not None and self._build_constrained_row(setting, constraint):
            return
        native_type = _native_setting_type(setting)
        if native_type is not None:
            # Kit's own widget: same look as every other page, and the write
            # goes through ChangeSettingCommand so it lands in the undo stack.
            self.create_setting_widget(
                setting.label, setting.path, native_type, tooltip=setting.tooltip
            )
            return
        height, padding = _row_metrics(setting)
        with ui.HStack(height=height):
            self.label(setting.label, tooltip=setting.tooltip)
            self._build_editor(setting)
        if padding:
            ui.Spacer(height=padding)

    def _build_constrained_row(self, setting: Setting, constraint: Constraint) -> bool:
        """Render a declared setting with an editor that cannot leave its range.

        False when the declaration does not fit the value after all - a choice
        list against a number, say - so the caller falls back rather than
        rendering a control that writes the wrong type.
        """
        if constraint.choices and setting.kind == STRING:
            self.create_setting_widget_combo(
                setting.label,
                setting.path,
                list(constraint.choices),
                tooltip=setting.tooltip,
            )
            return True
        if (
            constraint.minimum is not None
            and constraint.maximum is not None
            and setting.kind == NUMBER
        ):
            self.create_setting_widget(
                setting.label,
                setting.path,
                _native_setting_type(setting),
                range_from=constraint.minimum,
                range_to=constraint.maximum,
                tooltip=setting.tooltip,
            )
            return True
        carb.log_warn(
            f"{setting.path} declares a constraint that does not fit its "
            f"{setting.kind} value; falling back to the plain editor."
        )
        return False

    def _build_editor(self, setting: Setting):
        if setting.kind == COLOR:
            self._build_color(setting)
            return
        if setting.kind == TEXT:
            self._build_multiline(setting)
            return
        # A list of strings, or anything carb handed back that has no obvious
        # editor. Writing a guessed type into carb is worse than not offering
        # the edit.
        ui.Label(str(setting.value), elided_text=True)

    def _build_color(self, setting: Setting):
        """A colour picker that writes back in the format it read.

        Most colours here are hex strings (see float_array_to_hex), but
        classify_setting also calls a float array a colour, and those exist
        too. Writing an array over a string leaves every reader that calls
        get_as_string with an empty value and its default colour - and reading
        a list as if it were a hex string raises, which took the whole page
        down with it.
        """
        stored_as_hex = isinstance(setting.value, str)
        components = (
            hex_to_float_array(setting.value)
            if stored_as_hex
            else [float(part) for part in setting.value]
        )
        with ui.HStack():
            with ui.VStack(width=_COLOR_SIDE):
                widget = ui.ColorWidget(
                    *components, width=_COLOR_SIDE, height=_COLOR_SIDE
                )
            ui.Spacer()

        def _write(_item, path=setting.path, model=widget.model, as_hex=stored_as_hex):
            rgba = [child.get_value_as_float() for child in model.get_item_children()]
            if as_hex:
                self._settings.set_string(path, float_array_to_hex(rgba))
            else:
                self._settings.set(path, rgba)

        widget.model.add_end_edit_fn(_write)

    def _build_multiline(self, setting: Setting):
        field = ui.StringField(multiline=True, height=_TEXT_FIELD_HEIGHT)
        field.model.set_value(str(setting.value))
        field.model.add_end_edit_fn(
            lambda model, path=setting.path: self._settings.set_string(
                path, model.get_value_as_string()
            )
        )


def _row_metrics(setting: Setting) -> tuple[int, int]:
    """(row height, trailing padding), matching create_setting_widget.

    A text field is a STRING widget grown taller, so it keeps the padding that
    goes with one; a colour swatch is laid out like Kit's own COLOR3 row.
    """
    if setting.kind == TEXT:
        return _TEXT_FIELD_HEIGHT, _FIELD_ROW_PADDING
    if setting.kind == COLOR:
        return _ROW_HEIGHT, 0
    return _FIELD_ROW_HEIGHT, _FIELD_ROW_PADDING


def _native_setting_type(setting: Setting) -> Optional[SettingType]:
    """The Kit widget type for this setting, or None to build the row by hand.

    Colours are hex strings here and multi-line text has no Kit widget, so both
    fall through. So does anything with no editor at all.
    """
    if setting.kind == BOOL:
        return SettingType.BOOL
    if setting.kind == NUMBER:
        return SettingType.INT if isinstance(setting.value, int) else SettingType.FLOAT
    if setting.kind == STRING:
        return SettingType.STRING
    return None


def _extension_title(extension_name: str) -> Optional[str]:
    """The readable title Kit knows for an extension, or None.

    None whenever the extension is not loaded or carries no title - then the
    header falls back to the bare name, which the setting path shows anyway.
    """
    if not extension_name:
        return None
    try:
        manager = omni.kit.app.get_app().get_extension_manager()
        extension_id = manager.get_enabled_extension_id(extension_name)
        if not extension_id:
            return None
        return manager.get_extension_dict(extension_id).get("package/title")
    except (AttributeError, KeyError, RuntimeError, TypeError):
        return None
