import omni.ui as ui

from wandelbots.omni.ui.wb_theme import (
    CHECKBOX_CHECKED_STYLE,
    CHECKBOX_UNCHECKED_STYLE,
)


def styled_checkbox(
    model: ui.AbstractValueModel | None = None, **kwargs
) -> ui.CheckBox:
    """Build a CheckBox with the NOVA purple-when-checked styling.

    omni.ui's CheckBox does not switch its background by checked state on its
    own, so the style is swapped between the checked and unchecked variants on
    every value change.
    """
    if model is not None:
        kwargs["model"] = model
    checkbox = ui.CheckBox(**kwargs)

    def _apply_style(value_model: ui.AbstractValueModel) -> None:
        checkbox.set_style(
            CHECKBOX_CHECKED_STYLE
            if value_model.get_value_as_bool()
            else CHECKBOX_UNCHECKED_STYLE
        )

    _apply_style(checkbox.model)
    checkbox.model.add_value_changed_fn(_apply_style)
    return checkbox
