from omni.kit.window.property.templates import SimplePropertyWidget
from omni.kit.property.usd.prim_selection_payload import PrimSelectionPayload
from .attribute_widget import AttributeWidget
from pxr import Usd
import carb


class SchemaComponent:
    def __init__(self, title: str, schema: str, attributes_order: list[str] = []):
        self.name: str = schema.__name__
        self.title: str = title
        self.schema: str = schema
        self.attributes_order: list[str] = attributes_order

    def is_present(self, prim: Usd.Prim) -> bool:
        """
        Check if the given prim has the schema.
        """
        return prim.HasAPI(self.schema)

    def can_add(self, prim: Usd.Prim) -> bool:
        """
        Check if the schema can be added to the given prim.
        """
        return not prim.HasAPI(self.schema)

    def apply(self, prims: list[Usd.Prim]) -> None:
        for prim in prims:
            self.schema.Apply(prim)

    def remove(self, prims: list[Usd.Prim]) -> None:
        for prim in prims:
            if prim.HasAPI(self.schema):
                prim.RemoveAPI(self.schema)
                carb.log_info(f"Removed API: {self.name} from prim: {prim.GetPath()}")
            else:
                carb.log_warn(
                    f"Prim {prim.GetPath()} does not have API {self.name} to remove."
                )


class PropertyGroupFrameWidget(SimplePropertyWidget):
    class ExtWidget:
        def __init__(self, widget: AttributeWidget):
            self.enabled = False
            self.widget = widget

    def __init__(
        self,
        id: str,
    ):
        super().__init__("Wandelbots NOVA")
        self.id = id

        self._base_subwidgets: list[AttributeWidget] = []
        self._base_enabled = []
        self._any_visible = False
        self._refresh_enabled = False
        self.popup_populate_fns = {}

    def add_schema(self, schema_component: SchemaComponent):
        def on_remove_schema_fn(prims: list[Usd.Prim]):
            schema_component.remove(prims)
            self.request_rebuild()

        self._base_subwidgets.append(
            AttributeWidget(
                title=schema_component.title,
                schema=schema_component.schema,
                on_remove_schema_fn=on_remove_schema_fn,
                attributes_order=schema_component.attributes_order,
            )
        )
        self.request_rebuild()

    def register_popup_menu_populate_fn(self, base_name, populate_fn):
        self.popup_populate_fns[base_name] = populate_fn

    def build_items(self):
        if self._refresh_enabled:
            # refresh enabled data before rebuilding, visibility might have changed
            self.on_new_payload(self._payload)

        self._any_item_visible = self._any_visible

        if not self._any_visible:
            return

        sub_widget: AttributeWidget
        for enabled, sub_widget in zip(self._base_enabled, self._base_subwidgets):
            if enabled:
                sub_widget._filter = self._filter
                sub_widget.build_impl()

        self._collapsable_frame.visible = True
        self._collapsable_frame.name = "groupFrame"

    def _build_frame(self):
        super()._build_frame()

        if not self._any_visible:
            self._collapsable_frame.visible = False

    def request_rebuild(self):
        if self._collapsable_frame is not None:
            self._collapsable_frame.visible = True
        self._refresh_enabled = True
        super().request_rebuild()

    def on_new_payload(self, payload: PrimSelectionPayload):
        self._any_visible = False
        self._refresh_enabled = False
        self.popup_populate_fns = {}

        if not super().on_new_payload(payload):
            return False
        for sub_widget in self._base_subwidgets:
            sub_widget.on_new_payload(payload)

        if len(payload) == 0:
            self._base_enabled = False
            self._any_visible = False
            return True

        payload_prims = [
            sub_widget._get_prim(p)
            for sub_widget in self._base_subwidgets
            for p in payload
        ]

        self._base_enabled = [
            all(sub_widget.prim_has_schema(prim) for prim in payload_prims)
            for sub_widget in self._base_subwidgets
        ]

        self._any_visible = any(
            [
                all(sub_widget.prim_has_schema(prim) for prim in payload_prims)
                for sub_widget in self._base_subwidgets
            ]
        )

        # we want to be there always so on change we can just rebuild this widget instead
        # of the whole property window
        return True  # any(self._base_enabled)
