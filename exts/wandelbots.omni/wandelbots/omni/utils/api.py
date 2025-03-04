import wandelbots_api_client as wb
from .auth import Auth0Model

def get_api_client(host: str, secure=False) -> wb.ApiClient:
    base_url = f"http{'s' if secure else ''}://{host}/api/v1"
    config = wb.Configuration(
        host=base_url,
        access_token=Auth0Model.get_token()
    )
    return wb.ApiClient(config)