from typing import Callable
import wandelbots_api_client.v2 as wb
import asyncio
import omni.ui as ui
from wandelbots.omni.utils.api import get_api_client_from_config


class TcpItem(ui.AbstractItem):
    def __init__(self, tcp: str):
        super().__init__()
        self.tcp = tcp
        self.model = ui.SimpleStringModel(tcp)


class TcpModel(ui.AbstractItemModel):
    def __init__(self, selected_tcp: str, tcps: list[str]):
        super().__init__()
        self._items = [TcpItem(tcp) for tcp in tcps]
        self._current_index = ui.SimpleIntModel(
            tcps.index(selected_tcp) if selected_tcp is not None else -1
        )
        self._current_index.add_value_changed_fn(
            lambda m: self._item_changed(self._items[m.as_int])
        )

    def get_item_children(self, item):
        return self._items

    def get_item_value_model(self, item: TcpItem, column_id):
        if not item:
            return self._current_index
        return item.model


class TcpSelector:
    def __init__(
        self,
        api_configuration,
        cell: str,
        controller: str,
        motion_group: str,
        tcp_changed_fn: Callable[[str], None],
        selected_tcp: str = None,
    ):
        self._api_configuration = api_configuration
        self._cell = cell
        self._controller = controller
        self._motion_group = motion_group
        self._tcp_changed_fn = tcp_changed_fn
        self._tcp_names = []
        self._selected_tcp = selected_tcp
        task = asyncio.get_event_loop().create_task(self.refresh_tcp_names())
        task.add_done_callback(lambda _: self._build_ui())
        self._frame = ui.Frame()

    async def refresh_tcp_names(self):
        async with get_api_client_from_config(self._api_configuration) as api:
            tcps = await wb.VirtualControllerApi(api).list_virtual_controller_tcps(
                cell=self._cell,
                controller=self._controller,
                motion_group=self._motion_group,
            )
            self._tcp_names = [tcp.id for tcp in tcps]

    def _build_ui(self):
        self._frame.clear()
        with self._frame:
            if len(self._tcp_names) == 0:
                ui.Label("No TCPs found")
                return
            self._tcp_names_model = TcpModel(self._selected_tcp, self._tcp_names)

            def assign_tcp(
                model: TcpModel,
                item: TcpItem,
            ):
                self._tcp_changed_fn(item.tcp)

            self._tcp_names_model_sub = self._tcp_names_model.subscribe_item_changed_fn(
                assign_tcp
            )
            ui.ComboBox(self._tcp_names_model)
