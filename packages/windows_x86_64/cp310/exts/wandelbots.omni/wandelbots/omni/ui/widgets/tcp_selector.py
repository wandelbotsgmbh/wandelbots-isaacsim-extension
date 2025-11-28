from dataclasses import dataclass
from typing import Callable
import weakref
import wandelbots_api_client.v2 as wb
import asyncio
import omni.ui as ui
from wandelbots.omni.utils.api import get_api_client_from_config
from omni.kit.async_engine import run_coroutine
import carb


class TcpItem(ui.AbstractItem):
    def __init__(self, tcp: str):
        super().__init__()
        self.tcp = tcp
        self.model = ui.SimpleStringModel(tcp)


class TcpModel(ui.AbstractItemModel):
    def __init__(
        self,
        tcps: list[str],
        selected_tcp: str | None = None,
        select_first_tcp_fallback: bool = True,
    ):
        super().__init__()
        self._items = [TcpItem(tcp) for tcp in tcps]
        self._current_index = ui.SimpleIntModel(-1)

        if selected_tcp in tcps:
            self._current_index = ui.SimpleIntModel(tcps.index(selected_tcp))
        elif select_first_tcp_fallback and len(tcps) > 0:
            self._current_index = ui.SimpleIntModel(0)
        self._current_index.add_value_changed_fn(
            lambda m: self._item_changed(self._items[m.as_int])
        )

    def get_item_children(self, item):
        return self._items

    def get_item_value_model(self, item: TcpItem, column_id):
        if not item:
            return self._current_index
        return item.model

    @property
    def selected_tcp(self) -> str | None:
        if self._current_index.as_int == -1:
            return None
        return self._items[self._current_index.as_int].tcp

    @property
    def tcp_names(self) -> list[str]:
        return [item.tcp for item in self._items]


class TcpSelector:
    def __init__(
        self,
        api_configuration,
        cell: str,
        controller: str,
        motion_group: str,
        tcp_changed_fn: Callable[[str], None] | None = None,
        selected_tcp: str = None,
        select_first_tcp_fallback=True,
    ):
        self._api_configuration = api_configuration
        self._cell = cell
        self._controller = controller
        self._motion_group = motion_group
        self._tcp_changed_fn = tcp_changed_fn
        self._tcp_names_model = None
        self._initial_selected_tcp = selected_tcp
        self._select_first_tcp_fallback = select_first_tcp_fallback
        self._frame = ui.Frame()
        self._refresh_tcp_names_with_ui_update()
        self._tcp_subscription = subscribe_tcp_list_changed(
            api_configuration,
            cell,
            controller,
            motion_group,
            tcps_changed_fn=lambda weak_self=weakref.proxy(
                self
            ): weak_self._refresh_tcp_names_with_ui_update(),
        )

    async def refresh_tcp_names(self):
        async with get_api_client_from_config(self._api_configuration) as api:
            tcp_ids = list(
                (
                    await wb.MotionGroupApi(api).get_motion_group_description(
                        cell=self._cell,
                        controller=self._controller,
                        motion_group=self._motion_group,
                    )
                ).tcps.keys()
            )
            if self._tcp_names_model is None:
                self._tcp_names_model = TcpModel(
                    tcp_ids,
                    self._initial_selected_tcp,
                    select_first_tcp_fallback=self._select_first_tcp_fallback,
                )
            else:
                selected_tcp = self._tcp_names_model.selected_tcp
                self._tcp_names_model = TcpModel(
                    tcp_ids,
                    selected_tcp,
                    select_first_tcp_fallback=self._select_first_tcp_fallback,
                )
            if self._tcp_changed_fn is None:
                return

            def assign_tcp(
                model: TcpModel,
                item: TcpItem,
                weak_self=weakref.proxy(self),
            ):
                weak_self._tcp_changed_fn(item.tcp)

            self._tcp_names_model_sub = self._tcp_names_model.subscribe_item_changed_fn(
                assign_tcp
            )

    def _refresh_tcp_names_with_ui_update(self):
        task = run_coroutine(self.refresh_tcp_names())

        def task_done_callback(future: asyncio.Future):
            try:
                future.result()
                self._build_ui()
            except Exception as e:
                carb.log_verbose(f"Error refreshing TCP names: {str(e)}")

        task.add_done_callback(task_done_callback)

    def _build_ui(self):
        self._frame.clear()
        with self._frame:
            if not self._tcp_names_model:
                ui.Label("Loading tcps...")
                return
            if len(self._tcp_names_model.tcp_names) == 0:
                ui.Label("No TCPs found")
                return

            ui.ComboBox(self._tcp_names_model)

    @property
    def selected_tcp(self) -> str | None:
        return (
            self._tcp_names_model.selected_tcp
            if self._tcp_names_model
            else self._initial_selected_tcp
        )


@dataclass
class TcpSubscription:
    poll_task: asyncio.Task

    def __del__(self):
        self.poll_task.cancel()


def subscribe_tcp_list_changed(
    api_configuration,
    cell: str,
    controller: str,
    motion_group: str,
    tcps_changed_fn: Callable[[], None],
    polling_interval: float = 2.0,
) -> TcpSubscription:
    async def poll_tcps():
        # TODO replace with NATS subscription
        async with get_api_client_from_config(api_configuration) as api:
            motion_group_api = wb.MotionGroupApi(api)

            async def fetch_tcp_ids():
                try:
                    motion_group_description = (
                        await motion_group_api.get_motion_group_description(
                            cell=cell,
                            controller=controller,
                            motion_group=motion_group,
                        )
                    )
                    return set(motion_group_description.tcps.keys())
                except Exception as e:
                    carb.log_warn(f"Error fetching TCP ids: {str(e)}")
                    return None

            initial_tcp_ids = await fetch_tcp_ids()

            while True:
                fetched_tcp_ids = await fetch_tcp_ids()
                if fetched_tcp_ids and fetched_tcp_ids != initial_tcp_ids:
                    initial_tcp_ids = fetched_tcp_ids
                    tcps_changed_fn()

                await asyncio.sleep(polling_interval)

    return TcpSubscription(run_coroutine(poll_tcps()))
