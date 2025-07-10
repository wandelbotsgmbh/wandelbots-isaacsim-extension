import asyncio
import weakref
from typing import Awaitable, Callable
import inspect

import carb
import websockets
from tenacity import RetryCallState, retry, wait_exponential
from wandelbots.omni.utils.api import get_base_headers


def _log_retry_state(state: RetryCallState):
    carb.log_error(
        f"Connection error #{state.attempt_number} ({state.outcome.exception()}) {state.outcome}. Reconnecting in {state.upcoming_sleep}s"
    )


def _to_header_params(headers):
    signature = inspect.signature(websockets.connect)
    kwargs = {}
    # newer websockets versions only accept additional_headers
    if "additional_headers" in signature.parameters:
        kwargs["additional_headers"] = headers
    elif "extra_headers" in signature.parameters:
        kwargs["extra_headers"] = headers
    return kwargs


class ReconnectingWebsocket:
    def __init__(
        self,
        uri: str,
        on_receive: Callable[[str], Awaitable[any]],
        token: str = None,
    ):
        self._stream_lifecycle_lock = asyncio.Lock()
        self.websocket_uri = uri
        self.websocket_additional_headers = get_base_headers(token)
        self.on_receive = on_receive
        self._streaming_task: asyncio.Task = None
        self._send_task: asyncio.Task = None
        self._send_queue = asyncio.Queue()

    @property
    def streaming(self):
        return self._streaming_task is not None

    async def open(
        self,
    ):
        async with self._stream_lifecycle_lock:
            if self.streaming:
                carb.log_warn(f"{self.websocket_uri} is already open")
                return
            self._streaming_task = asyncio.create_task(self._connect())

    async def close(self):
        async with self._stream_lifecycle_lock:
            if not self.streaming:
                carb.log_warn(f"{self.websocket_uri} is already closed")
                return
            carb.log_verbose(f"Closing ws {self.websocket_uri}")
            self._streaming_task.cancel()
            self._streaming_task = None

            if self._send_task:
                self._send_task.cancel()
                self._send_task = None
            self._send_queue: asyncio.Queue[websockets.Data] = asyncio.Queue()

    @retry(
        before_sleep=_log_retry_state,
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=False,
    )
    async def _connect(self):
        carb.log_verbose(f"Opening websocket connection to {self.websocket_uri}")
        kwargs = _to_header_params(self.websocket_additional_headers)
        async with websockets.connect(self.websocket_uri, **kwargs) as websocket:
            carb.log_verbose(f"Start listening {self.websocket_uri}")
            self._send_task = asyncio.create_task(
                self._start_send_loop(weakref.ref(websocket))
            )
            while True:
                data = await websocket.recv()
                await self.on_receive(data)

    async def _start_send_loop(
        self, websocket: weakref.ReferenceType[websockets.WebSocketClientProtocol]
    ):
        while websocket:
            message = await self._send_queue.get()
            if not websocket:
                carb.log_info(f"Websocket closed. {message} will be discarded")
                return
            try:
                await websocket().send(message)
            except Exception as ex:
                carb.log_error(f"Failed to send message. {ex}")

    async def send(self, message: websockets.Data):
        await self._send_queue.put(message)
