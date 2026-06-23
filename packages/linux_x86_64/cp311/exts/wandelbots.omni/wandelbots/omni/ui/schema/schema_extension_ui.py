from dataclasses import dataclass
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
from wandelbots.omni.ui.create_context_menu.robot import RobotSpawnWindow
from omni.kit.async_engine import run_coroutine

from wandelbots.omni.ui.create_context_menu.robot.send_mounting_to_nova import (
    create_nova_mounting_from_payload,
    can_create_mounting_from_payload,
)
from wandelbots.omni.ui.create_context_menu.cell import CellSpawnWindow
from wandelbots.omni.core.collision.collider_preset import (
    apply_collider_preset_from_payload,
    can_apply_collider_preset,
)
from wandelbots.omni.ui.create_context_menu.pose import (
    ConvertPoseWindow,
    is_convertible_prim,
)


def _has_convertible_payload(payload: dict) -> bool:
    """Show the Convert entry when at least one selected prim is convertible
    (any transformable prim that is not already a ghost object)."""
    prim_list: list[Usd.Prim] = payload.get("prim_list", [])
    return any(is_convertible_prim(prim) for prim in prim_list)


class SchemaExtensionUI:
    """
    UI loader class for registering all schema data related to the wandelbots NOVA usd schema extension.

    The class needs to closed with unregister() to clean up the property window and prim menu.
    """

    def __init__(self):
        self._stage_menu_subscription = None
        self._add_menu_items = []
        self._frame_widget = PropertyGroupFrameWidget(id="wb_schema_nova_schema_widget")
        self._robot_spawn_window = RobotSpawnWindow()
        self._cell_spawn_window = CellSpawnWindow()
        self._convert_pose_window = ConvertPoseWindow()

        for component in schema_components:
            self._frame_widget.add_schema(component)

        self._register()

    def _register(self):
        self._register_property_windows()
        self._register_prim_menu()
        self._register_create_menu()

    def unregister(self):
        carb.log_verbose("Unregister Wandelbots NOVA schema UI")
        self._stage_menu_subscription = None
        self._unregister_property_windows()
        self._unregister_prim_menu()
        self._robot_spawn_window = None
        self._cell_spawn_window = None
        self._convert_pose_window = None

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
                        "name": "TCP in NOVA",
                        "show_fn": lambda payload: (
                            GhostObjectApiSchema.can_create_nova_tcp_object(payload)
                        ),
                        "onclick_fn": lambda payload: run_coroutine(
                            GhostObjectApiSchema.create_nova_tcp_from_payload(payload)
                        ),
                    },
                    {
                        "name": "TCP from NOVA",
                        "show_fn": lambda payload: (
                            GhostObjectApiSchema.can_create_tcp_prim_from_nova(payload)
                        ),
                        "onclick_fn": lambda payload: run_coroutine(
                            GhostObjectApiSchema.create_tcp_prim_from_nova(payload)
                        ),
                    },
                    {
                        "name": "Mounting in NOVA",
                        "show_fn": lambda payload: can_create_mounting_from_payload(
                            payload
                        ),
                        "onclick_fn": lambda payload: run_coroutine(
                            create_nova_mounting_from_payload(payload)
                        ),
                    },
                    {
                        "name": "Single Robot Model",
                        "onclick_fn": lambda payload, weak_self=weakref.proxy(self): (
                            weak_self._robot_spawn_window.open(payload)
                        ),
                    },
                    {
                        "name": "All Robots from Cell",
                        "onclick_fn": lambda payload, weak_self=weakref.proxy(self): (
                            weak_self._cell_spawn_window.open(payload)
                        ),
                    },
                    {
                        "name": "TCP",
                        "onclick_fn": TcpUtils.create_tcp_from_payload,
                    },
                    {
                        "name": "Collider Preset",
                        "show_fn": lambda payload: can_apply_collider_preset(payload),
                        "onclick_fn": lambda payload: (
                            apply_collider_preset_from_payload(payload)
                        ),
                    },
                    {
                        "name": "Convert to Ghost Object",
                        "show_fn": lambda payload: _has_convertible_payload(payload),
                        "onclick_fn": lambda payload, weak_self=weakref.proxy(self): (
                            weak_self._convert_pose_window.open(payload)
                        ),
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

        # Collider Preset entry in +Add menu
        self._add_menu_items.append(
            PrimPathWidget.add_button_menu_entry(
                "Wandelbots NOVA/Collider Preset",
                show_fn=lambda payload: True,
                onclick_fn=lambda payload: apply_collider_preset_from_payload(payload),
            )
        )

    def _unregister_prim_menu(self):
        # remove menus to property window path/+add and context menus +add submenu.
        if not self._add_menu_items:
            return
        for item in self._add_menu_items:
            PrimPathWidget.remove_button_menu_entry(item)
        self._add_menu_items = []

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


@dataclass
class SchemaExtensionUISubscription:
    schema_extension_ui: SchemaExtensionUI = None

    def __del__(self) -> None:
        if self.schema_extension_ui is not None:
            self.schema_extension_ui.unregister()


def register_schema_extension_ui() -> SchemaExtensionUISubscription:
    return SchemaExtensionUISubscription(SchemaExtensionUI())
