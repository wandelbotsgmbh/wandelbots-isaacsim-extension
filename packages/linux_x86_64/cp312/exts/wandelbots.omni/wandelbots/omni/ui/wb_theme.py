"""Global design system for Wandelbots NOVA UIs.

Single source of truth for every shared widget style. Each widget type has an
explicit style dict here; modules import the one(s) they need and apply them
directly (``ui.Button("Save", style=BUTTON_PRIMARY_STYLE)``).

Explicit per-widget styles are used instead of a window-level cascade because
omni.ui's style cascade does not reliably propagate through intermediate frames
(e.g. a CollapsibleSection body), so nested widgets would silently fall back to
defaults. Applying the style on the widget itself is deterministic at any depth.
This also lets ``ui.ComboBox`` be themed the same way as every other widget --
its closed field body only paints from a flat per-widget style anyway.
"""

from wandelbots.omni.ui.colors import NOVAColor

import omni.ui as ui

# Corner radius for every rectangular surface (frames, fields, buttons, combos).
CORNER_RADIUS = 4

# Uniform height for every themed text button. Buttons are content-width
# (``width=0``) so they hug their label and never stretch with the window.
BUTTON_HEIGHT = 30

# Horizontal indentation applied per nesting level (e.g. a CollapsibleSection
# nested inside another). Used to inset child content from its parent's left
# edge so the hierarchy reads visually.
INDENT_STEP = 12

# Typography scale. Use these named sizes instead of raw ``font_size`` ints so
# text sizing stays consistent across the UI. ``FONT_SIZE_MD`` is the default
# body size.
FONT_SIZE_SM = 13
FONT_SIZE_MD = 14
FONT_SIZE_LG = 16
FONT_SIZE_XL = 18

# Spacing scale (pixels). Use for Spacer sizes and stack spacing to keep a
# consistent rhythm instead of scattering raw magic numbers.
SPACING_XS = 2
SPACING_SM = 4
SPACING_MD = 8
SPACING_LG = 12
SPACING_XL = 16

# Section layout. Vertical rhythm and horizontal insets for a stack of
# CollapsibleSection cards. SECTION_NEST_INSET aligns a top-level card's left
# edge with content nested two levels deep; SECTION_EDGE_INSET right-aligns it
# with that nested card's border. The divider gaps are the visible space a
# section_divider() leaves above/below its line.
SECTION_GAP = SPACING_MD
SECTION_NEST_INSET = 2 * INDENT_STEP
SECTION_EDGE_INSET = 2
DIVIDER_INSET = SPACING_XL
# A more-inset (shorter) hairline for dividers WITHIN a card — e.g. between the
# cloud and custom instance groups inside "Added Instances" — so it reads as a
# lighter, subordinate separation than the full-width section dividers.
DIVIDER_INSET_NARROW = 2 * SPACING_XL
DIVIDER_GAP_ABOVE = SPACING_MD
DIVIDER_GAP_BELOW = SPACING_MD

# Compact single-field forms (Add Instance host, Sign In environment). A shared
# label column width keeps their inputs left-aligned to the same x; the header
# gap is the space below a section title before its first form row.
FORM_LABEL_WIDTH = 120
FORM_HEADER_GAP = SPACING_MD

# Shared fixed width for the combo boxes / input fields in those forms, so the
# Instance Type, Environment and Host inputs all render the same width. The label
# column stretches to fill the rest, pushing the field to a right margin that
# matches the form's 15px left inset.
FORM_FIELD_WIDTH = 250
# Left/right inset for the form rows; the label starts this far from the left and
# the field ends this far from the right, giving equal margins on both sides.
FORM_SIDE_MARGIN = 15

# Tooltip popup styling, scoped through the "Tooltip" type selector so it
# themes only the popup and never the host widget's body.
#
# Merging this into a widget's own style does not restyle its default
# string tooltip: plain tooltip="..." always renders the app-default popup.
# Use tooltip_fn=lambda: build_tooltip("...") together with
# **TOOLTIP_RESET in the widget style instead.
TOOLTIP_STYLE = {
    "Tooltip": {
        "background_color": "#141414",
        "color": NOVAColor.TEXT_SECONDARY.color,
        "border_color": NOVAColor.TOOLTIP_BORDER.color,
        "border_width": 1,
        "border_radius": CORNER_RADIUS,
        "margin": 4,
    },
}

# Tooltip reset for widgets that supply their own popup via ``tooltip_fn`` /
# ``build_tooltip``. omni.ui still wraps the custom content in a Tooltip
# container; without this it inherits a border and margin. Make that wrapper
# invisible so only the self-drawn ``build_tooltip`` content is seen.
TOOLTIP_RESET = {
    "Tooltip": {
        "background_color": NOVAColor.SURFACE_TRANSPARENT.color,
        "border_width": 0,
        "margin": 0,
        "padding": 0,
    },
}


# Tooltips longer than the threshold wrap at the fixed width.
_TOOLTIP_WRAP_WIDTH = 420
_TOOLTIP_WRAP_THRESHOLD = 60


def build_tooltip(text: str) -> None:
    """Draw a fully self-contained themed tooltip popup.

    A ComboBox/Field paints from flat style keys (``background_color``,
    ``color``); in this build those flat keys bleed into the widget's default
    string tooltip popup and override the scoped ``"Tooltip"`` selector, so the
    popup shows the dark field color instead of the warm theme. Building the
    tooltip content ourselves -- an explicit themed Rectangle behind a padded
    Label -- makes the popup independent of the host's flat style, so nothing
    leaks through. Pass via ``tooltip_fn=lambda: build_tooltip("...")``.

    Long texts wrap at a fixed width; a single line (a Nucleus URL, say)
    would otherwise run across the whole screen.
    """
    longest_line = max((len(line) for line in text.splitlines()), default=0)
    wrap = longest_line > _TOOLTIP_WRAP_THRESHOLD
    with ui.ZStack(width=_TOOLTIP_WRAP_WIDTH if wrap else ui.Fraction(1)):
        ui.Rectangle(
            style={
                "background_color": TOOLTIP_STYLE["Tooltip"]["background_color"],
                "border_radius": CORNER_RADIUS,
            }
        )
        with ui.HStack(height=0):
            ui.Spacer(width=4)
            with ui.VStack(height=0):
                ui.Spacer(height=2)
                ui.Label(
                    text,
                    word_wrap=wrap,
                    style={"color": TOOLTIP_STYLE["Tooltip"]["color"]},
                )
                ui.Spacer(height=2)
            ui.Spacer(width=4)


# Buttons are styled through the "Button" type selector (not flat keys) so the
# widget's own ``color``/``background_color`` cannot leak into its default
# NVIDIA tooltip popup, matching the Reachability UI button styles. Fields and
# combos keep flat keys because their closed bodies only paint from a flat
# per-widget style in this build.

# Text / string fields. Shares the dropdown body color so fields and combos match.
FIELD_STYLE = {
    "background_color": NOVAColor.LAYER_DROPDOWN_BODY.color,
    "color": NOVAColor.TEXT_PRIMARY.color,
    "border_radius": CORNER_RADIUS,
    **TOOLTIP_STYLE,
}

# A disabled button must not keep its enabled face - it would invite clicks
# that do nothing. Both selectors are needed: the frame paints from "Button",
# the caption from "Button.Label" (see the note above).
BUTTON_DISABLED_STYLE = {
    "Button:disabled": {"background_color": NOVAColor.DIVIDER.color},
    "Button.Label:disabled": {"color": NOVAColor.TEXT_DISABLED.color},
}

# Default (secondary) button. The caption text is painted by the "Button.Label"
# selector (Isaac Sim's global default style sets it via "Button.Label", so a
# "color" under "Button" only colors the frame and is overridden for the label).
# The scoped "Tooltip" selector keeps the tooltip popup's own color.
BUTTON_STYLE = {
    "Button": {
        "background_color": NOVAColor.SURFACE_OVERLAY.color,
        "border_radius": CORNER_RADIUS,
        "padding": 6,
    },
    "Button.Label": {"color": NOVAColor.TEXT_PRIMARY_CONTRAST.color},
    "Button:hovered": {"background_color": NOVAColor.SURFACE_OVERLAY_HOVER.color},
    **BUTTON_DISABLED_STYLE,
    **TOOLTIP_STYLE,
}

# Primary call-to-action button.
BUTTON_PRIMARY_STYLE = {
    "Button": {
        "background_color": NOVAColor.PRIMARY_MAIN.color,
        "border_radius": CORNER_RADIUS,
        "padding": 6,
    },
    "Button.Label": {"color": NOVAColor.TEXT_PRIMARY_CONTRAST.color},
    "Button:hovered": {"background_color": NOVAColor.PRIMARY_DARK.color},
    **BUTTON_DISABLED_STYLE,
    **TOOLTIP_STYLE,
}

# Borderless icon button (transparent face, brightens on hover) with a themed
# tooltip. Used for compact icon-only actions.
ICON_BTN_STYLE = {
    "Button": {"background_color": NOVAColor.SURFACE_TRANSPARENT.color},
    "Button:hovered": {"background_color": NOVAColor.BUTTON_HOVER.color},
    **TOOLTIP_STYLE,
}

# ComboBox. Flat keys (omni.ui paints the closed field body only from a flat
# per-widget style): background_color = field body, secondary_color = arrow,
# selected_color = selected popup row.
COMBOBOX_STYLE = {
    "font_size": FONT_SIZE_MD,
    "color": NOVAColor.TEXT_PRIMARY.color,
    "background_color": NOVAColor.LAYER_DROPDOWN_BODY.color,
    "secondary_color": NOVAColor.LAYER_DROPDOWN_WIDGET.color,
    "selected_color": NOVAColor.PRIMARY_SELECTED.color,
    "border_radius": CORNER_RADIUS,
    **TOOLTIP_RESET,
}

# Elevated "paper" surface rectangle.
PAPER_STYLE = {
    "background_color": NOVAColor.BACKGROUND_PAPER.color,
    "border_radius": CORNER_RADIUS,
}

# Main window header title label.
HEADER_LABEL_STYLE = {
    "Label": {
        "font_size": FONT_SIZE_MD,
        "color": NOVAColor.TEXT_PRIMARY_CONTRAST.color,
    },
}

# Toggle switch. The Switch widget paints four named sub-rectangles; the default
# uses the primary accent, the warning variant swaps the "on" color to amber.
SWITCH_STYLE = {
    "Switch::switch_base": {"background_color": NOVAColor.BACKGROUND_ELEVATION_2.color},
    "Switch::switch_selected": {"background_color": NOVAColor.PRIMARY_LIGHT.color},
    "Switch::switch_toggle": {
        "background_color": NOVAColor.PRIMARY_CONTRAST_TEXT.color
    },
    "Switch::switch_base_hover": {"background_color": NOVAColor.ACTION_HOVER.color},
}
SWITCH_WARNING_STYLE = {
    "Switch::switch_base": {"background_color": NOVAColor.BACKGROUND_ELEVATION_2.color},
    "Switch::switch_selected": {"background_color": NOVAColor.WARNING_MAIN.color},
    "Switch::switch_toggle": {
        "background_color": NOVAColor.PRIMARY_CONTRAST_TEXT.color
    },
    "Switch::switch_base_hover": {"background_color": NOVAColor.ACTION_HOVER.color},
}
# Non-interactive variant: greyed out, no hover feedback (hover rectangle uses
# the same color as the base so it reads as inert).
SWITCH_DISABLED_STYLE = {
    "Switch::switch_base": {
        "background_color": NOVAColor.ACTION_DISABLED_BACKGROUND.color
    },
    "Switch::switch_selected": {
        "background_color": NOVAColor.ACTION_DISABLED_BACKGROUND.color
    },
    "Switch::switch_toggle": {"background_color": NOVAColor.ACTION_DISABLED.color},
    "Switch::switch_base_hover": {
        "background_color": NOVAColor.ACTION_DISABLED_BACKGROUND.color
    },
}

# Checkbox. Purple fill with a dark check when on (matching the toggle switch's
# "on" color), a flat 12%-white box when off. omni.ui's CheckBox does not switch
# its background by checked state on its own, so styled_checkbox() swaps these
# two dicts on value change.
CHECKBOX_CHECKED_STYLE = {
    "background_color": NOVAColor.PRIMARY_LIGHT.color,
    "color": NOVAColor.LAYER_BASE.color,
    "border_radius": CORNER_RADIUS,
}
CHECKBOX_UNCHECKED_STYLE = {
    "background_color": NOVAColor.OVERLAY_LIGHT.color,
    "color": NOVAColor.LAYER_BASE.color,
    "border_radius": CORNER_RADIUS,
}

# Thin determinate progress bar. The small radius fully rounds the 4px-tall bar.
PROGRESS_BAR_STYLE = {
    "color": NOVAColor.PRIMARY_MAIN.color,
    "background_color": NOVAColor.PROGRESS_BAR_BACKGROUND.color,
    "secondary_color": NOVAColor.PROGRESS_BAR_BACKGROUND.color,
    "border_radius": 2,
    "font_size": 1,
}

# Hairline separator / divider line.
LINE_STYLE = {"color": NOVAColor.SURFACE_OVERLAY.color}
