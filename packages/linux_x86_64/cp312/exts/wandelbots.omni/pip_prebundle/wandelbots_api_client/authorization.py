"""OAuth2 Device Code Flow implementation for Auth0 and Entra ID."""

import aiohttp
import asyncio
from typing import Any
from abc import ABC, abstractmethod
import pydantic


class DeviceCodeFlowConfig(pydantic.BaseModel, ABC):
    """Base class for OAuth2 device code flow authentication."""

    scope: str = "openid profile email offline_access"

    class Config:
        arbitrary_types_allowed = True

    @abstractmethod
    def is_complete(self) -> bool:
        """Check if all required fields are set."""
        pass

    @property
    @abstractmethod
    def token_endpoint(self) -> str:
        """Get the token endpoint URL."""
        pass

    @property
    @abstractmethod
    def device_endpoint(self) -> str:
        """Get the device code endpoint URL."""
        pass

    @abstractmethod
    def _build_device_code_request(self) -> dict[str, str]:
        """Build the device code request data."""
        pass

    async def request_device_code(self) -> dict[str, Any]:
        """
        Request device code from the authorization server.

        Returns:
            Device code response with verification_uri, user_code, etc.

        Raises:
            ValueError: If configuration is incomplete
            Exception: If device code request fails
        """
        if not self.is_complete():
            raise ValueError(f"{self.__class__.__name__} configuration is incomplete")

        data = self._build_device_code_request()

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.device_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Device code request failed: {error_text}")

                return await response.json()

    async def poll_token_endpoint(self, device_code: str, interval: int = 5, expires_in: int = 900) -> dict[str, str]:
        """
        Poll the token endpoint until authorization is complete.

        Args:
            device_code: Device code from request_device_code
            interval: Polling interval in seconds
            expires_in: Expiration time in seconds

        Returns:
            Token response with access_token, refresh_token, etc.

        Raises:
            TimeoutError: If authorization times out
            Exception: If authorization fails
        """
        start_time = asyncio.get_event_loop().time()

        while (asyncio.get_event_loop().time() - start_time) < expires_in:
            await asyncio.sleep(interval)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.token_endpoint,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": self._get_client_id(),
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ) as response:
                    if response.status == 200:
                        return await response.json()

                    error_data = await response.json()
                    error = error_data.get("error")

                    if error == "authorization_pending":
                        continue
                    elif error == "slow_down":
                        interval += 5
                        continue
                    else:
                        raise Exception(f"Authorization failed: {error}")

        raise TimeoutError("Device authorization timed out")

    async def refresh_token(self, refresh_token: str) -> dict[str, str]:
        """
        Refresh access token using refresh token.

        Args:
            refresh_token: Refresh token from previous authentication

        Returns:
            New token response with access_token and potentially new refresh_token

        Raises:
            Exception: If token refresh fails
        """
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._get_client_id(),
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Token refresh failed: {error_text}")

                return await response.json()

    @abstractmethod
    def _get_client_id(self) -> str:
        """Get the client ID for the configuration."""
        pass


class Auth0Config(DeviceCodeFlowConfig):
    """Configuration for Auth0 device code flow authentication."""

    domain: str | None = None
    client_id: str | None = None
    audience: str | None = None

    @classmethod
    def default(cls) -> "Auth0Config":
        """Provide the default Auth0 configuration."""
        return cls(
            domain="auth.portal.wandelbots.io",
            client_id="J7WJUi38xVQdJAEBNRT9Xw1b0fXDb4J2",
            audience="nova-api",
        )

    def is_complete(self) -> bool:
        """Check if all required fields are set."""
        return bool(self.domain and self.client_id and self.audience)

    @property
    def token_endpoint(self) -> str:
        """Get the token endpoint URL."""
        if not self.domain:
            raise ValueError("Domain is not set")
        return f"https://{self.domain}/oauth/token"

    @property
    def device_endpoint(self) -> str:
        """Get the device code endpoint URL."""
        if not self.domain:
            raise ValueError("Domain is not set")
        return f"https://{self.domain}/oauth/device/code"

    def _get_client_id(self) -> str:
        """Get the client ID for the configuration."""
        if not self.client_id:
            raise ValueError("Client ID is not set")
        return self.client_id

    def _build_device_code_request(self) -> dict[str, str]:
        """Build the device code request data."""
        data = {
            "client_id": self.client_id,
            "scope": self.scope,
        }
        if self.audience:
            data["audience"] = self.audience
        return data


class EntraIDConfig(DeviceCodeFlowConfig):
    """Configuration for Microsoft Entra ID (Azure AD) device code flow authentication."""

    client_id: str | None = None
    tenant_id: str | None = None

    def is_complete(self) -> bool:
        """Check if all required fields are set."""
        return bool(self.client_id and self.tenant_id)

    @property
    def token_endpoint(self) -> str:
        """Get the token endpoint URL."""
        if not self.tenant_id:
            raise ValueError("Tenant ID is not set")
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

    @property
    def device_endpoint(self) -> str:
        """Get the device code endpoint URL."""
        if not self.tenant_id:
            raise ValueError("Tenant ID is not set")
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/devicecode"

    def _get_client_id(self) -> str:
        """Get the client ID for the configuration."""
        if not self.client_id:
            raise ValueError("Client ID is not set")
        return self.client_id

    def _build_device_code_request(self) -> dict[str, str]:
        """Build the device code request data."""
        if not self.client_id:
            raise ValueError("Client ID is not set")
        return {
            "client_id": self.client_id,
            "scope": self.scope,
        }
