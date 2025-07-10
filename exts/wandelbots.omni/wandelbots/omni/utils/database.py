import json
import os
from dataclasses import dataclass, field
from collections.abc import MutableMapping
import omni.kit.app
import carb

manager = omni.kit.app.get_app().get_extension_manager()
ext_path = manager.get_extension_path_by_module("wandelbots.omni")


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
class CredentialStore(MutableMapping):
    data = {}

    def __getitem__(self, key):
        if key not in self.data:
            raise KeyError(key)
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def __delitem__(self, key):
        if key not in self.data:
            raise KeyError(key)
        del self.data[key]

    def __len__(self):
        return len(self.data)

    def __iter__(self):
        return iter(self.data)

    def clear_all(self):
        self.data.clear()

    def load_data(self, file_path=os.path.join(ext_path, "_temp", "credentials.json")):
        if not os.path.exists(os.path.dirname(file_path)):
            os.makedirs(os.path.dirname(file_path))
        try:
            with open(file_path, "r") as f:
                self.data = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError("Unable to find credentials")
        except Exception as e:
            raise IOError(f"Could not read credentials: {str(e)}")

    def save_data(self, file_path=os.path.join(ext_path, "_temp", "credentials.json")):
        if not os.path.exists(os.path.dirname(file_path)):
            os.makedirs(os.path.dirname(file_path))
        try:
            with open(file_path, "w") as f:
                json.dump(self.data, f)
        except Exception as e:
            raise IOError(f"Could not read write: {str(e)}")
