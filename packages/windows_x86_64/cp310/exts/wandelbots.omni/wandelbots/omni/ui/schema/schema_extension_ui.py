from typing import cast
import weakref
from .widgets import SchemaComponent, PropertyGroupFrameWidget
import omni.kit.window.property as property_window_ext
import carb
from omni.kit.property.usd import PrimPathWidget
from pxr import Usd
from omni.kit.property.usd.prim_selection_payload import PrimSelectionPayload
from .schema_components import (
    schema_components,
    GhostObjectApiSchema,
)
import omni.usd
import omni.kit.context_menu
from wandelbots.omni.usd import TcpUtils
from wandelbots.omni.ui.utils import get_icon


class SchemaExtensionUI:
    """
    UI loader class for registering all schema data related to the wandelbots NOVA usd schema extension.

    The class needs to closed with unregister() to clean up the property window and prim menu.
    """

    def __init__(self):
        self._stage_create_menu_subscription = None
        self._stage_menu_subscription = None
        self._add_menu_items = []
        self._frame_widget = PropertyGroupFrameWidget(id="wb_schema_nova_schema_widget")

        for component in schema_components:
            self._frame_widget.add_schema(component)

        self._register()

    def __del__(self):
        self.unregister()

    def _register(self):
        self._register_property_windows()
        self._register_prim_menu()
        self._register_create_menu()

    def unregister(self):
        carb.log_verbose("Unregister Wandelbots NOVA schema UI")
        self._unregister_property_windows()
        self._unregister_prim_menu()
        self._stage_create_menu_subscription = None

    def _register_create_menu(self):
        create_menu_dict = {
            "name": {
                "Wandelbots NOVA": [
                    {
                        "name": "Ghost Object",
                        "show_fn": lambda payload: (
                            GhostObjectApiSchema.can_create_ghost_object(payload)
                        ),
                        "onclick_fn": lambda payload: (
                            GhostObjectApiSchema.create_ghost_object_from_payload(
                                payload
                            )
                        ),
                    },
                    {
                        "name": "TCP",
                        "onclick_fn": TcpUtils.create_tcp_from_payload,
                    },
                ]
            },
            "glyph": get_icon("wandelbots.png"),
        }
        self._stage_menu_subscription = omni.kit.context_menu.add_menu(
            create_menu_dict, "CREATE"
        )

    def _register_property_windows(self):
        property_window = property_window_ext.get_window()
        if not property_window:
            carb.log_error("Property window extension is None/missing")
            return

        if self._frame_widget:
            property_window.register_widget(
                "prim", self._frame_widget.id, self._frame_widget
            )

    def _unregister_property_windows(self):
        property_window = property_window_ext.get_window()

        if not property_window:
            carb.log_error("Property window extension is None/missing")
            return

        if self._frame_widget:
            self._frame_widget.clean()
            property_window.unregister_widget("prim", self._frame_widget.id)

    def _register_prim_menu(self):
        def show_fn(component: SchemaComponent, menu_payload: dict):
            prim_list: list[Usd.Prim] = menu_payload.get("prim_list", [])
            return all([component.can_add(prim) for prim in prim_list])

        for component in schema_components:
            self._add_menu_items.append(
                PrimPathWidget.add_button_menu_entry(
                    f"Wandelbots NOVA/{component.title}",
                    show_fn=lambda payload, c=component: show_fn(c, payload),
                    onclick_fn=lambda payload, c=component, weak_self=weakref.proxy(self): (
                        weak_self._prim_add_api(c, payload)
                    ),
                )
            )

    def _unregister_prim_menu(self):
        # remove menus to property window path/+add and context menus +add submenu.
        for item in self._add_menu_items:
            PrimPathWidget.remove_button_menu_entry(item)

        self._add_menu_items = None

    def _prim_add_api(self, component: SchemaComponent, payload: PrimSelectionPayload):
        component.apply(
            [
                cast(Usd.Stage, omni.usd.get_context().get_stage()).GetPrimAtPath(
                    prim_path
                )
                for prim_path in payload
            ]
        )
        self._frame_widget.request_rebuild()
