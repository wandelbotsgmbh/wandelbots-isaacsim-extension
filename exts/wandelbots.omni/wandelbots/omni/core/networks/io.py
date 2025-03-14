import json
import carb
import urllib.parse
from typing import Literal, List, final
from wandelbots.omni.utils.auth import validate_request
from .base import StreamingConnector
from wandelbots.omni.environment import host_database
from wandelbots.omni.utils.auth import get_auth_token


class IOStateConnector(StreamingConnector):
    @final
    class Configuration(StreamingConnector.Configuration):
        identifier: str
        type: Literal["IOStateConnector"] = "IOStateConnector"
        robot: str

        class Config:
            title = "IO State Connector"

    def __init__(self, configuration=Configuration):
        super().__init__(configuration=configuration)
        self._robot_configuration = host_database[
            f"robots.{self.configuration.robot}.configuration"
        ]
        self.host = self._robot_configuration["host"]
        self.controller_id = self._robot_configuration["controller_id"]
        self.cell = self._robot_configuration["cell"]
        websocket_protocol = "wss" if self._robot_configuration["is_secured"] else "ws"
        self.base_websocket_uri = f"{websocket_protocol}://{self.host}/api/v1/cells/{self.cell}/controllers/{self.controller_id}/ios/stream"

    async def check_connection(self, token: str | None):
        protocol = "https" if self._robot_configuration["is_secured"] else "http"
        base_url = f"{protocol}://{self.host}/api/v1/cells/{self.cell}/controllers/{self.controller_id}/ios/values"
        await validate_request(token, base_url)

    async def open(self, io_ids: List[str]):
        query = "ios=" + "&ios=".join(
            [urllib.parse.quote(io, safe="") for io in io_ids]
        )
        self.websocket_uri = f"{self.base_websocket_uri}?{query}"
        token = get_auth_token()
        await self._open_websocket_connection(uri=self.websocket_uri, token=token)

    async def close(self):
        await self._close_websocket_connection()

    async def receive(self):
        async with self.receive_lock:
            io_values = json.loads(await self.websocket.recv())
            return io_values["result"]["io_values"] if "result" in io_values else {}

    async def send(self):
        raise NotImplementedError

    async def _parse(self, **kwargs):
        registered_tools = kwargs.get("tools")
        for tool in registered_tools:
            if self.data:
                tool.on_io_stream_message(self.data)
            else:
                carb.log_warn("Received empty data from IO websocket stream")

    @property
    def robot(self):
        return self.configuration.robot
