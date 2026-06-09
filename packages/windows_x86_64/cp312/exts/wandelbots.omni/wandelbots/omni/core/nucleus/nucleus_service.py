import json
from urllib.parse import urlparse
import omni.kit.app
import carb.settings
from omni.kit.window.filepicker.extension import NUCLEUS_SERVER_ADDED_GLOBAL_EVENT
import omni.client
from omni.kit.window.content_browser import get_content_instance
from omni.kit.window.filepicker.collections.filesystem_collection import (
    FileSystemCollectionItem,
)
from pydantic import BaseModel, Field, field_validator

_CARB_TOKENS_KEY = "/persistent/exts/wandelbots.omni/nucleus/api_tokens"


class NucleusServerModel(BaseModel):
    name: str = Field(description="Display name for the Nucleus server connection.")
    url: str = Field(
        description="Nucleus server URL, e.g. omniverse://nucleus.example.com."
    )

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("url")
    @classmethod
    def url_must_be_omniverse(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme != "omniverse":
            raise ValueError("url must use the omniverse:// protocol")
        if not parsed.hostname:
            raise ValueError("url must include a valid hostname")
        return v


class ServerUserMetadata(BaseModel):
    username: str
    groups: list[str]


class NucleusServerMetadata(BaseModel):
    name: str
    url: str
    token_auth_configured: bool | None = None
    user_metadata: ServerUserMetadata | None = None


class NucleusService:
    def __init__(self):
        self._auth_subs: dict = {}

    def add_nucleus_server(self, server: NucleusServerModel):
        omni.kit.app.queue_event(
            NUCLEUS_SERVER_ADDED_GLOBAL_EVENT,
            {"name": server.name, "url": server.url},
        )

    def list_nucleus_servers(self) -> dict[str, NucleusServerMetadata]:
        omniverse: FileSystemCollectionItem | None = (
            get_content_instance()._window._widget._view.collections.get(
                "omniverse", None
            )
        )
        if omniverse is None:
            return {}
        return {
            connection_name: NucleusServerMetadata(
                name=connection_name,
                url=connection_data.path,
                token_auth_configured=self._token_auth_configured(connection_data.path),
                user_metadata=self.get_server_user_metadata(connection_data.path)
                if self._token_auth_configured(connection_data.path)
                else None,
            )
            for connection_name, connection_data in omniverse.children.items()
            if urlparse(connection_data.path).scheme
            == "omniverse"  # Filter Add new connection item
        }

    def get_server_user_metadata(self, url: str) -> ServerUserMetadata | None:
        try:
            server_info: omni.client.ServerInfo
            _, server_info = omni.client.get_server_info(url)
            username = server_info.username
            _, groups = omni.client.get_user_groups(url, username)
            return ServerUserMetadata(username=username, groups=groups)
        except Exception as e:
            print(f"Error getting user metadata for {url}: {e}")
            return None

    def add_api_token(self, url: str, token: str):
        self._register_token_callback(url, token)
        self._persist_tokens({url: token})

    def remove_api_token(self, url: str):
        self._auth_subs.pop(url, None)
        stored = self._load_stored_tokens()
        stored.pop(url, None)
        carb.settings.get_settings().set_string(_CARB_TOKENS_KEY, json.dumps(stored))

    def remove_all_api_tokens(self):
        self._auth_subs.clear()
        carb.settings.get_settings().set_string(_CARB_TOKENS_KEY, json.dumps({}))

    def load_tokens_from_settings(self):
        for url, token in self._load_stored_tokens().items():
            carb.log_verbose(f"Restoring Nucleus API token for {url}")
            self._register_token_callback(url, token)

    def _token_auth_configured(self, url: str) -> bool:
        return self._auth_subs.get(url) is not None

    def _register_token_callback(self, url: str, token: str):
        def authenticate(omniverse_url: str):
            if omniverse_url.startswith(url):
                return (
                    "$omni-api-token",
                    token,
                )

        self._auth_subs[url] = omni.client.register_authentication_callback(
            authenticate
        )

    def _load_stored_tokens(self) -> dict[str, str]:
        raw = carb.settings.get_settings().get_as_string(_CARB_TOKENS_KEY)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _persist_tokens(self, new_tokens: dict[str, str]):
        stored = self._load_stored_tokens()
        stored.update(new_tokens)
        carb.settings.get_settings().set_string(_CARB_TOKENS_KEY, json.dumps(stored))


service = NucleusService()


def get_nucleus_service():
    return service
