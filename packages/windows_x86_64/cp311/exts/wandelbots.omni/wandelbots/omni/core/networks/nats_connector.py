"""NATS WebSocket connection utilities for Wandelbots NOVA API"""

import asyncio
import urllib.parse
from typing import Callable, Optional
from dataclasses import dataclass

import carb
import nats
from nats.aio.client import Client as NatsClient
from nats.aio.subscription import Subscription as NatsSubscription


@dataclass
class NatsConnectionConfig:
    """Configuration for NATS connection"""

    max_reconnect_attempts: int = -1  # -1 = infinite
    reconnect_time_wait: int = 2  # seconds between reconnect attempts
    ping_interval: int = 20  # seconds between pings
    max_outstanding_pings: int = 2  # max unanswered pings before disconnect
    connect_timeout: int = 10  # seconds
    allow_reconnect: bool = True
    dont_randomize: bool = True
    verbose: bool = True  # Enable verbose NATS protocol logging
    pedantic: bool = True  # Enable pedantic mode for stricter error handling


def get_nats_url(base_url: str, access_token: Optional[str] = None) -> str:
    """Get NATS WebSocket URL from API base URL

    Args:
        base_url: The API base URL (e.g. https://controller.example.com)
        access_token: Optional access token (determines wss vs ws)

    Returns:
        NATS WebSocket URL (wss:// if token present, ws:// otherwise)
    """
    parsed_url = urllib.parse.urlparse(base_url)
    hostname = parsed_url.hostname

    if access_token:
        return f"wss://{hostname}:443/api/nats"
    else:
        return f"ws://{hostname}:80/api/nats"


def build_nats_connect_kwargs(
    nats_url: str,
    access_token: Optional[str] = None,
    config: Optional[NatsConnectionConfig] = None,
    disconnected_cb: Optional[Callable] = None,
    reconnected_cb: Optional[Callable] = None,
    error_cb: Optional[Callable] = None,
    closed_cb: Optional[Callable] = None,
) -> dict:
    """Build connection kwargs for nats.connect()

    Args:
        nats_url: The NATS WebSocket URL
        access_token: Optional access token for authentication
        config: Connection configuration (uses defaults if None)
        disconnected_cb: Async callback for disconnect events
        reconnected_cb: Async callback for reconnect events
        error_cb: Async callback for error events
        closed_cb: Async callback for connection closed events

    Returns:
        Dictionary of kwargs for nats.connect()
    """
    if config is None:
        config = NatsConnectionConfig()

    connect_kwargs = {
        "servers": [nats_url],
        "max_reconnect_attempts": config.max_reconnect_attempts,
        "reconnect_time_wait": config.reconnect_time_wait,
        "ping_interval": config.ping_interval,
        "max_outstanding_pings": config.max_outstanding_pings,
        "connect_timeout": config.connect_timeout,
        "allow_reconnect": config.allow_reconnect,
        "dont_randomize": config.dont_randomize,
        "verbose": config.verbose,
        "pedantic": config.pedantic,
    }

    if access_token:
        connect_kwargs["token"] = access_token

    if disconnected_cb:
        connect_kwargs["disconnected_cb"] = disconnected_cb
    if reconnected_cb:
        connect_kwargs["reconnected_cb"] = reconnected_cb
    if error_cb:
        connect_kwargs["error_cb"] = error_cb
    if closed_cb:
        connect_kwargs["closed_cb"] = closed_cb

    return connect_kwargs


async def connect_to_nats(
    base_url: str,
    access_token: Optional[str] = None,
    config: Optional[NatsConnectionConfig] = None,
    disconnected_cb: Optional[Callable] = None,
    reconnected_cb: Optional[Callable] = None,
    error_cb: Optional[Callable] = None,
    closed_cb: Optional[Callable] = None,
    context_name: str = "unknown",
) -> Optional[NatsClient]:
    """Connect to NATS server via WebSocket

    Args:
        base_url: The API base URL (e.g. https://controller.example.com)
        access_token: Optional access token for authentication
        config: Connection configuration (uses defaults if None)
        disconnected_cb: Async callback for disconnect events
        reconnected_cb: Async callback for reconnect events
        error_cb: Async callback for error events
        closed_cb: Async callback for connection closed events
        context_name: Name for logging context (e.g. cell name)

    Returns:
        NATS client connection or None if failed
    """
    try:
        nats_url = get_nats_url(base_url, access_token)

        if access_token:
            carb.log_verbose(
                f"Connecting to NATS with token at {nats_url} (context: {context_name})"
            )
        else:
            carb.log_verbose(
                f"Connecting to NATS without token at {nats_url} (context: {context_name})"
            )

        connect_kwargs = build_nats_connect_kwargs(
            nats_url=nats_url,
            access_token=access_token,
            config=config,
            disconnected_cb=disconnected_cb,
            reconnected_cb=reconnected_cb,
            error_cb=error_cb,
            closed_cb=closed_cb,
        )

        connection = await nats.connect(**connect_kwargs)
        carb.log_verbose(
            f"Connected to NATS at {connection.connected_url.netloc} (context: {context_name})"
        )
        return connection

    except Exception as ex:
        carb.log_error(f"Failed to connect to NATS: {ex} (context: {context_name})")
        return None


def create_default_error_cb(context_name: str) -> Callable:
    """Create a default error callback for NATS connections

    Args:
        context_name: Name for logging context (e.g. cell name)

    Returns:
        Async error callback function
    """

    async def error_cb(e):
        error_type = type(e).__name__
        error_str = str(e)
        if "EOF" in error_str or "unexpected" in error_str.lower():
            carb.log_warn(
                f"NATS EOF/connection error for {context_name}: {error_type} - {e}"
            )
        else:
            carb.log_error(f"NATS error ({error_type}) for {context_name}: {e}")

    return error_cb


def create_default_disconnected_cb(context_name: str) -> Callable:
    """Create a default disconnected callback for NATS connections

    Args:
        context_name: Name for logging context (e.g. cell name)

    Returns:
        Async disconnected callback function
    """

    async def disconnected_cb():
        carb.log_warn(f"Disconnected from NATS for {context_name}.")

    return disconnected_cb


def create_default_reconnected_cb(context_name: str) -> Callable:
    """Create a default reconnected callback for NATS connections

    Args:
        context_name: Name for logging context (e.g. cell name)

    Returns:
        Async reconnected callback function
    """

    async def reconnected_cb():
        carb.log_verbose(f"Reconnected to NATS for {context_name}")

    return reconnected_cb


def create_default_closed_cb(context_name: str) -> Callable:
    """Create a default closed callback for NATS connections

    Args:
        context_name: Name for logging context (e.g. cell name)

    Returns:
        Async closed callback function
    """

    async def closed_cb():
        carb.log_verbose(f"Connection to NATS is closed for {context_name}")

    return closed_cb


class NatsSubscriptionService:
    """Manages a NATS connection with automatic reconnection and resubscription.

    This service handles:
    - Connecting to NATS via WebSocket
    - Subscribing to a subject with a message handler
    - Automatic resubscription after reconnection
    - Clean disconnection and resource cleanup
    """

    def __init__(
        self,
        base_url: str,
        subject: str,
        message_handler: Callable,
        access_token: Optional[str] = None,
        config: Optional[NatsConnectionConfig] = None,
        context_name: str = "unknown",
    ):
        """Initialize the subscription manager.

        Args:
            base_url: The API base URL (e.g. https://<instance_id>.instance.wandelbots.io/api/v2)
            subject: NATS subject to subscribe to
            message_handler: Async callback for incoming messages
            access_token: Optional access token for authentication
            config: Connection configuration (uses defaults if None)
            context_name: Name for logging context
        """
        self.base_url = base_url
        self.subject = subject
        self.message_handler = message_handler
        self.access_token = access_token
        self.config = config or NatsConnectionConfig()
        self.context_name = context_name

        self.connection: Optional[NatsClient] = None
        self.subscription: Optional[NatsSubscription] = None
        self._lock = asyncio.Lock()
        self._should_be_connected = False
        self._last_message_time: Optional[float] = None

    async def connect(self) -> bool:
        """Connect to NATS and subscribe to the subject.

        Returns:
            True if connection and subscription succeeded, False otherwise
        """
        async with self._lock:
            if self.connection and self.subscription:
                carb.log_verbose(f"Already connected to NATS for {self.context_name}")
                return True

            try:
                # Create callbacks
                async def disconnected_cb():
                    carb.log_warn(f"Disconnected from NATS for {self.context_name}.")
                    if self.subscription:
                        self.subscription = None

                async def reconnected_cb():
                    carb.log_info(f"Reconnected to NATS for {self.context_name}")
                    if self._should_be_connected:
                        await self._resubscribe()

                self.connection = await connect_to_nats(
                    base_url=self.base_url,
                    access_token=self.access_token,
                    config=self.config,
                    disconnected_cb=disconnected_cb,
                    reconnected_cb=reconnected_cb,
                    error_cb=create_default_error_cb(self.context_name),
                    closed_cb=create_default_closed_cb(self.context_name),
                    context_name=self.context_name,
                )

                if not self.connection:
                    return False

                # Subscribe to subject
                await self._subscribe()
                self._should_be_connected = True
                return True

            except Exception as ex:
                carb.log_error(
                    f"Failed to connect to NATS for {self.context_name}: {ex}"
                )
                self.connection = None
                return False

    async def _subscribe(self):
        """Subscribe to the NATS subject."""
        if not self.connection:
            return

        try:
            carb.log_verbose(
                f"Subscribing to NATS subject: {self.subject} ({self.context_name})"
            )
            self.subscription = await self.connection.subscribe(
                self.subject, cb=self._wrapped_message_handler
            )
            self._last_message_time = asyncio.get_event_loop().time()
            carb.log_verbose(
                f"Successfully subscribed to {self.subject} ({self.context_name})"
            )
        except Exception as ex:
            carb.log_error(f"Failed to subscribe to {self.subject}: {ex}")
            self.subscription = None

    async def _resubscribe(self):
        """Resubscribe after reconnection."""
        try:
            # Ensure old subscription is gone
            if self.subscription:
                try:
                    await self.subscription.unsubscribe()
                except Exception as ex:
                    carb.log_error(
                        f"Error unsubscribing old subscription for {self.context_name}: {ex}"
                    )
                self.subscription = None

            await self._subscribe()
        except Exception as ex:
            carb.log_error(
                f"Failed to resubscribe after reconnect for {self.context_name}: {ex}",
                exc_info=True,
            )

    async def _wrapped_message_handler(self, msg):
        """Wrapper for the message handler that tracks message time."""
        self._last_message_time = asyncio.get_event_loop().time()

        if not msg.data:
            carb.log_warn(f"Received empty NATS message for {self.context_name}")
            return

        try:
            await self.message_handler(msg)
        except EOFError as ex:
            carb.log_warn(
                f"EOF while reading NATS message for {self.context_name}: {ex}"
            )
        except Exception as ex:
            carb.log_error(
                f"Error in message handler for {self.context_name}: {ex}", exc_info=True
            )

    async def disconnect(self):
        """Disconnect from NATS and clean up resources."""
        async with self._lock:
            self._should_be_connected = False
            carb.log_verbose(f"Disconnecting from NATS for {self.context_name}")

            if self.subscription:
                try:
                    await self.subscription.unsubscribe()
                except Exception as ex:
                    carb.log_warn(f"Error unsubscribing from NATS: {ex}")
                self.subscription = None

            if self.connection:
                try:
                    if self.connection.is_connected:
                        await self.connection.close()
                except Exception as ex:
                    carb.log_warn(f"Error closing NATS connection: {ex}")
                self.connection = None

    @property
    def is_connected(self) -> bool:
        """Check if connected to NATS."""
        return self.connection is not None and self.connection.is_connected

    @property
    def is_subscribed(self) -> bool:
        """Check if subscribed to the subject."""
        return self.subscription is not None
