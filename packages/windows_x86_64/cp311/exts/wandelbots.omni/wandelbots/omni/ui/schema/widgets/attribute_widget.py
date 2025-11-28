import re
from sys import maxsize
from typing import Callable
from pxr import Usd
from omni.kit.property.usd.usd_property_widget import (
    UsdPropertyUiEntry,
    SchemaPropertiesWidget,
)
from omni.kit.property.usd.widgets import ICON_PATH
from pathlib import Path
import omni.ui as ui
from omni.physx.scripts import utils
from omni.kit.property.usd.prim_selection_payload import PrimSelectionPayload

from .dialog_window import DialogWindow

REMOVE_BUTTON_STYLE = style = {
    "image_url": str(Path(ICON_PATH).joinpath("remove.svg")),
    "margin": 0,
    "padding": 0,
}


class AttributeWidget(SchemaPropertiesWidget):
    def __init__(
        self,
        title: str,
        schema: str,
        on_remove_schema_fn: Callable[[list[Usd.Prim]], None],
        attributes_order: list[str] = [],
    ):
        super().__init__(title, schema, False)
        self._button_frame = None
        self._allow_refresh = True
        self._property_utils = {}
        self._ext_prop_specs = {}
        self._attributes_order = attributes_order
        self._payload: PrimSelectionPayload = None
        self._on_remove_schema_fn = on_remove_schema_fn
        self.has_schema_of: Callable[[Usd.Prim], bool]

    def prim_has_schema(self, prim: Usd.Prim) -> bool:
        """
        Check if the given prim has the schema.
        """
        return prim.HasAPI(self._schema)

    def on_new_payload(self, payload: PrimSelectionPayload):
        """
        Called when a new payload is delivered. PropertyWidget can take this opportunity to update its UI models,
        or schedule full UI rebuild.
        """
        self._payload = payload

        if not payload or len(payload) == 0:
            return False

        if not super().on_new_payload(payload):
            return False
        return True

    def _filter_props_to_build(self, prim_properties: list[Usd.Property]):
        self._collapsable = len(prim_properties)

        prop_names: set[str] = set(utils.getSchemaPropertyNames(self._schema))
        filtered_props = [
            prop for prop in prim_properties if prop.GetName() in prop_names
        ]

        def split_at_capitals(title) -> list[str]:
            return re.findall(".[^A-Z]*", title)

        def get_display_name(name: str):
            name_segments = name.split(":")
            if len(name_segments) == 0:
                return name
            return " ".join(
                [
                    name_part.capitalize()
                    for name_part in split_at_capitals(name_segments[-1])
                ]
            )

        for prop in filtered_props:
            prop.SetDisplayName(get_display_name(prop.GetName()))

        return filtered_props

    def _customize_props_layout(self, props: list[UsdPropertyUiEntry]):
        indices = {name: pos for pos, name in enumerate(self._attributes_order)}
        sorted_props = sorted(
            props,
            key=lambda property: indices.get(
                property.prop_name.split(":")[-1], maxsize
            ),
        )
        return sorted_props

    def _show(self, title, on_yes):
        prompt = DialogWindow(
            "Remove component?",
            f"Are you sure you want to remove the '{title}' component?",
            "Yes",
            "No",
            ok_button_fn=on_yes,
            modal=True,
        )
        prompt.show()

    def _build_impl_with_remove_button(self):
        # behold the greatness of my omni.ui-fu! yeah, just kidding, items in a zstack (or wherever, but
        # it's important here) cannot stop mouse events going through the whole stack so I have to revert
        # the underlying frame's collapse when the remove button is clicked ...
        revert_last = False

        def on_collapsed_changed(collapsed):
            nonlocal revert_last
            if revert_last and collapsed:
                self._collapsable_frame.collapsed = False
            revert_last = False

        def on_remove_clicked_mouse(*_):
            def on_remove():
                nonlocal revert_last
                revert_last = True
                if self._on_remove_schema_fn is None:
                    return

                self._on_remove_schema_fn(
                    [self._get_prim(prim_path) for prim_path in self._payload]
                )

            self._show(self._title, on_remove)

        self._button_frame = ui.Frame()
        with self._button_frame:
            with ui.ZStack():
                with ui.VStack():
                    super().build_impl()
                    pass
                with ui.HStack():
                    ui.Spacer(width=ui.Fraction(0.5))
                    with ui.VStack(width=0):
                        ui.Spacer(height=5)
                        ui.Button(
                            style=REMOVE_BUTTON_STYLE,
                            height=16,
                            width=16,
                            identifier=f"remove_component_{self._title}",
                        ).set_mouse_pressed_fn(on_remove_clicked_mouse)
                    ui.Spacer(width=5)
        self._collapsable_frame.set_collapsed_changed_fn(on_collapsed_changed)

    def build_impl(self):
        self._button_frame = None
        if self._on_remove_schema_fn is not None:
            self._build_impl_with_remove_button()
        else:
            super().build_impl()
