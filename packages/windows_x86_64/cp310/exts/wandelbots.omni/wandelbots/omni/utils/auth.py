import aiohttp
import carb
import asyncio


from typing import Literal, Optional
from pydantic import BaseModel, Field

from fastapi import HTTPException
from wandelbots.omni.environment import credential_store, load_config
from wandelbots.omni.datatypes import AuthProvider, DEFAULT_AUTH0_IDENTIFIER
from wandelbots_api_client.authorization import Auth0Config
from wandelbots.omni.utils.oauth_device_flow import (
    DeviceCodeAuth,
    EntraIDAuth,
    Auth0Auth,
)
from wandelbots.omni.utils.api import get_base_headers


class Auth0Model(BaseModel):
    """Auth0 authentication configuration."""

    provider: Literal[AuthProvider.AUTH0] = AuthProvider.AUTH0
    name: str
    identifier: str
    domain: str
    client_id: str
    audience: Optional[str] = None
    scope: str = "openid profile email offline_access"

    @staticmethod
    def default():
        config = Auth0Config().default()
        return Auth0Model(
            name="NOVA (Default)",
            identifier=DEFAULT_AUTH0_IDENTIFIER,
            domain=config.domain,
            client_id=config.client_id,
            audience=config.audience,
        )


class EntraIDModel(BaseModel):
    """Microsoft Entra ID authentication configuration."""

    provider: Literal[AuthProvider.ENTRA] = AuthProvider.ENTRA
    name: str
    identifier: str
    domain: str
    tenant_id: str
    client_id: str
    scope: str = "openid profile email offline_access"

    def default():
        raise NotImplementedError("Default EntraID config is not yet implemented.")


class AuthModel(BaseModel):
    """Union of all authentication configurations."""

    config: Auth0Model | EntraIDModel = Field(..., discriminator="provider")

    @property
    def provider(self) -> str:
        return self.config.provider

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def identifier(self) -> str:
        return self.config.identifier


def get_portal_api_url(auth_config_id: str) -> str | None:
    """Get portal API URL for Auth0 configs."""
    config = get_auth_config(auth_config_id)
    if config is None:
        return None

    if isinstance(config, EntraIDModel):
        return f"https://{config.domain}"
    elif isinstance(config, Auth0Model):
        return f"https://api.{config.domain.replace('auth.', '')}/v1"
    else:
        return None


def get_auth_token(
    auth_config_id: str, refresh_buffer_seconds: int = 300
) -> str | None:
    # Check if token is expired or will expire soon (synchronous check)
    if not credential_store.is_token_expired(auth_config_id, refresh_buffer_seconds):
        # Token is valid, return it directly from credential store
        token = credential_store.get_token(auth_config_id)
        if token is None:
            carb.log_verbose(f"No stored token found for {auth_config_id}.")
            return None

        carb.log_verbose(f"Retrieved valid stored token for {auth_config_id}.")
        return token

    # Token is expired or expiring soon, trigger async refresh
    carb.log_info(
        f"Token for {auth_config_id} is expired or expiring soon, attempting refresh..."
    )
    try:
        refreshed_token = asyncio.run(refresh_access_token(auth_config_id))
        if refreshed_token:
            return refreshed_token
        else:
            carb.log_warn(f"Failed to refresh token for {auth_config_id}")
            return None
    except Exception as e:
        carb.log_error(f"Error refreshing auth token: {e}")
        return None


async def poll_token_endpoint(
    controller: DeviceCodeAuth,
    device_code: str,
    interval: int = 5,
    expires_in: int = 900,
):
    """Poll token endpoint and store both access and refresh tokens."""
    carb.log_verbose("Waiting for successful authentication.")
    token_response = await controller.poll_token_endpoint(
        device_code, interval, expires_in
    )
    return token_response


async def get_device_code_info(controller: DeviceCodeAuth):
    """Request device code information."""
    try:
        device_code_info = await controller.request_device_code()
        carb.log_verbose(f"Device code info: {device_code_info}")
        return device_code_info
    except Exception as e:
        carb.log_error(f"Failed to request device code: {e}")
        raise


def store_auth_tokens(token_response: dict, auth_config_id: str):
    """Store access token, refresh token, and expiration time."""
    try:
        access_token = token_response.get("access_token")
        refresh_token = token_response.get("refresh_token")
        expires_in = token_response.get("expires_in")

        if access_token:
            credential_store.store_token(auth_config_id, access_token, expires_in)
            if expires_in:
                carb.log_verbose(
                    f"Stored access token for {auth_config_id} (expires in {expires_in}s)"
                )
            else:
                carb.log_verbose(f"Stored access token for {auth_config_id}")

        if refresh_token:
            credential_store.store_refresh_token(auth_config_id, refresh_token)
            carb.log_verbose(f"Stored refresh token for {auth_config_id}")

    except Exception as e:
        carb.log_error(f"{auth_config_id} Failed to store tokens: {e}")
        raise


def invalidate_auth_token(auth_config_id: str):
    """Invalidate and remove the authentication token when 401 is received."""
    try:
        credential_store.remove_token(auth_config_id)
        carb.log_verbose(f"Authentication token invalidated for {auth_config_id}")
    except KeyError:
        carb.log_verbose("No token found to invalidate")
    except Exception as e:
        carb.log_error(f"Failed to invalidate token: {e}")


async def refresh_access_token(auth_config_id: str) -> str | None:
    """
    Attempt to refresh the access token using stored refresh token.

    Returns:
        New access token if successful, None otherwise
    """
    try:
        refresh_token = credential_store.get_refresh_token(auth_config_id)
        if not refresh_token:
            carb.log_verbose(f"No refresh token found for {auth_config_id}")
            return None

        config = get_auth_config(auth_config_id)
        if not config:
            carb.log_error(f"Auth config not found: {auth_config_id}")
            return None

        controller = create_auth_controller(config)
        token_response = await controller.refresh_token(refresh_token)

        # Store new tokens
        store_auth_tokens(token_response, auth_config_id)

        carb.log_info(f"Successfully refreshed access token for {auth_config_id}")
        return token_response.get("access_token")

    except Exception as e:
        carb.log_error(f"Failed to refresh token for {auth_config_id}: {e}")
        # Invalidate tokens on refresh failure
        invalidate_auth_token(auth_config_id)
        return None


def get_auth_configs() -> dict[str, Auth0Model | EntraIDModel]:
    """Load all authentication configurations from TOML file."""
    config = load_config("authentication.toml")
    wandelbots_configs: dict = config.get("wandelbots", {})
    environments: list[dict] = wandelbots_configs.get("environments", [])

    auth_configs = {}
    auth_configs.setdefault(DEFAULT_AUTH0_IDENTIFIER, Auth0Model.default())

    for env in environments:
        provider = env.get("provider", AuthProvider.AUTH0)
        identifier = env.get("id")
        name = env.get("name")

        if not identifier:
            carb.log_warn(f"Skipping environment without identifier: {env}")
            continue

        try:
            if provider == AuthProvider.AUTH0:
                auth_configs[identifier] = Auth0Model(
                    name=name,
                    identifier=identifier,
                    domain=env.get("domain", ""),
                    client_id=env.get("client_id", ""),
                    audience=env.get("audience"),
                    scope=env.get("scope", "openid profile email offline_access"),
                )
            elif provider == AuthProvider.ENTRA:
                auth_configs[identifier] = EntraIDModel(
                    name=name,
                    identifier=identifier,
                    tenant_id=env.get("tenant_id", ""),
                    client_id=env.get("client_id", ""),
                    domain=env.get("domain", ""),
                    scope=env.get("scope", "openid profile email offline_access"),
                )
            else:
                carb.log_warn(f"Unknown provider '{provider}' for environment: {name}")
        except Exception as e:
            carb.log_error(f"Failed to parse config for {name}: {e}")

    return auth_configs


def get_auth_config(identifier: str) -> Auth0Model | EntraIDModel | None:
    """Get authentication config by name."""
    return get_auth_configs().get(identifier)


def create_auth_controller(config: Auth0Model | EntraIDModel) -> DeviceCodeAuth:
    if isinstance(config, Auth0Model):
        return Auth0Auth(
            client_id=config.client_id,
            domain=config.domain,
            audience=config.audience,
            scope=config.scope,
        )
    elif isinstance(config, EntraIDModel):
        return EntraIDAuth(
            domain=config.domain,
            client_id=config.client_id,
            tenant_id=config.tenant_id,
            scope=config.scope,
        )
    else:
        raise ValueError(f"Unsupported config type: {type(config)}")


async def _retry_request_with_refreshed_token(
    session: aiohttp.ClientSession, base_url: str, auth_config_id: str
) -> None:
    """Attempt to refresh token and retry the request."""
    carb.log_info(f"Received 401, attempting to refresh token for {auth_config_id}")
    invalidate_auth_token(auth_config_id)

    new_token = await refresh_access_token(auth_config_id)
    if new_token:
        headers = get_base_headers(new_token)
        headers["Accept"] = "application/json"
        async with session.get(url=base_url, headers=headers) as retry_response:
            if retry_response.status == 200:
                carb.log_info("Authentication successful after token refresh")
                return

    raise HTTPException(
        401,
        "Authentication error: Unauthorized access. Please check your credentials",
    )


async def validate_request(
    token: str | None, base_url: str, auth_config_id: str = None
):
    timeout = aiohttp.ClientTimeout(total=3)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = get_base_headers(token)
            headers["Accept"] = "application/json"

            async with session.get(url=base_url, headers=headers) as response:
                if response.status == 200:
                    carb.log_verbose("Authentication successful")
                    return

                if response.status == 401:
                    await _retry_request_with_refreshed_token(
                        session, base_url, auth_config_id
                    )
                    return

                raise HTTPException(
                    400,
                    "Unable to ping server after successfully establishing connection",
                )
    except aiohttp.ClientError as e:
        raise HTTPException(400, "Invalid authentication details") from e
    except HTTPException:
        raise
