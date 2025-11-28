import asyncio
import json
import urllib.parse
import uuid
import weakref
from enum import Enum
from typing import Callable, Optional

import carb
import requests
import wandelbots_api_client as wb
import wandelbots_api_client.models as wb_models
from wandelbots.omni.core.networks.reconnecting_websocket import ReconnectingWebsocket
from wandelbots.omni.utils.api import (
    ApiConfiguration,
    get_api_client_from_config,
    get_base_headers,
)


class IOValueType(Enum):
    BOOL = (0,)
    FLOAT = (1,)
    INTEGER = (2,)


IOValue = bool | int | float
OnInitCallback = Callable[[dict[str, IOValue]], None]
OnChangeCallback = Callable[[str, IOValue], None]


def get_io_value(io_value: wb_models.IOValue) -> IOValue | None:
    if io_value.boolean_value is not None:
        return io_value.boolean_value
    if io_value.integer_value is not None:
        return int(io_value.integer_value)
    if io_value.floating_value is not None:
        return float(io_value.floating_value)
    carb.log_error(f"IO value type not supported. {io_value}")
    return None


class Subscription:
    Id = str

    def __init__(
        self,
        unsubscribe: Callable[[Id], None],
        on_change: OnChangeCallback,
        get_value: Callable[[str], IOValue],
        on_init: Optional[OnInitCallback] = None,
    ):
        self.id: Subscription.Id = uuid.uuid4()
        self.get_value = get_value
        self.on_init = on_init
        self.on_change = on_change
        self._unsubscribe = unsubscribe

    def __del__(self):
        carb.log_verbose(f"IO Unsub {self.id}")
        self._unsubscribe(self.id)


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
        self.subscription_ios: dict[Subscription.Id, list[str]] = {}
        self.subscriptions: weakref.WeakKeyDictionary[Subscription.Id, Subscription] = (
            weakref.WeakValueDictionary()
        )
        self._subscription_lock = asyncio.Lock()
        self.io_subscriptions: dict[str, weakref.WeakSet[Subscription]] = {}
        self.io_cache: dict[str, IOValue] = {}
        self.io_stream = None
        self._io_stream_lock = asyncio.Lock()
        self.api_configuration = api_configuration

    async def clear(self):
        async with self._subscription_lock:
            await self.stop_stream()
            self.subscription_ios.clear()
            self.io_subscriptions.clear()

    async def subscribe(
        self,
        io_ids: list[str],
        on_change: OnChangeCallback,
        on_init: Optional[OnInitCallback] = None,
    ) -> Subscription:
        carb.log_verbose(f"Subscribe {io_ids}")
        if len(io_ids) == 0:
            raise ValueError("IO list is empty")

        async with self._subscription_lock:
            subscription = Subscription(
                unsubscribe=lambda id: (self._unsubscribe_callback(id)),
                on_change=on_change,
                on_init=on_init,
                get_value=self.get_io_value,
            )
            self._add_subscription(subscription, io_ids)
            if self.io_stream is not None:
                await self._restart_stream()

            carb.log_verbose(f"Subscription {subscription.id} created")
            return subscription

    async def get_io_value(self, io: str) -> IOValue | None:
        async with self._io_stream_lock:
            if io not in self.io_subscriptions:
                raise ValueError(
                    f"ControllerIOStreamService {self.cell}/{self.controller} is not subscribed to {io}"
                )
            return self.io_cache.get(io, None)

    async def get_available_ios(self) -> list[str]:
        async with get_api_client_from_config(self.api_configuration) as api_client:
            io_api = wb.ControllerIOsApi(api_client)
            io_descriptions = (
                await io_api.list_io_descriptions(
                    cell=self.cell, controller=self.controller
                )
            ).io_descriptions
            return [io_description.id for io_description in io_descriptions]

    def _unsubscribe_callback(self, subscription_id: Subscription.Id):
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self._unsubscribe(subscription_id))
        else:
            loop.run_until_complete(self._unsubscribe(subscription_id))

    async def _unsubscribe(self, subscription_id: Subscription.Id):
        carb.log_verbose(f"Unsubscribe {subscription_id}")
        async with self._subscription_lock:
            stream_running = self.io_stream is not None
            if stream_running:
                await self.stop_stream()

            self._remove_subscription(subscription_id)

            if stream_running and len(self.io_subscriptions.keys()) > 0:
                await self.start_stream()

    def _add_subscription(self, subscription: Subscription, ios: list[str]):
        self.subscriptions[subscription.id] = subscription
        self.subscription_ios[subscription.id] = ios

        for io in ios:
            if io in self.io_subscriptions:
                self.io_subscriptions[io].add(subscription)
            else:
                self.io_subscriptions[io] = weakref.WeakSet([subscription])

    def _remove_subscription(self, subscription_id: Subscription.Id):
        # remove from tracked subscriptions
        # because of weak ref the sub might be removed before the unsubscribe call
        ios: list[str] = []
        if subscription_id in self.subscription_ios:
            ios = list(self.subscription_ios[subscription_id])
            del self.subscription_ios[subscription_id]

        if subscription_id in self.subscriptions:
            del self.subscriptions[subscription_id]

        # remove from tracked IOs
        for io in ios:
            if io not in self.io_subscriptions:
                carb.log_error(
                    "IO not found in io_subscriptions which seems to be wrong"
                )
                return

            if len(self.io_subscriptions[io]) == 0:
                del self.io_subscriptions[io]

    async def _restart_stream(self):
        """Might be used when a subscription changed"""
        carb.log_verbose(f"Restarting stream {self.cell}/{self.controller}")
        await self.stop_stream()
        await self.start_stream()

    def _update_value(self, io: str, value: IOValue):
        if io not in self.io_cache:
            return

        if value == self.io_cache[io]:
            return

        self.io_cache[io] = value

        for subscription in self.io_subscriptions[io]:
            carb.log_verbose(f"{subscription.id} {io}={value}")
            subscription.on_change(io, value)

    async def _receive_io_state(self, io_result_data: str):
        try:
            result_response = json.loads(io_result_data)
            io_response = wb_models.ListIOValuesResponse.from_dict(
                result_response["result"]
            )
            async with self._io_stream_lock:
                for io_data in io_response.io_values:
                    self._update_value(io=io_data.io, value=get_io_value(io_data))
        except Exception as ex:
            carb.log_error(f"Failed to read {self.controller} io_state. error={ex}")

    async def start_stream(self):
        async with self._io_stream_lock:
            if len(self.io_subscriptions.keys()) == 0:
                carb.log_verbose(
                    f"Trying to start {self.cell}/{self.controller} without ios"
                )
                return

            if self.io_stream and self.io_stream.streaming:
                carb.log_verbose(f"{self.controller} io stream already started")
                return
            carb.log_info(f"Start {self.cell}/{self.controller} stream")

            self.io_cache = {}
            ios = self.io_subscriptions.keys()
            carb.log_verbose(f"Filling cache {list(ios)}")

            watched_ios: list[str] = []
            for io in ios:
                try:
                    response = wb_models.ListIOValuesResponse.from_dict(
                        requests.get(
                            f"{self.api_configuration.base_url}/cells/{self.cell}/controllers/{self.controller}/ios/values?{urllib.parse.urlencode({'ios': list({io})}, doseq=True)}",
                            headers=get_base_headers(
                                self.api_configuration.access_token
                            ),
                            timeout=10,
                        ).json()
                    )
                    self.io_cache[io] = get_io_value(response.io_values[0])
                    if self.io_cache[io] is None:
                        raise ValueError("Value not supported")
                    watched_ios.append(io)
                except Exception as ex:
                    carb.log_error(
                        f'Failed to retrieve {self.controller} "{io}" data. IO will not be watched. {ex}'
                    )
            carb.log_verbose(self.io_cache)
            if len(watched_ios) == 0:
                carb.log_info("No IOs found to watch, stream will not be started")
                return

            carb.log_verbose("Initializing io subscriptions")
            for subscription_id, subscription_ios in self.subscription_ios.items():
                subscription = self.subscriptions[subscription_id]
                if not subscription.on_init:
                    continue
                initial_subscription_values = dict(
                    [(io, self.io_cache.get(io, None)) for io in subscription_ios]
                )
                subscription.on_init(initial_subscription_values)

            carb.log_verbose("Connecting")
            ios_query_string = urllib.parse.urlencode({"ios": list(ios)}, doseq=True)

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
                carb.log_error(f"Failed to open io stream. {ex}")
                return

    async def stop_stream(self):
        carb.log_verbose("Stopping stream")
        async with self._io_stream_lock:
            carb.log_info(f"Stop {self.cell}/{self.controller} stream")
            self.io_cache = {}
            if not self.io_stream or not self.io_stream.streaming:
                carb.log_verbose(f"{self.controller} io stream not running")
                return
            await self.io_stream.close()


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
        headers = get_base_headers(api_configuration.access_token)
        response = wb_models.ListIODescriptionsResponse.from_dict(
            requests.get(
                f"{api_configuration.base_url}/cells/{cell}/controllers/{controller}/ios/description?{urllib.parse.urlencode({'ios': [io]}, doseq=True)}",
                headers=headers,
                timeout=10,
            ).json()
        )
        description = response.io_descriptions[0]
        if description.value_type == "IO_VALUE_DIGITAL":
            return IOValueType.BOOL
        if description.value_type == "IO_VALUE_ANALOG_INTEGER":
            return IOValueType.INTEGER
        if description.value_type == "IO_VALUE_ANALOG_FLOATING":
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
        bool_value = None
        integer_value = None
        float_value = None

        if isinstance(value, bool):
            bool_value = value
        elif isinstance(value, int):
            integer_value = str(value)
        elif isinstance(value, float):
            float_value = value

        carb.log_verbose(
            f"{cell}/{controller} Set IO  {io} b={bool_value} int={integer_value} float={float_value}"
        )

        async with get_api_client_from_config(api_configuration) as api_client:
            self.virtual_io_api = wb.VirtualRobotApi(api_client)

            await self.virtual_io_api.set_virtual_robot_io_value(
                cell=cell,
                controller=controller,
                io=io,
                bool=bool_value,
                integer=integer_value,
                double=float_value,
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
            carb.log_verbose(
                f"{service_key} subs {list(controller_service.io_subscriptions.keys())}"
            )
            if len(list(controller_service.io_subscriptions.keys())) == 0:
                carb.log_verbose(
                    f'Removing unused ControllerIOStreamService "{service_key}"'
                )
                del self.controller_services[service_key]


_io_service = IOStreamService()


def get_io_stream_service() -> IOStreamService:
    return _io_service
