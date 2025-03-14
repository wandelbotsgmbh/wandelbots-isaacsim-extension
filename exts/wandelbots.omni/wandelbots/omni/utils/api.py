import wandelbots_api_client as wb

def get_api_client(host: str, secure=False, token: str | None = None) -> wb.ApiClient:
    base_url = f"http{'s' if secure else ''}://{host}/api/v1"
    config = wb.Configuration(
        host=base_url,
        access_token=token
    )
    return wb.ApiClient(
        configuration=config,
        header_name="X-Wandelbots-Client",
        header_value="isaac-sim-extension"
    )