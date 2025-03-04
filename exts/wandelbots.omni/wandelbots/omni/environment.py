from wandelbots.omni.utils.database import InMemoryDatabase, CredentialStore
from decouple import Config, RepositoryEnv
import os

host_database = InMemoryDatabase()
credential_store = CredentialStore()

def find_env_file(start_path):
    for root, dirs, files in os.walk(start_path):
        if ".env" in files:
            return os.path.join(root, ".env")
    return None

def load_env() -> Config:
    start_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    env_path = find_env_file(start_path)
    
    if env_path is None:
        raise FileNotFoundError(f"No .env file found in the directory tree. {start_path}")
    
    config = Config(RepositoryEnv(env_path))
    return config