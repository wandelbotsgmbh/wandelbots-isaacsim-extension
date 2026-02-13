import json
import os
import time
from dataclasses import dataclass, field
from collections.abc import MutableMapping
import omni.kit.app
import carb
from wandelbots.omni.instances.models import NOVACustomInstance

manager = omni.kit.app.get_app().get_extension_manager()
ext_path = manager.get_extension_path_by_module("wandelbots.omni")


class BaseStore:
    def __init__(self, file_name: str, folder_name: str = "wandelbots"):
        self._file_name = file_name
        self._folder_name = folder_name
        self._file_path = self._get_or_create_file_path(self._file_name)
        self._data = self.load_data() or {}

    def _get_or_create_file_path(
        self, file_name: str, folder_name: str = "wandelbots"
    ) -> str:
        data_path = carb.tokens.get_tokens_interface().resolve("${data}")
        file_path = os.path.join(data_path, folder_name, file_name)
        if not os.path.exists(file_path):
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w") as f:
                json.dump({}, f)
            carb.log_verbose(f"File created at {file_path}")
        return file_path

    def load_data(self):
        if not os.path.exists(self._file_path):
            carb.log_verbose(
                f"Credential file not found at {self._file_path}, returning empty data."
            )
            return {}
        with open(self._file_path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                carb.log_error(f"Failed to decode JSON from {self._file_path}")
                return {}

    def save_data(self):
        try:
            with open(self._file_path, "w") as f:
                json.dump(self._data, f)
        except Exception as e:
            raise IOError(f"Could not read write: {str(e)}")

    def clear(self):
        carb.log_verbose("Clearing credential store")
        self._data.clear()
        self.save_data()


@dataclass
class InMemoryDatabase(MutableMapping):
    data: dict = field(default_factory=dict)

    def __getitem__(self, key):
        current_node = self.data
        parts = key.split(".")
        for part in parts:
            current_node = current_node.get(part, {})
        return current_node

    def __setitem__(self, key, value):
        parts = key.split(".")
        current_node = self.data
        for part in parts[:-1]:
            current_node = current_node.setdefault(part, {})
        current_node[parts[-1]] = value

    def __delitem__(self, key):
        parts = key.split(".")
        current_node = self.data
        for part in parts[:-1]:
            current_node = current_node[part]
        del current_node[parts[-1]]

    def _is_dict_empty_or_none(value: dict) -> bool:
        return not value

    def __contains__(self, key):
        current_node = self.data
        parts = key.split(".")
        current_node_found = not InMemoryDatabase._is_dict_empty_or_none(current_node)
        if not current_node_found:
            return False

        for part in parts:
            current_node = current_node.get(part, {})
            if InMemoryDatabase._is_dict_empty_or_none(current_node):
                return False
        return current_node_found

    def __iter__(self):
        return iter(self.data)

    def __len__(self):
        return len(self.data)

    def __repr__(self):
        return repr(self.data)

    def clear_all(self):
        carb.log_verbose("Clearing host database")
        self.data.clear()


@dataclass
class InstanceStore(BaseStore):
    def __init__(self):
        super().__init__(file_name="instances.json")

    def store_instance(self, instance: NOVACustomInstance):
        if not isinstance(instance, NOVACustomInstance):
            raise TypeError("Expected instance to be of type NOVACustomInstance")
        if not instance.host:
            raise ValueError("Instance must have a host defined")

        self._data[instance.host] = instance.model_dump()
        self.save_data()

    def remove_instance(self, instance_id: str):
        """
        Remove an instance by its ID.

        Args:
            instance_id: Unique identifier of the instance to remove
        """
        carb.log_info(f"Removing instance with ID: {instance_id}")
        if instance_id in self._data:
            del self._data[instance_id]
            self.save_data()
            carb.log_info(f"Instance {instance_id} removed successfully.")
        else:
            carb.log_warn(f"Instance {instance_id} not found in store.")

    def get_instances(self) -> list[NOVACustomInstance]:
        instances = []
        for host, instance_data in self._data.items():
            try:
                instance = NOVACustomInstance(**instance_data)
                instances.append(instance)
            except Exception as e:
                carb.log_error(
                    f"Error creating NOVACustomInstance from data for host {host}: {e}"
                )
        return instances

    def cleanup_instances(self):
        """Remove instances with missing or invalid data."""
        invalid_hosts = []
        for host, instance_data in self._data.items():
            try:
                NOVACustomInstance(**instance_data)
            except Exception as e:
                carb.log_warn(
                    f"Invalid instance data for host {host}, marking for removal: {e}"
                )
                invalid_hosts.append(host)

        for host in invalid_hosts:
            del self._data[host]
            carb.log_info(f"Removed invalid instance for host: {host}")

        if invalid_hosts:
            self.save_data()
            carb.log_info(f"Cleaned up {len(invalid_hosts)} invalid instances.")


@dataclass
class CredentialStore:
    """Store credentials using carb settings with auth_config_id as key."""

    SETTINGS_PREFIX = "/persistent/wandelbots/credentials"

    def __init__(self):
        self._settings = carb.settings.get_settings()

    def _get_config_path(self, auth_config_id: str) -> str:
        """Get the settings path for an auth config."""
        # Sanitize auth_config_id to be settings-path friendly
        sanitized_name = auth_config_id.replace(".", "_").replace(":", "_")
        return f"{self.SETTINGS_PREFIX}/{sanitized_name}"

    def _get_token_data(self, auth_config_id: str) -> dict:
        """Get token data dict for an auth config."""
        path = self._get_config_path(auth_config_id)
        token_json = self._settings.get(f"{path}/data")
        if token_json:
            try:
                return json.loads(token_json)
            except json.JSONDecodeError:
                carb.log_error(
                    f"Failed to decode token data for auth config {auth_config_id}"
                )
                return {}
        return {}

    def _set_token_data(self, auth_config_id: str, data: dict):
        """Set token data dict for an auth config."""
        path = self._get_config_path(auth_config_id)
        self._settings.set(f"{path}/data", json.dumps(data))

    def store_token(self, auth_config_id: str, token: str, expires_in: int = None):
        """Store access token for an auth config."""
        if not auth_config_id or not token:
            raise ValueError("Auth name and token must be provided.")

        data = self._get_token_data(auth_config_id)
        data["access_token"] = token

        # Store expiration timestamp if expires_in is provided
        if expires_in is not None:
            expiration_time = time.time() + expires_in
            data["expires_at"] = expiration_time

        self._set_token_data(auth_config_id, data)
        carb.log_verbose(f"Stored access token for auth config: {auth_config_id}")

    def store_refresh_token(self, auth_config_id: str, refresh_token: str):
        """Store refresh token for an auth config."""
        if not auth_config_id or not refresh_token:
            raise ValueError("Auth name and refresh token must be provided.")

        data = self._get_token_data(auth_config_id)
        data["refresh_token"] = refresh_token
        self._set_token_data(auth_config_id, data)
        carb.log_verbose(f"Stored refresh token for auth config: {auth_config_id}")

    def get_token(self, auth_config_id: str) -> str:
        """Get access token for an auth config."""
        if not auth_config_id:
            raise ValueError("Auth name must be provided.")

        data = self._get_token_data(auth_config_id)
        if not data:
            carb.log_verbose(
                f"No token found for {auth_config_id}. Authentication required."
            )
            return None

        return data.get("access_token")

    def get_refresh_token(self, auth_config_id: str) -> str:
        """Get refresh token for an auth config."""
        if not auth_config_id:
            raise ValueError("Auth name must be provided.")

        data = self._get_token_data(auth_config_id)
        return data.get("refresh_token")

    def get_token_expiration(self, auth_config_id: str) -> float | None:
        """Get token expiration timestamp for an auth config."""
        if not auth_config_id:
            raise ValueError("Auth name must be provided.")

        data = self._get_token_data(auth_config_id)
        return data.get("expires_at")

    def is_token_expired(
        self, auth_config_id: str, grace_period_in_seconds: int = 300
    ) -> bool:
        """Check if token is expired or will expire soon."""
        expires_at = self.get_token_expiration(auth_config_id)
        if expires_at is None:
            # No expiration info - assume not expired
            return False

        # Consider expired if current time + buffer >= expiration time
        return (time.time() + grace_period_in_seconds) >= expires_at

    def remove_token(self, auth_config_id: str):
        """Remove all tokens for an auth config."""
        if not auth_config_id:
            raise ValueError("Auth name must be provided.")

        path = self._get_config_path(auth_config_id)
        if self._settings.get(f"{path}/data"):
            self._settings.set(f"{path}/data", "")
            carb.log_info(f"Token removed for auth config: {auth_config_id}")
        else:
            carb.log_warn(
                f"No token found for auth config: {auth_config_id} - nothing to remove"
            )

    def clear(self):
        """Clear all stored credentials."""
        carb.log_verbose("Clearing credential store")
        # Get all credential paths and remove them
        all_settings = self._settings.get_settings_dictionary(self.SETTINGS_PREFIX)
        if all_settings:
            for key in all_settings:
                self._settings.set(f"{self.SETTINGS_PREFIX}/{key}", "")
        carb.log_info("All credentials cleared")
