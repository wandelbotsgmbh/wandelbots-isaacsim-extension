import weakref
import traceback
import carb
import omni.ui as ui
from wandelbots.omni.ui.wb_theme import (
    SWITCH_DISABLED_STYLE,
    SWITCH_STYLE,
    SWITCH_WARNING_STYLE,
    TOOLTIP_RESET,
    build_tooltip,
)
import omni.kit.app
from omni.kit.async_engine import run_coroutine

# Back-compat aliases: these styles now live in the design system (wb_theme).
DEFAULT_SWITCH_STYLE = SWITCH_STYLE
WARNING_SWITCH_STYLE = SWITCH_WARNING_STYLE


class Switch:
    def __init__(
        self,
        height: int = 30,
        model: ui.SimpleBoolModel = None,
        style: dict = None,
        tooltip: str = None,
        enabled: bool = True,
    ):
        self._tooltip = tooltip
        self._model: ui.SimpleBoolModel = model or ui.SimpleBoolModel(False)
        self._height = height
        self._enabled = enabled
        # A disabled switch must also LOOK inert: models whose write path is a
        # no-op (e.g. an ill-formed prim path) would otherwise render a live
        # control whose state never reaches USD.
        base_style = (
            SWITCH_DISABLED_STYLE if not enabled else (style or DEFAULT_SWITCH_STYLE)
        )
        container_kwargs = {"height": ui.Pixel(height)}
        if self._tooltip:
            # Self-draw the tooltip via build_tooltip; TOOLTIP_RESET hides the
            # native popup wrapper so only the themed content shows.
            container_kwargs["style"] = {**base_style, **TOOLTIP_RESET}
            container_kwargs["tooltip_fn"] = lambda t=self._tooltip: build_tooltip(t)
        else:
            container_kwargs["style"] = base_style
        self.container = ui.ZStack(**container_kwargs)
        self._switch_base: ui.Rectangle = None
        self._switch_selected: ui.Rectangle = None
        self._switch_base_hover: ui.Rectangle = None
        self._switch_toggle: ui.Rectangle = None
        self._toggle_placer: ui.Placer = None
        self._click_catcher: ui.Rectangle = None

        self._toggle_inset = 4

        self._model.add_value_changed_fn(
            lambda model, weak_self=weakref.ref(self): weak_self().rebuild()
        )
        self._build_ui()

    def _deferred_build_ui(self):
        async def wait_one_frame_and_build():
            await omni.kit.app.get_app().next_update_async()
            self._build_ui()

        run_coroutine(wait_one_frame_and_build())

    def _build_ui(self):
        try:
            self._build_ui_inner()
        except Exception as e:
            carb.log_error(f"[Switch] _build_ui failed: {e}\n{traceback.format_exc()}")

    def _build_ui_inner(self):
        self.container.clear()
        with self.container:
            border_radius = self._height / 2
            style = self.container.style.get("Switch::switch_base", {})
            style["border_radius"] = border_radius
            self._switch_base = ui.Rectangle(
                name="switch_base",
                height=ui.Fraction(1),
                width=self._height * 2,
                visible=not self.model.get_value_as_bool(),
                style=style,
            )
            style = self.container.style.get("Switch::switch_selected", {})
            style["border_radius"] = border_radius
            self._switch_selected = ui.Rectangle(
                name="switch_selected",
                height=ui.Fraction(1),
                width=self._height * 2,
                visible=self.model.get_value_as_bool(),
                style=style,
            )

            style = self.container.style.get("Switch::switch_base_hover", {})
            style["border_radius"] = border_radius
            self._switch_base_hover = ui.Rectangle(
                name="switch_base_hover",
                height=ui.Fraction(1),
                width=self._height * 2,
                visible=False,
                style=style,
            )

            self._toggle_placer = ui.Placer()
            self._toggle_placer.offset_y = self._toggle_inset / 2
            with self._toggle_placer:
                style = self.container.style.get("Switch::switch_toggle", {})
                style["border_radius"] = border_radius
                self._switch_toggle = ui.Rectangle(
                    name="switch_toggle",
                    height=self._height - self._toggle_inset,
                    width=self._height - self._toggle_inset,
                    style=style,
                )

            ui.Rectangle(
                name="invisible_click_catcher",
                height=ui.Fraction(1),
                width=self._height * 2,
                style={
                    "background_color": ui.color(0, 0, 0, 0),
                },
                mouse_pressed_fn=lambda *_: self._toggle_switch(),
                mouse_hovered_fn=lambda over: self._mouse_hover(over),
            )
        self._refresh_switch()

    def _toggle_switch(self):
        if not self._enabled:
            return
        self._model.set_value(not self._model.get_value_as_bool())
        self._refresh_switch()

    def _refresh_switch(self):
        self._toggle_placer.offset_x = (
            self._height if self.model.get_value_as_bool() else 0
        ) + self._toggle_inset / 2
        self._switch_selected.visible = self.model.get_value_as_bool()
        self._switch_base.visible = not self.model.get_value_as_bool()

    def _mouse_hover(self, over: bool):
        # self._switch_base.visible = not over
        self._switch_base_hover.visible = over and self._enabled

    def rebuild(self):
        self._deferred_build_ui()

    @property
    def model(self):
        return self._model
