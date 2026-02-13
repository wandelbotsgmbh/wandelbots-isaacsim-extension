import asyncio
import json
import uuid
import weakref
from typing import Callable, Optional

import carb
from wandelbots_api_client.v2.api.bus_inputs_outputs_api import BUSInputsOutputsApi
from wandelbots_api_client.v2.models.io_value import (
    IOValue,
    IOBooleanValue,
    IOIntegerValue,
    IOFloatValue,
)
from wandelbots_api_client.v2.models.io_value_type import IOValueType
from wandelbots.omni.utils.api import (
    ApiConfiguration,
    get_api_client_from_config,
)
from wandelbots.omni.core.networks.nats_connector import (
    NatsSubscriptionService,
)

OnInitCallback = Callable[[dict[str, IOValue]], None]
OnChangeCallback = Callable[[str, IOValue], None]


def get_io_value_from_dict(io_value_dict: dict) -> IOValue | None:
    """Extract IO value from NATS message dict and convert to IOValue
    Returns the actual value compatible with wb_models.IOValue
    """
    io = io_value_dict.get("io")
    value = io_value_dict.get("value")
    value_type = io_value_dict.get("value_type")

    if value is None:
        carb.log_warn(f"IO value is None in {io_value_dict}")
        return None

    try:
        # Map NATS value_type to IOValueType enum values
        if value_type == "boolean":
            # Handle both boolean and string representations
            if isinstance(value, bool):
                return IOBooleanValue(io=io, value=value)
            elif isinstance(value, str):
                return IOBooleanValue(io=io, value=value.lower() in ("true", "1"))
            else:
                return IOBooleanValue(io=io, value=bool(value))
        elif value_type == "integer":
            return IOIntegerValue(io=io, value=str(value))
        elif value_type == "float":
            return IOFloatValue(io=io, value=float(value))
        else:
            carb.log_error(f"Unknown value_type '{value_type}' in {io_value_dict}")
            return None
    except (ValueError, TypeError) as ex:
        carb.log_error(
            f"Failed to convert value '{value}' to type '{value_type}': {ex}"
        )
        return None


class Subscription:
    Id = str

    def __init__(
        self,
        unsubscribe: Callable[[str], None],
        on_change: OnChangeCallback,
        get_value: Callable[[str], IOValue | None],
        on_init: Optional[OnInitCallback] = None,
    ):
        self.id: Subscription.Id = uuid.uuid4()
        self.get_value = get_value
        self.on_init = on_init
        self.on_change = on_change
        self._unsubscribe = unsubscribe

    def __del__(self):
        carb.log_verbose(f"Bus IO Unsub {self.id}")
        self._unsubscribe(self.id)


class BusIOStream:
    """NATS-based Bus IO stream service
    - Value change events via NATS subscription
    - Value caching
    - Access to latest values
    - Subscribes to: nova.v2.cells.{cell}.bus-ios.ios
    """

    def __init__(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
    ):
        self.cell = cell
        self.subscription_ios: dict[Subscription.Id, list[str]] = {}
        self.subscriptions: weakref.WeakKeyDictionary[Subscription.Id, Subscription] = (
            weakref.WeakValueDictionary()
        )
        self._subscription_lock = asyncio.Lock()
        self.io_subscriptions: dict[str, weakref.WeakSet[Subscription]] = {}
        self.io_cache: dict[str, IOValue] = {}
        self._nats_lock = asyncio.Lock()
        self.api_configuration = api_configuration

        # NATS subscription service handles connection lifecycle
        self._nats_service: Optional[NatsSubscriptionService] = None

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
        carb.log_verbose(f"Subscribe Bus IO {io_ids}")
        if len(io_ids) == 0:
            raise ValueError("IO list is empty")

        async with self._subscription_lock:
            subscription = Subscription(
                unsubscribe=lambda id: self._unsubscribe_callback(id),
                on_change=on_change,
                on_init=on_init,
                get_value=self.get_io_value,
            )
            self._add_subscription(subscription, io_ids)
            if self._nats_service is not None:
                await self._restart_stream()

            carb.log_verbose(f"Bus IO Subscription {subscription.id} created")
            return subscription

    async def get_io_value(self, io: str) -> IOValue | None:
        async with self._nats_lock:
            if io not in self.io_subscriptions:
                raise ValueError(f"BusIOStream {self.cell} is not subscribed to {io}")
            return self.io_cache.get(io, None)

    async def get_available_ios(self) -> list[str]:
        """Get available Bus IOs from the API

        Returns list of BusIODescription.io (unique identifiers)
        """
        try:
            async with get_api_client_from_config(self.api_configuration) as api_client:
                bus_io_api = BUSInputsOutputsApi(api_client)
                # list_io_descriptions returns List[BusIODescription]
                io_descriptions = await bus_io_api.list_bus_io_descriptions(
                    cell=self.cell
                )
                # BusIODescription has 'io' field as unique identifier
                return [io_desc.io for io_desc in io_descriptions]
        except Exception as ex:
            carb.log_error(f"Failed to get available Bus IOs: {ex}")
            return []

    def _unsubscribe_callback(self, subscription_id: Subscription.Id):
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self._unsubscribe(subscription_id))
        else:
            loop.run_until_complete(self._unsubscribe(subscription_id))

    async def _unsubscribe(self, subscription_id: Subscription.Id):
        carb.log_verbose(f"Unsubscribe Bus IO {subscription_id}")
        async with self._subscription_lock:
            stream_running = self._nats_service is not None
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
        """Restart stream when subscriptions change"""
        carb.log_verbose(f"Restarting NATS stream for cell {self.cell}")
        await self.stop_stream()
        await self.start_stream()

    def _update_value(self, io: str, value: IOValue):
        # Check if we're subscribed to this IO
        if io not in self.io_subscriptions:
            return

        # Check if value has actually changed
        if io in self.io_cache and value == self.io_cache[io]:
            return

        # Update cache
        self.io_cache[io] = value

        # Notify all subscriptions for this IO
        for subscription in self.io_subscriptions[io]:
            carb.log_verbose(f"Bus IO {subscription.id} {io}={value}")
            subscription.on_change(io, value)

    async def _handle_nats_message(self, msg):
        """Handle incoming NATS message with IO values"""
        try:
            if not msg.data:
                carb.log_warn(f"Received empty NATS message for cell {self.cell}")
                return

            data = json.loads(msg.data.decode())

            carb.log_verbose(f"Bus IO NATS message received: {data}")

            # Process I/O values from NATS message
            io_values = data

            if not io_values:
                carb.log_verbose("Received NATS message with no io_values")
                return

            async with self._nats_lock:
                for io_value_dict in io_values:
                    io_id = io_value_dict.get("io")
                    if not io_id:
                        carb.log_warn(f"IO value missing 'io' field: {io_value_dict}")
                        continue

                    # Only process IOs we're subscribed to
                    if io_id in self.io_subscriptions:
                        value = get_io_value_from_dict(io_value_dict)
                        if value is not None:
                            self._update_value(io=io_id, value=value)
                    else:
                        carb.log_verbose(f"Ignoring unsubscribed IO: {io_id}")

        except json.JSONDecodeError as ex:
            carb.log_error(f"Failed to decode Bus IO NATS message as JSON: {ex}")
        except EOFError as ex:
            carb.log_warn(f"EOF while reading NATS message for cell {self.cell}: {ex}")
            # EOF typically means connection was interrupted mid-message
            # The reconnect callback will handle resubscription
        except Exception as ex:
            carb.log_error(
                f"Failed to process Bus IO NATS message for cell {self.cell}: {ex}",
                exc_info=True,
            )

    async def start_stream(self):
        """Start NATS subscription for Bus IOs"""
        async with self._nats_lock:
            if len(self.io_subscriptions.keys()) == 0:
                carb.log_verbose(
                    f"Trying to start Bus IO stream for {self.cell} without ios"
                )
                return

            if self._nats_service and self._nats_service.is_subscribed:
                carb.log_verbose(f"Bus IO NATS stream for {self.cell} already started")
                return

            carb.log_info(f"Start Bus IO NATS stream for {self.cell}")

            # Initialize cache with current values
            self.io_cache = {}
            ios = list(self.io_subscriptions.keys())
            carb.log_verbose(f"Filling Bus IO cache for {ios}")

            watched_ios: list[str] = []
            try:
                # Get current values for all IOs at once via API
                async with get_api_client_from_config(
                    self.api_configuration
                ) as api_client:
                    bus_io_api = BUSInputsOutputsApi(api_client)
                    # get_io_values returns List[IOValue] with io field
                    io_values_response = await bus_io_api.get_bus_io_values(
                        cell=self.cell,
                        ios=list(ios),  # Pass list of IO identifiers
                    )

                    for io_value_obj in io_values_response:
                        io_value = io_value_obj.actual_instance
                        io_id = io_value.io
                        if io_id not in ios:
                            continue

                        self.io_cache[io_id] = io_value
                        watched_ios.append(io_id)
            except Exception as ex:
                carb.log_error(
                    f"Failed to retrieve Bus IO values. IOs ({watched_ios}) will not be watched. {ex}"
                )

            carb.log_verbose(f"Bus IO cache: {self.io_cache}")

            if len(watched_ios) == 0:
                carb.log_info("No Bus IOs found to watch, stream will not be started")
                return

            # Initialize subscriptions with current values
            carb.log_verbose("Initializing Bus IO subscriptions")
            for subscription_id, subscription_ios in self.subscription_ios.items():
                subscription = self.subscriptions[subscription_id]
                if not subscription.on_init:
                    continue
                initial_subscription_values = dict(
                    [(io, self.io_cache.get(io, None)) for io in subscription_ios]
                )
                subscription.on_init(initial_subscription_values)

            # Create NATS subscription service if not exists
            subject = f"nova.v2.cells.{self.cell}.bus-ios.ios"
            self._nats_service = NatsSubscriptionService(
                base_url=self.api_configuration.base_url,
                subject=subject,
                message_handler=self._handle_nats_message,
                access_token=self.api_configuration.access_token,
                context_name=f"cell {self.cell}",
            )

            if not await self._nats_service.connect():
                carb.log_error(f"Failed to connect NATS for cell {self.cell}")
                self._nats_service = None

    async def stop_stream(self):
        """Stop NATS subscription"""
        carb.log_verbose("Stopping Bus IO NATS stream")
        async with self._nats_lock:
            carb.log_info(f"Stop Bus IO NATS stream for {self.cell}")
            self.io_cache = {}

            if self._nats_service:
                await self._nats_service.disconnect()
                self._nats_service = None


class BusIOStreamService:
    """Manager for Bus IO stream services
    - subscribe to watch Bus IO changes for certain ios in cells
    - the subscription offers access to Bus IO values
    - start/stop streams to manually trigger service operation
    - all functions are intended to be threadsafe
    """

    def __init__(self):
        # Mapping of cell :: Bus IO streamer
        self.cell_services: dict[str, BusIOStream] = {}
        self._cell_services_lock = asyncio.Lock()
        self.started_all_streams = False

    async def subscribe(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        io_ids: list[str],
        on_change: OnChangeCallback,
        on_init: Optional[OnInitCallback] = None,
    ) -> Subscription:
        async with self._cell_services_lock:
            cell_service = self._get_or_create_service(
                api_configuration,
                cell,
            )
            subscription = await cell_service.subscribe(io_ids, on_change, on_init)
            self._cleanup()
            if self.started_all_streams:
                await cell_service.start_stream()
            return subscription

    async def start_all_streams(self):
        async with self._cell_services_lock:
            self.started_all_streams = True
            for service in self.cell_services.values():
                await service.start_stream()

    async def stop_all_streams(self):
        async with self._cell_services_lock:
            self.started_all_streams = False
            for service in self.cell_services.values():
                await service.stop_stream()

    async def clear(self):
        async with self._cell_services_lock:
            for service in self.cell_services.values():
                await service.clear()
            self.cell_services.clear()
            self.started_all_streams = False

    async def get_io_value(
        self, api_configuration: ApiConfiguration, cell: str, io: str
    ) -> IOValue | None:
        """Get cached Bus IO value from the service

        Returns the latest cached value or None if not available
        """
        async with self._cell_services_lock:
            cell_key = self._cell_key(api_configuration, cell)
            if cell_key not in self.cell_services:
                return None

            cell_service = self.cell_services[cell_key]
            return await cell_service.get_io_value(io)

    async def get_io_type(
        self, api_configuration: ApiConfiguration, cell: str, io: str
    ) -> IOValueType:
        """Get IO type from Bus IO API

        Returns IOValueType enum from wandelbots_api_client
        """
        async with get_api_client_from_config(api_configuration) as api_client:
            bus_io_api = BUSInputsOutputsApi(api_client)
            # Get all IO descriptions and find the specific one
            io_descriptions = await bus_io_api.list_bus_io_descriptions(cell=cell)
            for io_desc in io_descriptions:
                if io_desc.io == io:
                    return io_desc.value_type
            raise ValueError(f"Bus IO {io} not found in cell {cell}")

    async def set_io_value(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        io: str,
        value: IOValue,
    ):
        """Set Bus IO value via API using BUSInputsOutputsApi.set_output_values"""

        carb.log_verbose(f"{cell} Set Bus IO {io} to {value}")

        async with get_api_client_from_config(api_configuration) as api_client:
            bus_io_api = BUSInputsOutputsApi(api_client)

            # set_bus_io_values expects a list of IOValue objects
            await bus_io_api.set_bus_io_values(
                cell=cell,
                io_value=[IOValue(value)],
            )

    def _cell_key(self, api_configuration: ApiConfiguration, cell: str) -> str:
        return f"{api_configuration.base_url}#{cell}"

    def _get_or_create_service(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
    ) -> BusIOStream:
        cell_key = self._cell_key(api_configuration, cell)
        if cell_key not in self.cell_services:
            carb.log_verbose(f'Creating new BusIOStream "{cell_key}"')
            self.cell_services[cell_key] = BusIOStream(
                api_configuration=api_configuration, cell=cell
            )
        return self.cell_services[cell_key]

    def _cleanup(self):
        for service_key in list(self.cell_services.keys()):
            cell_service = self.cell_services[service_key]
            carb.log_verbose(
                f"{service_key} subs {list(cell_service.io_subscriptions.keys())}"
            )
            if len(list(cell_service.io_subscriptions.keys())) == 0:
                carb.log_verbose(f'Removing unused BusIOStream "{service_key}"')
                del self.cell_services[service_key]


_bus_io_stream_service = BusIOStreamService()


def get_bus_io_stream_service() -> BusIOStreamService:
    return _bus_io_stream_service
