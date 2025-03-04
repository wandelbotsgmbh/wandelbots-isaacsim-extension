import asyncio
import json
import carb
from typing import Literal, final, List, Optional

import httpx

from .base import StreamingConnector
import omni.isaac.core.utils.stage as stage_utils

from wandelbots.omni.utils.prim_utils import PrimUtils


class PoseTracker(StreamingConnector):
    @final
    class Configuration(StreamingConnector.Configuration):
        identifier: str
        type: Literal["PoseTracker"] = "PoseTracker"
        host: str = "localhost"
        port: int = 8211
        state_rate: Optional[int] = 500

        class Config:
            title = "Pose Tracker"

    def __init__(self, configuration=Configuration):
        super().__init__(configuration=configuration)
        self.websocket_protocol = "ws"
        self.websocket_uri = f"{self.websocket_protocol}://{self.configuration.host}:{self.configuration.port}/streaming/pose_tracker"
        self.prim_paths: List[str] = []
        self.state_rate = self.configuration.state_rate / 1000

    async def check_connection(self):
        self.base_url = (
            f"http://{self.configuration.host}:{self.configuration.port}/status"
        )
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url=self.base_url, timeout=3)
                if response.status_code != 200:
                    raise ConnectionError(
                        "Unable to receive correct status response from Omniverse API"
                    )
        except Exception as e:
            raise ConnectionError(
                f"Unable to connect with omniverse API on {self.configuration.host}:{self.configuration.port}"
            ) from e

    async def open(self):
        await self._open_websocket_connection(uri=self.websocket_uri)

    async def close(self):
        await self._close_websocket_connection()

    async def start_stream(self, prim_paths: List[str]):
        await asyncio.sleep(1)
        self.prim_paths = prim_paths
        await super().start_stream()

    async def stop_stream(self):
        await asyncio.sleep(self.state_rate)
        await super().stop_stream()

    async def receive(self):
        all_poses = {}
        all_prim_paths = [
            prim.GetPrimPath().pathString for prim in stage_utils.traverse_stage()
        ]
        if set(self.prim_paths).issubset(all_prim_paths):
            for prim_path in self.prim_paths:
                pose = PrimUtils.get_pose(prim_path)
                await asyncio.sleep(self.state_rate)
                all_poses.update({prim_path: pose.pose})
                carb.log_info(all_poses)
        else:
            carb.log_warn("Prim paths not found in the scene for tracking")
        return json.dumps({"number": self.prim_paths})

    async def send(self, message: str):
        for connection in self.connections:
            await connection.send_json(message)

    async def _parse(self, **kwargs):
        raise NotImplementedError
