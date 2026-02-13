"""Generic OAuth2 Device Code Flow implementation."""

import aiohttp
import asyncio
import carb
from typing import Optional
from abc import ABC, abstractmethod


class DeviceCodeAuth(ABC):
    """Base class for device code flow authentication."""

    def __init__(
        self,
        domain: str,
        token_endpoint: str,
        device_endpoint: str,
        client_id: str,
        scope: str = "offline_access",
    ):
        """
        Initialize device code authenticator.

        Args:
            domain: Authorization server domain
            token_endpoint: Token endpoint URL
            device_endpoint: Device code endpoint URL
            client_id: OAuth client ID
            scope: OAuth scopes (include offline_access for refresh token)
        """
        self.domain = domain
        self.token_endpoint = token_endpoint
        self.device_endpoint = device_endpoint
        self.client_id = client_id
        self.scope = scope

    async def request_device_code(self) -> dict[str, any]:
        """
        Request device code from the authorization server.

        Returns:
            Device code response with verification_uri, user_code, etc.
        """
        data = await self._build_device_code_request()

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.device_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    carb.log_error(f"Device code request failed: {error_text}")
                    response.raise_for_status()

                return await response.json()

    @abstractmethod
    async def _build_device_code_request(self) -> dict[str, str]:
        """Build the device code request data. Override in subclasses."""
        pass

    async def poll_token_endpoint(
        self, device_code: str, interval: int = 5, expires_in: int = 900
    ) -> dict[str, str]:
        """
        Poll the token endpoint until authorization is complete.

        Args:
            device_code: Device code from request_device_code
            interval: Polling interval in seconds
            expires_in: Expiration time in seconds

        Returns:
            Token response with access_token, refresh_token, etc.
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
                        "client_id": self.client_id,
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
            refresh_token: Refresh token

        Returns:
            New token response
        """
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self.client_id,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    carb.log_error(f"Token refresh failed: {error_text}")
                    response.raise_for_status()

                return await response.json()


class EntraIDAuth(DeviceCodeAuth):
    """Microsoft Entra ID (Azure AD) device code flow authenticator."""

    def __init__(
        self,
        client_id: str,
        tenant_id: str,
        domain: str,
        scope: str = "openid profile email offline_access",
    ):
        """
        Initialize Entra ID authenticator.

        Args:
            client_id: Application (client) ID
            tenant_id: Entra ID tenant ID
            domain: Entra ID domain
            scope: OAuth scopes
        """
        base_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0"
        super().__init__(
            token_endpoint=f"{base_url}/token",
            device_endpoint=f"{base_url}/devicecode",
            client_id=client_id,
            domain=domain,
            scope=scope,
        )

    async def _build_device_code_request(self) -> dict[str, str]:
        return {"client_id": self.client_id, "scope": self.scope}


class Auth0Auth(DeviceCodeAuth):
    """Auth0 device code flow authenticator."""

    def __init__(
        self,
        client_id: str,
        domain: str,
        audience: Optional[str] = None,
        scope: str = "openid profile email offline_access",
    ):
        """
        Initialize Auth0 authenticator.

        Args:
            client_id: Auth0 client ID
            domain: Auth0 domain (e.g., "your-domain.auth0.com")
            audience: API audience (optional)
            scope: OAuth scopes
        """
        base_url = f"https://{domain}"
        self.audience = audience
        super().__init__(
            domain=domain,
            token_endpoint=f"{base_url}/oauth/token",
            device_endpoint=f"{base_url}/oauth/device/code",
            client_id=client_id,
            scope=scope,
        )

    async def _build_device_code_request(self) -> dict[str, str]:
        data = {"client_id": self.client_id, "scope": self.scope}
        if self.audience:
            data["audience"] = self.audience
        return data
