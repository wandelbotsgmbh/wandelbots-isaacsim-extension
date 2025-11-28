from wandelbots.omni.utils.database import (
    InMemoryDatabase,
    CredentialStore,
    InstanceStore,
)
from decouple import Config, RepositoryEnv
import os
import toml

host_database = InMemoryDatabase()
credential_store = CredentialStore()
instance_store = InstanceStore()


def find_env_file(start_path):
    for root, dirs, files in os.walk(start_path):
        if ".env" in files:
            return os.path.join(root, ".env")
    return None


def load_env() -> Config:
    start_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    env_path = find_env_file(start_path)

    if env_path is None:
        return None

    config = Config(RepositoryEnv(env_path))
    return config


def load_config(filename: str = None) -> dict:
    if filename is None:
        return {}

    start_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    config_path = os.path.join(start_path, "config", filename)
    if not os.path.isfile(config_path):
        return {}

    if config_path is None:
        return {}

    config = toml.load(config_path)
    return config
