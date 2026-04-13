import omni.ui as ui
from typing import Callable, Optional
from wandelbots.omni.ui.colors import NOVAColor


class CollapsibleSection(ui.VStack):
    """Collapsible section whose header can host interactive widgets.

    Only the triangle + label area toggles the section.  Widgets added
    via *build_header_fn* sit outside the toggle area so they receive
    their own mouse events without side-effects.
    """

    def __init__(
        self,
        title: str,
        collapsed: bool = True,
        build_header_fn: Optional[Callable[["CollapsibleSection"], None]] = None,
        on_collapsed_changed: Optional[Callable[[bool], None]] = None,
        **kwargs,
    ):
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._title = title
        self._collapsed = collapsed
        self._body: Optional[ui.VStack] = None
        self._header_bg: Optional[ui.Rectangle] = None
        self._build_header_fn = build_header_fn
        self._on_collapsed_changed = on_collapsed_changed
        self._triangle: Optional[ui.Triangle] = None

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
        if self._body:
            self._body.visible = not value
        if self._on_collapsed_changed:
            self._on_collapsed_changed(value)

    @property
    def body(self) -> ui.VStack:
        return self._body

    def _build(self, collapsed: bool):
        with self:
            with ui.ZStack(height=28):
                self._header_bg = ui.Rectangle(
                    style={
                        "background_color": NOVAColor.BACKGROUND_DEFAULT.color,
                        "border_radius": 2,
                    },
                )
                self._header_bg.set_mouse_hovered_fn(
                    lambda hovered, _self=self: _self._on_header_hover(hovered)
                )

                with ui.HStack(spacing=0):
                    ui.Spacer(width=10)
                    with ui.VStack(width=0):
                        ui.Spacer()
                        self._triangle = self._make_triangle(collapsed)
                        self._triangle.set_mouse_pressed_fn(
                            lambda x, y, btn, _, _self=self: (
                                _self._toggle() if btn == 0 else None
                            )
                        )
                        ui.Spacer()
                    ui.Spacer(width=8)
                    ui.Label(
                        self._title,
                        style_type_name_override="CollapsableFrame.Header",
                        mouse_pressed_fn=lambda x, y, btn, _, _self=self: (
                            _self._toggle() if btn == 0 else None
                        ),
                    )
                    if self._build_header_fn:
                        with ui.HStack(width=0, spacing=6):
                            self._build_header_fn(self)
                    ui.Spacer(width=10)

            self._body = ui.VStack(
                visible=not collapsed,
                style={
                    "background_color": NOVAColor.BACKGROUND_PAPER.color,
                    "border_radius": 2,
                },
            )

    def _toggle(self):
        self.collapsed = not self._collapsed

    def _on_header_hover(self, hovered: bool):
        if not self._header_bg:
            return
        color = (
            NOVAColor.ACTION_HOVER.color
            if hovered
            else NOVAColor.BACKGROUND_DEFAULT.color
        )
        self._header_bg.style = {"background_color": color, "border_radius": 2}

    @staticmethod
    def _make_triangle(collapsed: bool) -> ui.Triangle:
        if collapsed:
            alignment = ui.Alignment.RIGHT_CENTER
            width, height = 5, 7
        else:
            alignment = ui.Alignment.CENTER_BOTTOM
            width, height = 7, 5
        return ui.Triangle(
            style_type_name_override="CollapsableFrame.Header",
            width=width,
            height=height,
            alignment=alignment,
        )

    def _update_triangle(self):
        if not self._triangle:
            return
        if self._collapsed:
            self._triangle.alignment = ui.Alignment.RIGHT_CENTER
            self._triangle.width = ui.Length(5)
            self._triangle.height = ui.Length(7)
        else:
            self._triangle.alignment = ui.Alignment.CENTER_BOTTOM
            self._triangle.width = ui.Length(7)
            self._triangle.height = ui.Length(5)
