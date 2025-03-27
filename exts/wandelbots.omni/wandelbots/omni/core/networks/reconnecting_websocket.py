import asyncio
from typing import Awaitable, Callable

import websockets
import carb


class DecayingTimeout:
    def __init__(self, timeout_interval=0.2, timeout_decay=0.2, max_timeout=3.0):
        """Creates a new decaying timeout which increase with each wait call

        Args:
            timeout_interval (float): Defaults to 0.2 seconds.
            timeout_decay (float): Defaults to 0.2 seconds.
            max_timeout (float): Defaults to 3.0 seconds.
        """
        self.timeout_interval = timeout_interval
        self.timeout = timeout_interval
        self.timeout_decay = timeout_decay
        self.max_timeout = max_timeout
        self.wait_count = 0

    async def wait_timeout(self):
        """Waits for current timeout time"""
        await asyncio.sleep(self.timeout)
        self.timeout = min(self.max_timeout, self.timeout + self.timeout_decay)
        self.wait_count += 1

    def reset(self):
        """Resets timeout and wait count"""
        self.timeout = self.timeout_interval
        self.wait_count = 0


class ReconnectingWebsocket:
    def __init__(
        self,
        uri: str,
        on_receive: Callable[[str], Awaitable[any]],
        timeout=DecayingTimeout(
            timeout_interval=0.2, timeout_decay=0.2, max_timeout=3.0
        ),
        token: str = None,
    ):
        self._stream_lifecycle_lock = asyncio.Lock()
        self.websocket_uri = uri
        self.websocket_additional_headers = (
            {"Authorization": f"Bearer {token}"} if token else None
        )
        self.websocket = None
        self.connection_timeout = timeout
        self.on_receive = on_receive
        self.streaming = False

    async def open(
        self,
    ):
        async with self._stream_lifecycle_lock:
            carb.log_verbose(f"Opening websocket connection to {self.websocket_uri}")
            if self.websocket:
                carb.log_warn(f"{self.websocket_uri} is already open")
                return
            self.connection_timeout.reset()
            self.websocket = await websockets.connect(
                self.websocket_uri, extra_headers=self.websocket_additional_headers
            )

    async def close(self):
        async with self._stream_lifecycle_lock:
            if not self.websocket:
                carb.log_warn(f"{self.websocket_uri} is already closed")
                return
            if self.websocket:
                carb.log_verbose(f"Closing ws {self.websocket_uri}")
                await self.websocket.close()
                self.websocket = None

    async def start_listening(self):
        self.streaming = True
        self.connection_timeout.reset()
        try:
            carb.log_verbose(f"Start listening {self.websocket_uri}")
            while self.streaming:
                try:
                    data = await self.websocket.recv()
                    await self.on_receive(data)
                except websockets.ConnectionClosedError as connection_error:
                    carb.log_error(f'Connection closed with error "{connection_error}"')
                    await self.close()
                    await self.connection_timeout.wait_timeout()
                    carb.log_error(
                        f"Reconnecting attempt {self.connection_timeout.wait_count}"
                    )
                    await self.open(self.websocket_uri)
        except websockets.ConnectionClosedOK as connection_closed:
            carb.log_verbose(f'Connection closed "{connection_closed}"')
        except websockets.ConnectionClosedError as connection_error:
            carb.log_error(f'Connection closed with error "{connection_error}"')
        finally:
            self.streaming = False
            carb.log_verbose(f"Stop listening {self.websocket_uri}")

    async def stop_listening(self):
        self.streaming = False
