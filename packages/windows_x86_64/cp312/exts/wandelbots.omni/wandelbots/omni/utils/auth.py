import aiohttp
import carb

from fastapi import HTTPException
from wandelbots.omni.environment import credential_store, load_config
from wandelbots_api_client.authorization import Auth0Config, Auth0DeviceAuthorization


DEFAULT_AUTH_CONFIG = "default"


def get_portal_api_url(auth_config_name: str) -> str | None:
    config = get_auth_config(auth_config_name)
    if config is None:
        return None

    portal_api = f"https://{config.domain.replace('auth.', 'api.')}/v1"
    return portal_api


def get_auth_token(auth_config_name: str) -> str | None:
    token = credential_store.get_token(auth_config_name)

    if token is None:
        carb.log_verbose(f"No stored token found for {auth_config_name}.")
        return None

    carb.log_verbose(f"Retrieved stored token for {auth_config_name}.")
    return token


async def poll_token_endpoint(controller: Auth0DeviceAuthorization):
    carb.log_verbose("Waiting for successful authentication.")
    token = await controller.poll_token_endpoint()
    return token


async def get_device_code_info(controller: Auth0DeviceAuthorization):
    try:
        device_code_info = await controller.request_device_code()
        carb.log_verbose(f"Device code info: {device_code_info}")
        return device_code_info
    except Exception as e:
        carb.log_error(f"Failed to request device code: {e}")


def store_auth_token(token: str, auth_config_name: str):
    try:
        credential_store.store_token(auth_config_name, token)
    except Exception as e:
        carb.log_error(f"{auth_config_name} Failed to store token: {e}")


def invalidate_auth_token(auth_config_name: str):
    """Invalidate and remove the authentication token when 401 is received."""
    try:
        credential_store.remove_token(auth_config_name)
        carb.log_verbose(f"Authentication token invalidated for {auth_config_name}")
    except KeyError:
        carb.log_verbose("No token found to invalidate")
    except Exception as e:
        carb.log_error(f"Failed to invalidate token: {e}")


def get_auth_configs() -> dict[str, Auth0Config]:
    config = load_config("authentication.toml")
    wandelbots_configs: dict = config.get("wandelbots", {})
    environments: list[dict] = wandelbots_configs.get("environments", [])

    auth_configs = {}
    for env in environments:
        domain = env.get("domain", "")
        client_id = env.get("client_id", "")
        audience = env.get("audience", "")
        name = env.get("name", f"{domain}-{client_id}-{audience}")

        auth_configs[name] = Auth0Config(
            domain=domain,
            client_id=client_id,
            audience=audience,
        )

    auth_configs.setdefault(DEFAULT_AUTH_CONFIG, Auth0Config().default())
    return auth_configs


def get_auth_config(name: str) -> Auth0Config | None:
    return get_auth_configs().get(name)


async def validate_request(token: str | None, base_url: str):
    timeout = aiohttp.ClientTimeout(total=3)

    if token is not None:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    "X-Wandelbots-Client": "isaac-sim-extension",
                }

                async with session.get(url=base_url, headers=headers) as response:
                    if response.status != 200:
                        if response.status == 401:
                            raise HTTPException(
                                401,
                                "Authentication error: Unauthorized access. Please check your credentials",
                            )
                        raise HTTPException(
                            400,
                            "Unable to ping server after successfully after establishing connection",
                        )
                    carb.log_verbose("Authentication successful")
        except aiohttp.ClientError as e:
            raise HTTPException(400, "Invalid authentication details") from e
        except HTTPException:
            raise

    else:
        headers = {
            "Accept": "application/json",
            "X-Wandelbots-Client": "isaac-sim-extension",
        }
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url=base_url, headers=headers) as response:
                    if response.status != 200:
                        raise HTTPException(
                            400,
                            "Unable to ping server after establishing connection",
                        )
        except aiohttp.ClientConnectorError as e:
            raise HTTPException(
                400,
                "Unable to reach server.",
            ) from e
        except aiohttp.ClientError as e:
            raise HTTPException(
                400,
                "Unable to reach server. Check if authentication details are required",
            ) from e
