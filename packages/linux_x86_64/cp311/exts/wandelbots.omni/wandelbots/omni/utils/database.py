import json
import os
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


@dataclass
class CredentialStore(BaseStore):
    def __init__(self):
        super().__init__(file_name="credentials.json")

    def store_token(self, auth_config_name: str, token: str):
        self.load_data()
        if not auth_config_name or not token:
            raise ValueError("Auth name and token must be provided.")
        self._data[auth_config_name] = token
        self.save_data()

    def get_token(self, auth_config_name: str) -> str:
        self.load_data()
        if not auth_config_name:
            raise ValueError("Auth name must be provided.")
        if auth_config_name not in self._data:
            carb.log_verbose(
                f"No token found for {auth_config_name}. Authentication required."
            )
            return None
        return self._data[auth_config_name]

    def remove_token(self, auth_config_name: str):
        if not auth_config_name:
            raise ValueError("Auth name must be provided.")
        if auth_config_name in self._data:
            del self._data[auth_config_name]
            self.save_data()
            carb.log_info(f"Token removed for auth: {auth_config_name}")
        else:
            carb.log_warn(
                f"No token found for auth: {auth_config_name} - nothing to remove"
            )
