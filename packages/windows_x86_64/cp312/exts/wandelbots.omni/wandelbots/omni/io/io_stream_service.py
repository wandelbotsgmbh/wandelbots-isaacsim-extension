import asyncio
import json
import urllib.parse
import uuid
import weakref
from enum import Enum
from typing import Callable, Optional

import carb
import wandelbots_api_client.v2 as wb
import wandelbots_api_client.v2.models as wb_models
from wandelbots.omni.core.networks.reconnecting_websocket import ReconnectingWebsocket
from wandelbots.omni.utils.api import (
    ApiConfiguration,
    get_api_client_from_config,
)


class IOValueType(Enum):
    BOOL = (0,)
    FLOAT = (1,)
    INTEGER = (2,)


IOValue = bool | int | float
OnInitCallback = Callable[[dict[str, IOValue]], None]
OnChangeCallback = Callable[[str, IOValue], None]


def get_io_value(io_value: wb_models.IOValue) -> IOValue | None:
    value_instance = io_value.actual_instance
    if isinstance(value_instance, wb_models.IOBooleanValue):
        return value_instance.value
    if isinstance(value_instance, wb_models.IOIntegerValue):
        return int(value_instance.value)
    if isinstance(value_instance, wb_models.IOFloatValue):
        return float(value_instance.value)
    carb.log_error(f"IO value type not supported. {io_value.actual_instance}")
    return None


class Subscription:
    Id = str

    def __init__(
        self,
        ios: list[str],
        unsubscribe: Callable[[Id], None],
        on_change: OnChangeCallback,
        get_value: Callable[[str], IOValue],
        on_init: Optional[OnInitCallback] = None,
    ):
        self._ios = ios
        self.id: Subscription.Id = uuid.uuid4()
        self.get_value = get_value
        self.on_init = on_init
        self.on_change = on_change
        self._unsubscribe = unsubscribe

    def __del__(self):
        carb.log_verbose(f"IO Unsub {self.id}")
        self._unsubscribe(self.id)

    @property
    def ios(self) -> list[str]:
        return self._ios


class ControllerIOStreamService:
    """Central access to controller specific IO stream
    - Value change events
    - Value caching
    - Access to latests values
    """

    def __init__(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
    ):
        self.cell = cell
        self.controller = controller

        self.subscriptions: weakref.WeakKeyDictionary[Subscription.Id, Subscription] = (
            weakref.WeakValueDictionary()
        )
        self._subscription_lock = asyncio.Lock()

        self.io_cache: dict[str, IOValue] = {}
        self.io_stream = None
        self._io_stream_lock = asyncio.Lock()
        self.api_configuration = api_configuration
        if self.api_configuration.version != "v2":
            raise ValueError("Only Wandelbots API v2 is supported for IO streaming")

        self._cached_io_subscriptions: dict[str, weakref.WeakSet[Subscription]] = {}

    async def clear(self):
        async with self._subscription_lock:
            await self.stop_stream()
            self._rebuild_subscription_cache()

    def has_subscriptions(self) -> bool:
        carb.log_verbose(f"{self.id} subs {list(self.subscriptions.keys())}")
        return len(self.subscriptions) > 0

    async def subscribe(
        self,
        io_ids: list[str],
        on_change: OnChangeCallback,
        on_init: Optional[OnInitCallback] = None,
    ) -> Subscription:
        carb.log_verbose(f"{self.id} Subscribe {io_ids}")
        if len(io_ids) == 0:
            raise ValueError(f"{self.id} IO list is empty")

        async with self._subscription_lock:

            def unsubscribe(id: Subscription.Id, weak_self=weakref.ref(self)):
                weak_self_instance = weak_self()
                if weak_self_instance is None:
                    return
                weak_self_instance._unsubscribe_callback(id)

            def get_io_value(io: str, weak_self=weakref.ref(self)) -> IOValue:
                weak_self_instance = weak_self()
                if weak_self_instance is None:
                    raise ValueError("IOStreamService has been deleted")
                return asyncio.get_event_loop().run_until_complete(
                    weak_self_instance.get_io_value(io)
                )

            subscription = Subscription(
                ios=io_ids,
                unsubscribe=unsubscribe,
                on_change=on_change,
                on_init=on_init,
                get_value=get_io_value,
            )
            self.subscriptions[subscription.id] = subscription
            self._rebuild_subscription_cache()
            if self.io_stream is not None:
                await self._restart_stream()

            carb.log_verbose(f"{self.id} Subscription {subscription.id} created")
            return subscription

    def _rebuild_subscription_cache(self):
        self._cached_io_subscriptions = {}
        for subscription in self.subscriptions.values():
            self.subscriptions[subscription.id] = subscription

            for io in subscription.ios:
                if io in self._cached_io_subscriptions:
                    self._cached_io_subscriptions[io].add(subscription)
                else:
                    self._cached_io_subscriptions[io] = weakref.WeakSet([subscription])

    async def get_io_value(self, io: str) -> IOValue | None:
        async with self._io_stream_lock:
            if io not in self._cached_io_subscriptions:
                raise ValueError(f"{self.id} is not subscribed to {io}")
            return self.io_cache.get(io, None)

    async def get_available_ios(self) -> list[str]:
        async with get_api_client_from_config(self.api_configuration) as api_client:
            io_api = wb.ControllerInputsOutputsApi(api_client)
            io_descriptions = await io_api.list_io_descriptions(
                cell=self.cell, controller=self.controller
            )
            return [io_description.io for io_description in io_descriptions]

    def _unsubscribe_callback(self, subscription_id: Subscription.Id):
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self._unsubscribe(subscription_id))
        else:
            loop.run_until_complete(self._unsubscribe(subscription_id))

    async def _unsubscribe(self, subscription_id: Subscription.Id):
        carb.log_verbose(f"{self.id} Unsubscribe {subscription_id}")
        async with self._subscription_lock:
            stream_running = self.io_stream is not None
            if stream_running:
                await self.stop_stream()

            self._remove_subscription(subscription_id)

            if stream_running and len(self._cached_io_subscriptions.keys()) > 0:
                await self.start_stream()

    def _remove_subscription(self, subscription_id: Subscription.Id):
        # Subscriptions usually remove themselves on deletion but we might be faster than GC so we clean up fast before rebuilding the cache
        if subscription_id not in self.subscriptions:
            carb.log_verbose(
                f"{self.id} Subscription {subscription_id} already removed"
            )
            return
        else:
            del self.subscriptions[subscription_id]
        self._rebuild_subscription_cache()

    def _add_subscription(self, subscription: Subscription):
        if subscription.id in self.subscriptions:
            carb.log_error(
                f"{self.id} Subscription {subscription.id} already exists in ControllerIOStreamService"
            )
            return
        self.subscriptions[subscription.id] = subscription
        self._rebuild_subscription_cache()

    async def _restart_stream(self):
        """Might be used when a subscription changed"""
        carb.log_verbose(f"{self.id} Restarting stream")
        await self.stop_stream()
        await self.start_stream()

    def _update_value(self, io: str, value: IOValue):
        if io not in self.io_cache:
            return

        if value == self.io_cache[io]:
            return

        self.io_cache[io] = value

        for subscription in self._cached_io_subscriptions[io]:
            carb.log_verbose(f"{subscription.id} {io}={value}")
            subscription.on_change(io, value)

    async def _receive_io_state(self, io_result_data: str):
        try:
            result_response = json.loads(io_result_data)
            io_response = wb_models.StreamIOValuesResponse.from_dict(
                result_response["result"]
            )
            async with self._io_stream_lock:
                for io_data in io_response.io_values:
                    self._update_value(
                        io=io_data.actual_instance.io, value=get_io_value(io_data)
                    )
        except Exception as ex:
            carb.log_error(f"{self.id} Failed to read io_state. error={ex}")

    async def start_stream(self):
        async with self._io_stream_lock:
            if len(self._cached_io_subscriptions.keys()) == 0:
                carb.log_verbose(f"{self.id} Trying to start without ios")
                return

            if self.io_stream and self.io_stream.streaming:
                carb.log_verbose(f"{self.id} io stream already started")
                return
            carb.log_info(f"{self.id} Start stream")

            self.io_cache = {}
            ios = self._cached_io_subscriptions.keys()
            carb.log_verbose(f"{self.id} Filling cache {list(ios)}")

            watched_ios: list[str] = []
            for io in ios:
                try:
                    async with get_api_client_from_config(
                        self.api_configuration
                    ) as api_client:
                        io_api = wb.ControllerInputsOutputsApi(api_client)
                        self.io_cache[io] = get_io_value(
                            (
                                await io_api.list_io_values(
                                    self.cell, self.controller, ios=[io]
                                )
                            )[0]
                        )
                    if self.io_cache[io] is None:
                        raise ValueError(f"Value for {io} not supported")
                    watched_ios.append(io)
                except wb.exceptions.NotFoundException:
                    carb.log_warn(f"{self.id} IO {io} not found, will not be watched")
                except Exception as ex:
                    carb.log_error(
                        f'Failed to retrieve {self.controller} "{io}" data. IO will not be watched. {ex}'
                    )
            carb.log_verbose(f"{self.id} Cache: {self.io_cache}")
            if len(watched_ios) == 0:
                carb.log_info(
                    f"{self.id} No IOs found to watch, stream will not be started"
                )
                return

            carb.log_verbose(f"{self.id} Initializing io subscriptions")
            for subscription_id, subscription in self.subscriptions.items():
                subscription = self.subscriptions[subscription_id]
                if not subscription.on_init:
                    continue
                initial_subscription_values = dict(
                    [(io, self.io_cache.get(io, None)) for io in subscription.ios]
                )
                subscription.on_init(initial_subscription_values)

            carb.log_verbose(f"{self.id} Connecting")
            ios_query_string = urllib.parse.urlencode(
                {"ios": list(watched_ios)}, doseq=True
            )

            uri = f"{self.api_configuration.base_url_websocket}/cells/{self.cell}/controllers/{self.controller}/ios/stream?{ios_query_string}"
            carb.log_verbose(f"Open io stream {uri}")
            self.io_stream = ReconnectingWebsocket(
                uri=uri,
                token=self.api_configuration.access_token,
                on_receive=self._receive_io_state,
            )
            try:
                await self.io_stream.open()
            except Exception as ex:
                carb.log_error(f"{self.id} Failed to open io stream. {ex}")
                return

    async def stop_stream(self):
        carb.log_verbose(f"{self.id} Stopping stream")
        async with self._io_stream_lock:
            carb.log_info(f"{self.id} Stop {self.cell}/{self.controller} stream")
            self.io_cache = {}
            if not self.io_stream or not self.io_stream.streaming:
                carb.log_verbose(f"{self.id} io stream not running")
                return
            await self.io_stream.close()

    @property
    def id(self):
        return f"{self.cell}/{self.controller}"


class IOStreamService:
    """Base class of io streaming watchers
    - subscribe to watch io changes for certain ios on controllers
    - the subscription offers access to controller values
    - start/stop streams to manually trigger service operation
    - all functions are intended to be threadsafe
    """

    def __init__(self):
        # Mapping of (cell, controller) :: controller streamer
        self.controller_services: dict[(str, str), ControllerIOStreamService] = {}
        self._controller_services_lock = asyncio.Lock()
        self.started_all_streams = False

    async def subscribe(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
        io_ids: list[str],
        on_change: OnChangeCallback,
        on_init: Optional[OnInitCallback] = None,
    ) -> Subscription:
        async with self._controller_services_lock:
            controller_service = self._get_or_create_service(
                api_configuration,
                cell,
                controller,
            )
            subscription = await controller_service.subscribe(
                io_ids, on_change, on_init
            )
            self._cleanup_unused_services()
            if self.started_all_streams:
                await controller_service.start_stream()
            return subscription

    async def start_all_streams(self):
        async with self._controller_services_lock:
            self.started_all_streams = True
            for service in self.controller_services.values():
                await service.start_stream()

    async def stop_all_streams(self):
        async with self._controller_services_lock:
            self.started_all_streams = False
            for service in self.controller_services.values():
                await service.stop_stream()

    async def clear(self):
        async with self._controller_services_lock:
            for service in self.controller_services.values():
                await service.clear()
            self.controller_services.clear()
            self.started_all_streams = False

    async def get_io_type(
        self, api_configuration: ApiConfiguration, cell: str, controller: str, io: str
    ) -> IOValueType:
        async with get_api_client_from_config(api_configuration) as api_client:
            io_api = wb.ControllerInputsOutputsApi(api_client)
            description = (
                await io_api.list_io_descriptions(
                    cell=cell, controller=controller, ios=[io]
                )
            )[0]
            if description.value_type == wb_models.IOValueType.IO_VALUE_BOOLEAN:
                return IOValueType.BOOL
            if description.value_type == wb_models.IOValueType.IO_VALUE_ANALOG_INTEGER:
                return IOValueType.INTEGER
            if description.value_type == wb_models.IOValueType.IO_VALUE_ANALOG_FLOAT:
                return IOValueType.FLOAT
            raise ValueError(f"Unsupported value type {description.value_type}")

    async def set_io_value(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
        io: str,
        value: IOValue,
    ):
        io_value: wb_models.IOValue = None

        if isinstance(value, bool):
            io_value = wb_models.IOValue(
                wb_models.IOBooleanValue(io=io, value=value, value_type="boolean")
            )
        elif isinstance(value, int):
            io_value = wb_models.IOValue(
                wb_models.IOIntegerValue(io=io, value=str(value), value_type="integer")
            )
        elif isinstance(value, float):
            io_value = wb_models.IOValue(
                wb_models.IOFloatValue(io=io, value=value, value_type="float")
            )
        carb.log_verbose(f"{cell}/{controller} Set IO  {io_value.actual_instance}")

        async with get_api_client_from_config(api_configuration) as api_client:
            self.virtual_io_api = wb.VirtualControllerInputsOutputsApi(api_client)

            await self.virtual_io_api.set_io_values(
                cell=cell,
                controller=controller,
                io_value=[io_value],
            )

    def _get_or_create_service(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
    ) -> ControllerIOStreamService:
        controller_key = IOStreamService._controller_key(
            api_configuration, cell, controller
        )
        if controller_key not in self.controller_services:
            carb.log_verbose(
                f'Creating new ControllerIOStreamService "{controller_key}"'
            )
            self.controller_services[controller_key] = ControllerIOStreamService(
                api_configuration=api_configuration, cell=cell, controller=controller
            )
        return self.controller_services[controller_key]

    def _controller_key(
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
    ) -> str:
        return f"config={api_configuration} cell={cell} controller={controller}"

    def _cleanup_unused_services(self):
        for service_key in list(self.controller_services.keys()):
            controller_service = self.controller_services[service_key]
            if not controller_service.has_subscriptions():
                carb.log_verbose(
                    f'Removing unused ControllerIOStreamService "{service_key}"'
                )
                del self.controller_services[service_key]


_io_service = IOStreamService()


def get_io_stream_service() -> IOStreamService:
    return _io_service
