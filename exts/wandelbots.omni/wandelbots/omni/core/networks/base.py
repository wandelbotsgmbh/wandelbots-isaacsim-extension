import carb
from asyncio import Lock, sleep, create_task, AbstractEventLoop, get_running_loop
from dataclasses import dataclass
from typing import Any, ClassVar, Dict

import omni
import omni.timeline
import pydantic
import websockets
from wandelbots.omni.utils.auth import Auth0Model
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

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
        await sleep(self.timeout)
        self.timeout = min(self.max_timeout, self.timeout + self.timeout_decay)
        self.wait_count += 1

    def reset(self):
        """Resets timeout and wait count"""
        self.timeout = self.timeout_interval
        self.wait_count = 0


@dataclass
class ServiceConnector:
    class Configuration(pydantic.BaseModel):
        identifier: str
        host: str

    _configuration: Configuration

    def __init__(self, configuration: Configuration, **kwargs):
        super().__init__(**kwargs)
        self._configuration = configuration

    @property
    def configuration(self) -> Configuration:
        return self._configuration

    @property
    def identifier(self) -> str:
        return self._configuration.identifier

    @property
    def host(self) -> str:
        return self._configuration.host

    @classmethod
    def from_dict(cls, **kwargs):
        return cls(cls.Configuration(**kwargs))

    @property
    def to_dict(self) -> dict[str, Any]:
        return dict(self._configuration)


@dataclass
class StreamingConnector:
    class Configuration(pydantic.BaseModel):
        identifier: str
        type: str

    _configuration: Configuration
    streams_registry: ClassVar[Dict] = {}

    def __init_subclass__(cls):
        super().__init_subclass__()
        if StreamingConnector.Configuration is cls.Configuration:
            raise ValueError(
                "StreamingConnector.Configuration should not be the same as cls.Configuration"
            )
        cls.streams_registry[cls.__name__] = cls

    def __init__(self, configuration: Configuration, **kwargs):
        super().__init__(**kwargs)
        self._configuration = configuration
        self.connections = set()
        self._stream_lifecycle_lock = Lock()
        self.stream_event_loop: AbstractEventLoop = None
        self.streaming: bool = False
        self.websocket_uri = ""
        self.websocket = None
        self.data: Dict = {}
        self.timeline = omni.timeline.get_timeline_interface()
        self.receive_lock = Lock()
        self.connection_timeout = DecayingTimeout(
            timeout_interval=0.2, timeout_decay=0.2, max_timeout=3.0
        )

    @property
    def configuration(self) -> Configuration:
        return self._configuration

    async def connect(self, websocket):
        await websocket.accept()
        self.connections.add(websocket)

    async def disconnect(self, websocket):
        self.connections.remove(websocket)
    
    def connected(self) -> bool:
        return self.websocket

    async def _start_receive_loop(self, **kwargs):
        self.connection_timeout.reset()
        
        try:
            self.stream_event_loop = get_running_loop()
            while self.streaming:
                try:
                    self.data = await self.receive()
                    await self._parse(**kwargs)
                except ConnectionClosedError as connection_error:
                    carb.log_error(f'Connection closed with error "{connection_error}"')
                    await self._close_websocket_connection()
                    await self.connection_timeout.wait_timeout()
                    carb.log_error(
                        f"Reconnecting attempt {self.connection_timeout.wait_count}"
                    )
                    await self._open_websocket_connection(self.websocket_uri)
        except ConnectionClosedOK as connection_closed:
            carb.log_info(f'Connection closed "{connection_closed}"')
        except ConnectionClosedError as connection_error:
            carb.log_error(f'Connection closed with error "{connection_error}"')
        finally:
            self.streaming = False
            self.stream_event_loop = None

    async def start_stream(self, **kwargs):
        async with self._stream_lifecycle_lock:
            if self.streaming:
                carb.log_warn(f"{self.configuration.identifier} is already running")
                return 
            if not self.timeline.is_playing():
                return
            if not self.websocket:
                raise ConnectionError(
                    "Stream cannot be started without opening a connection"
                )
            self.streaming = True
            self.receive_task = create_task(self._start_receive_loop(**kwargs))
            

    async def stop_stream(self):
        async with self._stream_lifecycle_lock:
            self.streaming = False
            if self.receive_task:
                await self.receive_task
                self.receive_task = None
            self.stream_event_loop = None

    async def get_data(self):
        return self.data

    async def receive(self):
        pass

    async def send(self, **kwargs):
        pass

    async def _parse(self, **kwargs):
        pass

    async def _open_websocket_connection(self, uri):
        async with self._stream_lifecycle_lock:
            carb.log_info(f"Opening websocket connection to {uri}")
            self.connection_timeout.reset()
            token = Auth0Model.get_token()
            if token: 
                headers = {"Authorization": f"Bearer {token}"}
                self.websocket = await websockets.connect(uri, extra_headers=headers)
            else:
                self.websocket = await websockets.connect(uri)

    async def _close_websocket_connection(self):
        async with self._stream_lifecycle_lock:
            if self.websocket:
                carb.log_info("Closing websocket connection")
                await self.websocket.close()

    @property
    def identifier(self) -> str:
        return self._configuration.identifier

    @property
    def type(self) -> str:
        return self._configuration.type

    @classmethod
    def from_dict(cls, config: Dict):
        return cls.Configuration.parse_obj(config)

    @property
    def to_dict(self) -> dict[str, Any]:
        return dict(self._configuration)

    @property
    async def is_running(self) -> bool:
        async with self._stream_lifecycle_lock:
            return self.streaming
