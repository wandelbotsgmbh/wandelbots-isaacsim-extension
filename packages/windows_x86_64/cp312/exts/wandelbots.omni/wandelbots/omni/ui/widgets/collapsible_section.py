import omni.ui as ui
from typing import Callable, Optional
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.wb_theme import CORNER_RADIUS, INDENT_STEP
from wandelbots.omni.ui.utils import get_icon, is_ui_busy

_HEADER_HEIGHT = 30
# Top/bottom inset so the header content (label, leading widget, icon buttons)
# never touches the card's rounded borders. The target visual margin is 8px to
# the *pure text*, but a font-14 label box is ~3px taller than its glyphs
# (line-height padding), so an 8px spacer to the box would read as ~11px to the
# text. 5px to the box therefore lands the visible text at ~8px, while still
# clearing the 20px icon buttons (5 + 20 + 5 = 30). The content row itself is
# height-0 (content-driven) so a word-wrapped multi-line title grows the header.
_HEADER_VERTICAL_PADDING = 5

# Triangle geometry as (alignment, width, height) for each collapsed state.
_TRIANGLE_COLLAPSED = (ui.Alignment.RIGHT_CENTER, 6, 9)
_TRIANGLE_EXPANDED = (ui.Alignment.CENTER_BOTTOM, 9, 6)


class CollapsibleSection(ui.VStack):
    """Collapsible section whose header can host interactive widgets.

    Only the triangle + label area toggles the section.  Widgets added
    via *build_header_fn* sit outside the toggle area so they receive
    their own mouse events without side-effects.
    """

    # omni.ui reports a Rectangle as ``hovered`` for the WHOLE of its bounds, so
    # while the cursor is over a nested child header, every ancestor header also
    # reports hovered. We therefore can't trust a single "active" pointer: we
    # keep the full set of sections currently reporting a raw hover and, on every
    # change, recompute which single header should be lit (the innermost one).
    # Recomputing from live state means a highlight can never get stranded by
    # out-of-order or dropped enter/leave events.
    _raw_hovered: set["CollapsibleSection"] = set()
    # The one section whose header is lit right now (at most one at a time).
    _highlighted: Optional["CollapsibleSection"] = None

    def __init__(
        self,
        title: str,
        collapsed: bool = True,
        build_header_fn: Optional[Callable[["CollapsibleSection"], None]] = None,
        build_leading_fn: Optional[Callable[["CollapsibleSection"], None]] = None,
        on_collapsed_changed: Optional[Callable[[bool], None]] = None,
        header_color: NOVAColor = NOVAColor.SURFACE_OVERLAY,
        header_hover_color: NOVAColor = NOVAColor.SURFACE_OVERLAY_HOVER,
        title_color: Optional[NOVAColor] = None,
        title_icon: Optional[str] = None,
        margin: int = 1,
        content_padding: int = 1,
        **kwargs,
    ):
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._title = title
        self._collapsed = collapsed
        self._body: Optional[ui.VStack] = None
        self._body_container: Optional[ui.VStack] = None
        self._section_bg: Optional[ui.Rectangle] = None
        self._header_bg: Optional[ui.Rectangle] = None
        self._build_header_fn = build_header_fn
        self._build_leading_fn = build_leading_fn
        self._on_collapsed_changed = on_collapsed_changed
        self._header_color = header_color
        self._header_hover_color = header_hover_color
        self._title_color = title_color
        self._title_icon = title_icon
        self._content_padding = content_padding
        self._triangle: Optional[ui.Triangle] = None
        self._title_label: Optional[ui.Label] = None

        # Outer gap between this card and its siblings / parent edges.
        # NOTE: applied via Spacers in ``_build`` (see below), NOT via
        # ``self.set_style({"margin": ...})``. An unselectored margin style on
        # this VStack cascades into EVERY descendant (including a nested switch's
        # track/toggle rectangles) and mis-positions them.
        self._margin = margin

        self._build(collapsed)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    @collapsed.setter
    def collapsed(self, value: bool):
        if self._collapsed == value:
            return
        self._collapsed = value
        self._update_triangle()
        if self._body_container:
            self._body_container.visible = not value
        if self._on_collapsed_changed:
            self._on_collapsed_changed(value)

    @property
    def body(self) -> ui.VStack:
        return self._body

    def set_header_mouse_pressed_fn(self, fn: Callable) -> None:
        """Attach an extra mouse-pressed handler to the header background.

        Useful for e.g. right-click context menus on the header. Does not
        interfere with the left-click toggle on the triangle/label.
        """
        if self._header_bg:
            self._header_bg.set_mouse_pressed_fn(fn)

    @property
    def title(self) -> str:
        return self._title

    @title.setter
    def title(self, value: str):
        self._title = value
        if self._title_label:
            self._title_label.text = value

    def _build(self, collapsed: bool):
        with self:
            margin = self._margin
            if margin:
                ui.Spacer(height=margin)
            with ui.HStack(spacing=0):
                if margin:
                    ui.Spacer(width=margin)
                # Full-section card: the background Rectangle spans the whole
                # ZStack (header + body) to give the section its translucent 8%
                # white surface. A separate header-only overlay (below) handles
                # the hover highlight so only the header brightens on hover.
                with ui.ZStack():
                    self._section_bg = ui.Rectangle(
                        style={
                            "background_color": self._header_color.color,
                            "border_radius": CORNER_RADIUS,
                        },
                    )
                    with ui.VStack(spacing=0):
                        # Header-only hover overlay. Transparent by default so the
                        # section background shows through; it paints the hover
                        # color only while the cursor is over the header row, and
                        # its ZStack hugs the 34px header so hovering the body
                        # never lights it up.
                        with ui.ZStack(height=0):
                            self._header_bg = ui.Rectangle(
                                style={
                                    "background_color": NOVAColor.SURFACE_TRANSPARENT.color,
                                    "border_radius": CORNER_RADIUS,
                                },
                            )
                            self._header_bg.set_mouse_hovered_fn(
                                lambda hovered, _self=self: _self._on_header_hover(
                                    hovered
                                )
                            )

                            with ui.VStack(height=0):
                                # Inset the content row vertically so the label and
                                # any header icon buttons keep a margin from the
                                # card's top/bottom borders.
                                ui.Spacer(height=_HEADER_VERTICAL_PADDING)
                                # Height-0 (content-driven) so a long, word-wrapped
                                # title can grow the header to two+ lines instead of
                                # being clipped. The 20px icon buttons keep the floor
                                # at the original 30px header for single-line titles.
                                with ui.HStack(height=0, spacing=0):
                                    ui.Spacer(width=10)
                                    with ui.VStack(width=10):
                                        ui.Spacer()
                                        self._triangle = self._make_triangle(collapsed)
                                        self._triangle.set_mouse_pressed_fn(
                                            lambda x, y, btn, _, _self=self: (
                                                _self._toggle() if btn == 0 else None
                                            )
                                        )
                                        ui.Spacer()
                                    ui.Spacer(width=6)
                                    if self._build_leading_fn:
                                        with ui.HStack(width=0, spacing=6):
                                            self._build_leading_fn(self)
                                        ui.Spacer(width=6)
                                    self._title_label = ui.Label(
                                        self._title,
                                        width=0 if self._title_icon else ui.Fraction(1),
                                        word_wrap=not self._title_icon,
                                        style_type_name_override="CollapsableFrame.Header",
                                        style=(
                                            {"color": self._title_color.color}
                                            if self._title_color
                                            else {}
                                        ),
                                        mouse_pressed_fn=lambda x, y, btn, _, _self=self: (
                                            _self._toggle() if btn == 0 else None
                                        ),
                                    )
                                    if self._title_icon:
                                        ui.Spacer(width=6)
                                        with ui.VStack(width=16):
                                            ui.Spacer()
                                            ui.Image(
                                                get_icon(self._title_icon),
                                                width=16,
                                                height=16,
                                                style=(
                                                    {"color": self._title_color.color}
                                                    if self._title_color
                                                    else {}
                                                ),
                                            )
                                            ui.Spacer()
                                        ui.Spacer()
                                    if self._build_header_fn:
                                        with ui.HStack(width=0, spacing=2):
                                            self._build_header_fn(self)
                                    ui.Spacer(width=6)
                                ui.Spacer(height=_HEADER_VERTICAL_PADDING)

                        # Body padding uses explicit Spacers, NOT a cascading
                        # ``style={"margin": ...}``. An unselectored margin style
                        # on the body would cascade into EVERY descendant widget
                        # (including nested switch track/toggle rectangles) and
                        # mis-position them. Spacers inset the content without
                        # leaking style to children.
                        self._body_container = ui.VStack(
                            visible=not collapsed,
                            spacing=0,
                        )
                        with self._body_container:
                            pad = self._content_padding
                            # Indent the body one INDENT_STEP past the header so a
                            # nested section's card sits inset from its parent,
                            # making the hierarchy depth read visually. The inset
                            # accumulates naturally per nesting level.
                            left_inset = pad + INDENT_STEP
                            if pad:
                                ui.Spacer(height=pad)
                                with ui.HStack(spacing=0):
                                    ui.Spacer(width=left_inset)
                                    self._body = ui.VStack(spacing=0)
                                    ui.Spacer(width=pad)
                                ui.Spacer(height=pad)
                            else:
                                with ui.HStack(spacing=0):
                                    ui.Spacer(width=INDENT_STEP)
                                    self._body = ui.VStack(spacing=0)
                if margin:
                    ui.Spacer(width=margin)
            if margin:
                ui.Spacer(height=margin)

    def _toggle(self):
        # Ignore header toggles while a blocking operation runs. The toggle is
        # driven by a custom mouse handler, which omni.ui's ``enabled`` does not
        # gate, so consult the global busy gate here.
        if is_ui_busy():
            return
        self.collapsed = not self._collapsed

    def _on_header_hover(self, hovered: bool):
        if not self._header_bg:
            return
        cls = CollapsibleSection
        if hovered:
            cls._raw_hovered.add(self)
        else:
            cls._raw_hovered.discard(self)
        cls._refresh_highlight()

    @classmethod
    def clear_hover_state(cls):
        """Forget all tracked hovers and clear the current highlight.

        A modal dialog (e.g. a file picker) opened from a header button steals
        input, so the header's ``hovered=False`` leave event is never delivered
        and the section would otherwise stay stranded in ``_raw_hovered`` and
        keep (or steal) the highlight. Callers opening such a modal should reset
        the tracking; the next pointer move re-establishes the correct hover.
        """
        cls._raw_hovered.clear()
        if cls._highlighted is not None:
            cls._highlighted._set_header_highlight(False)
            cls._highlighted = None

    @classmethod
    def _refresh_highlight(cls):
        # Among all sections currently reporting a raw hover, the one the cursor
        # is really over is the innermost = the narrowest header (nested cards
        # are inset per level, so a child header is always strictly narrower than
        # its ancestors'; unrelated siblings never overlap, so at most one chain
        # is hovered at a time). Recomputing the winner from scratch guarantees
        # exactly one lit header that always matches the live pointer position.
        winner: Optional["CollapsibleSection"] = None
        best_width: Optional[float] = None
        for section in list(cls._raw_hovered):
            bg = section._header_bg
            width = bg.computed_width if bg is not None else 0
            if width <= 0:
                # A detached/rebuilt section that never reported its leave; drop
                # it so a stale entry can't suppress or steal the highlight.
                cls._raw_hovered.discard(section)
                continue
            if best_width is None or width < best_width:
                best_width = width
                winner = section
        if winner is cls._highlighted:
            return
        if cls._highlighted is not None:
            cls._highlighted._set_header_highlight(False)
        cls._highlighted = winner
        if winner is not None:
            winner._set_header_highlight(True)

    def _set_header_highlight(self, highlighted: bool):
        if not self._header_bg:
            return
        # The header overlay sits on top of the full-section background, so its
        # non-hover state is transparent (background shows through) and it only
        # paints the hover color while highlighted.
        color = (
            self._header_hover_color.color
            if highlighted
            else NOVAColor.SURFACE_TRANSPARENT.color
        )
        try:
            self._header_bg.style = {
                "background_color": color,
                "border_radius": CORNER_RADIUS,
            }
        except Exception:
            # The underlying rectangle was destroyed (section rebuilt) before its
            # highlight could be reset; nothing to paint.
            pass

    @staticmethod
    def _make_triangle(collapsed: bool) -> ui.Triangle:
        alignment, width, height = (
            _TRIANGLE_COLLAPSED if collapsed else _TRIANGLE_EXPANDED
        )
        return ui.Triangle(
            width=width,
            height=height,
            alignment=alignment,
            name="collapsible_triangle",
            style={
                "background_color": NOVAColor.COLLAPSIBLE_SECTION_HEADER_ICON.color,
                "color": NOVAColor.COLLAPSIBLE_SECTION_HEADER_ICON.color,
            },
        )

    def _update_triangle(self):
        if not self._triangle:
            return
        alignment, width, height = (
            _TRIANGLE_COLLAPSED if self._collapsed else _TRIANGLE_EXPANDED
        )
        self._triangle.alignment = alignment
        self._triangle.width = ui.Length(width)
        self._triangle.height = ui.Length(height)
