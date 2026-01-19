from dataclasses import dataclass
from typing import Literal
from carb.tokens import get_tokens_interface
import wandelbots_api_client as wb
import wandelbots_api_client.v2 as wb_v2
from wandelbots.omni.utils.base import get_current_version


@dataclass
class ApiConfiguration:
    host: str
    secure_connection: bool = False
    access_token: str = None
    version: Literal["v1", "v2"] = "v2"

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
    version: Literal["v1", "v2"] = "v2",
) -> wb.ApiClient | wb_v2.ApiClient:
    base_url = f"http{'s' if secure else ''}://{host}/api/{version}"

    def create_client() -> wb.ApiClient | wb_v2.ApiClient:
        if version == "v2":
            config = wb_v2.Configuration(host=base_url, access_token=token)
            client = wb_v2.ApiClient(configuration=config)
            return client
        else:
            config = wb.Configuration(host=base_url, access_token=token)
            client = wb.ApiClient(configuration=config)
            return client

    client = create_client()
    client.user_agent = _get_user_agent()
    return client


def get_api_client_from_config(
    config: ApiConfiguration,
) -> wb.ApiClient | wb_v2.ApiClient:
    return get_api_client(
        host=config.host,
        secure=config.secure_connection,
        token=config.access_token,
        version=config.version,
    )


def get_base_headers(access_token: str | None) -> dict:
    base_headers = {
        "User-Agent": _get_user_agent(),
    }
    if access_token:
        base_headers["Authorization"] = f"Bearer {access_token}"
    return base_headers


def _get_user_agent() -> str:
    return get_tokens_interface().resolve(
        f"isaac-sim-extension/{get_current_version()}; IsaacSim/${{app_version}}; Kit/${{kit_version_short}} ${{platform}}"
    )
