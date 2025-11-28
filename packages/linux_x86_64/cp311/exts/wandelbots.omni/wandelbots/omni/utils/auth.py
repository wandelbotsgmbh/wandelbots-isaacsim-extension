import aiohttp
import carb

from fastapi import HTTPException
from wandelbots.omni.environment import credential_store, load_config
from wandelbots_api_client.authorization import Auth0Config, Auth0DeviceAuthorization


def get_portal_api_url() -> str | None:
    config = get_auth_config()
    if config is None:
        return None

    portal_api = f"https://{config.domain.replace('auth.', 'api.')}/v1"
    return portal_api


def get_auth_token():
    config = get_auth_config()
    host = config.get_validated_config()[0]

    token = credential_store.get_token(host)

    if token is None:
        carb.log_verbose(f"No stored token found for {host}.")
        return None

    carb.log_verbose(f"Retrieved stored token for {host}.")
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


def store_auth_token(token: str, host: str = None):
    try:
        if host is None:
            config = get_auth_config()
            host = config.get_validated_config()[0]

        credential_store.store_token(host, token)
    except Exception as e:
        carb.log_error(f"Failed to store token: {e}")


def invalidate_auth_token():
    """Invalidate and remove the authentication token when 401 is received."""
    try:
        config = get_auth_config()
        host = config.get_validated_config()[0]
        credential_store.remove_token(host)
        carb.log_verbose(f"Authentication token invalidated for {host}")
    except KeyError:
        carb.log_verbose("No token found to invalidate")
    except Exception as e:
        carb.log_error(f"Failed to invalidate token: {e}")


def get_auth_config() -> Auth0Config:
    config = load_config("authentication.toml")
    environments = config.get("wandelbots", {}).get("environments", [])

    if not environments:
        return Auth0Config().default()

    if len(environments) >= 1:
        name = environments[0]["name"]
        domain = environments[0]["domain"]
        client_id = environments[0]["client_id"]
        audience = environments[0]["audience"]

        carb.log_verbose(
            f"Multiple environments found in configuration. Using the first one: {name}"
        )
        return Auth0Config(
            domain=domain,
            client_id=client_id,
            audience=audience,
        )


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
