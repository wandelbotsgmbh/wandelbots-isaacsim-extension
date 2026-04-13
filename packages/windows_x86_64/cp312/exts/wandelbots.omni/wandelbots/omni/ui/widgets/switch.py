import weakref
import omni.ui as ui
from wandelbots.omni.ui.colors import NOVAColor
import omni.kit.app
from omni.kit.async_engine import run_coroutine

DEFAULT_SWITCH_STYLE = {
    "Switch::switch_base": {
        "background_color": NOVAColor.BACKGROUND_ELEVATION_2.color,
    },
    "Switch::switch_selected": {
        "background_color": NOVAColor.PRIMARY_LIGHT.color,
    },
    "Switch::switch_toggle": {
        "background_color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
    },
    "Switch::switch_base_hover": {
        "background_color": NOVAColor.ACTION_HOVER.color,
    },
}


WARNING_SWITCH_STYLE = {
    "Switch::switch_base": {
        "background_color": NOVAColor.BACKGROUND_ELEVATION_2.color,
    },
    "Switch::switch_selected": {
        "background_color": NOVAColor.WARNING_MAIN.color,
    },
    "Switch::switch_toggle": {
        "background_color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
    },
    "Switch::switch_base_hover": {
        "background_color": NOVAColor.ACTION_HOVER.color,
    },
}


class Switch:
    def __init__(
        self,
        height: int = 30,
        model: ui.SimpleBoolModel = None,
        style: dict = None,
        tooltip: str = None,
    ):
        self._tooltip = tooltip
        self._model: ui.SimpleBoolModel = model or ui.SimpleBoolModel(False)
        self._height = height
        self.container = ui.ZStack(
            height=ui.Pixel(height),
            style=style or DEFAULT_SWITCH_STYLE,
            tooltip=self._tooltip,
        )
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
        self._model.set_value(not self._model.get_value_as_bool())
        self._refresh_switch()

    def _refresh_switch(self):
        self._toggle_placer.offset_x = (
            self.container.height if self.model.get_value_as_bool() else 0
        ) + self._toggle_inset / 2
        self._switch_selected.visible = self.model.get_value_as_bool()
        self._switch_base.visible = not self.model.get_value_as_bool()

    def _mouse_hover(self, over: bool):
        # self._switch_base.visible = not over
        self._switch_base_hover.visible = over

    def rebuild(self):
        self._deferred_build_ui()

    @property
    def model(self):
        return self._model
