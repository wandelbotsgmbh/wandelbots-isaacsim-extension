import carb
import httpx

from fastapi import HTTPException
from wandelbots.omni.environment import credential_store, load_env
from nova.auth.auth_config import Auth0Config
from nova.auth.authorization import Auth0DeviceAuthorization


def get_auth_token():
    config = get_auth_config()
    storage_key = config.get_validated_config()[0]
    if storage_key in credential_store:
        carb.log_verbose(f"Retrieved stored token for {storage_key}.")
        return credential_store[storage_key]
    return None


async def poll_token_endpoint(controller: Auth0DeviceAuthorization):
    carb.log_verbose("Waiting for successful authentication.")
    token = await controller.poll_token_endpoint()
    return token


def get_device_code_info(controller: Auth0DeviceAuthorization):
    try:
        device_code_info = controller.request_device_code()
        carb.log_verbose(f"Device code info: {device_code_info}")
        return device_code_info
    except Exception as e:
        carb.log_error(f"Failed to request device code: {e}")


def store_auth_token(token: str):
    try:
        config = get_auth_config()
        storage_key = config.get_validated_config()[0]
        if storage_key in credential_store:
            return credential_store[storage_key]
        credential_store[storage_key] = token
    except Exception as e:
        carb.log_error(f"Failed to store token: {e}")


def get_auth_config():
    config = load_env()

    if config is None:
        carb.log_verbose("Use default authentication information.")
        return Auth0Config.from_env()
    auth0_domain = config("AUTH0_DOMAIN")
    auth0_client_id = config("AUTH0_CLIENT_ID")
    auth0_audience = config("AUTH0_AUDIENCE")
    carb.log_verbose("Use authentication information form .env.")
    return Auth0Config(
        domain=auth0_domain, client_id=auth0_client_id, audience=auth0_audience
    )


async def validate_request(token: str | None, base_url: str):
    if token is not None:
        try:
            async with httpx.AsyncClient() as client:
                headers = {
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    "X-Wandelbots-Client": "isaac-sim-extension",
                }

                response = await client.get(url=base_url, timeout=3, headers=headers)
                if response.status_code != 200:
                    raise HTTPException(
                        400,
                        "Unable to ping server after successfully after establishing connection",
                    )
                carb.log_info("Authentication successful")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise HTTPException(
                    401,
                    "Authentication error: Unauthorized access. Please check your credentials",
                )
            else:
                raise HTTPException(
                    e.response.status_code,
                    "Authentication error: Forbidden access. You might not have the necessary permissions",
                )
        except Exception as e:
            raise HTTPException(400, "Invalid authentication details") from e

    else:
        headers = {
            "Accept": "application/json",
            "X-Wandelbots-Client": "isaac-sim-extension",
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url=base_url, timeout=3, headers=headers)
                if response.status_code != 200:
                    raise HTTPException(
                        400,
                        "Unable to ping server after establishing connection",
                    )
            except httpx.HTTPStatusError as e:
                raise HTTPException(
                    e.response.status_code,
                    "Unable to reach server. Check if authentication details are required",
                ) from e
            except httpx.ConnectError as e:
                raise HTTPException(
                    400,
                    "Unable to reach server.",
                ) from e
