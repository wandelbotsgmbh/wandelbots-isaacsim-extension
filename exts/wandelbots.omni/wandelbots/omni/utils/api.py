from dataclasses import dataclass
from typing import Literal

import wandelbots_api_client as wb
import wandelbots_api_client.v2 as wb_v2


@dataclass
class ApiConfiguration:
    host: str
    secure_connection: bool = False
    access_token: str = None
    version: Literal["v1", "v2"] = "v1"

    @property
    def base_url(self):
        protocol = "https" if self.secure_connection else "http"
        return f"{protocol}://{self.host}/api/{self.version}"

    @property
    def base_url_websocket(self):
        protocol = "wss" if self.secure_connection else "ws"
        return f"{protocol}://{self.host}/api/{self.version}"

    def _to_string(self) -> str:
        return f"host={self.host} secure={self.secure_connection} version={self.version} token={self.access_token}"

    def __str__(self):
        return self._to_string()

    def __repr__(self):
        return self._to_string()


def get_api_client(
    host: str,
    secure=False,
    token: str | None = None,
    version: Literal["v1", "v2"] = "v1",
) -> wb.ApiClient | wb_v2.ApiClient:
    base_url = f"http{'s' if secure else ''}://{host}/api/{version}"

    if version == "v2":
        config = wb_v2.Configuration(host=base_url, access_token=token)
        return wb_v2.ApiClient(
            configuration=config,
            header_name="X-Wandelbots-Client",
            header_value="isaac-sim-extension",
        )
    else:
        config = wb.Configuration(host=base_url, access_token=token)
        return wb.ApiClient(
            configuration=config,
            header_name="X-Wandelbots-Client",
            header_value="isaac-sim-extension",
        )


def get_api_client_from_config(
    config: ApiConfiguration,
) -> wb.ApiClient | wb_v2.ApiClient:
    return get_api_client(
        host=config.host,
        secure=config.secure_connection,
        token=config.access_token,
        version=config.version,
    )
