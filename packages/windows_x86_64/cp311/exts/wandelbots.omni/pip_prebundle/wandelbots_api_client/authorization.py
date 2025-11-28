from typing import Optional

import asyncio
import aiohttp
import pydantic

class Auth0Config(pydantic.BaseModel):
    """Configuration for Auth0 authentication"""

    domain: Optional[str] = None
    client_id: Optional[str] = None
    audience: Optional[str] = None

    @classmethod
    def default(cls) -> "Auth0Config":
        """Create Auth0Config from environment variables"""
        return cls(
            domain="auth.portal.wandelbots.io",
            client_id="J7WJUi38xVQdJAEBNRT9Xw1b0fXDb4J2",
            audience="nova-api",
        )

    def is_complete(self) -> bool:
        """Check if all required fields are set and not None"""
        return bool(self.domain and self.client_id and self.audience)

    def get_validated_config(self) -> tuple[str, str, str]:
        """Get validated config values, ensuring they are not None"""
        if not self.is_complete():
            raise ValueError("Auth0 configuration is incomplete")
        return self.domain, self.client_id, self.audience


class Auth0DeviceCodeInfo(pydantic.BaseModel):
    """
    Model to store device code information.

    Attributes:
        device_code (str): The device code.
        user_code (str): The user code.
        verification_uri (str): The verification URI.
        expires_in (int): The expiration time in seconds.
        interval (int): The interval time in seconds (default is 5).
    """

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int = pydantic.Field(default=5)


class Auth0TokenInfo(pydantic.BaseModel):
    """
    Model to store token information.

    Attributes:
        access_token (str): The access token.
        refresh_token (str, optional): The refresh token.
    """

    access_token: str
    refresh_token: Optional[str] = None


class Auth0DeviceAuthorization:
    """
    Class to handle Auth0 device authorization.

    Methods:
        __init__(auth0_config: Auth0Config | None = None):
            Initializes the Auth0DeviceAuthorization instance with the given parameters.
        request_device_code():
            Requests a device code from Auth0.
        display_user_instructions():
            Displays instructions for the user to authenticate.
        poll_token_endpoint():
            Polls the token endpoint to obtain an access token.
        refresh_access_token(refresh_token: str):
            Refreshes the access token using the refresh token.
    """

    def __init__(self, auth0_config: Optional[Auth0Config] = None):
        """
        Initialize with Auth0Config from env vars or passed config.

        Args:
            auth0_config: Optional Auth0Config object. If not provided,
                         will be created using default configuration.
        """
        try:
            self.params = auth0_config or Auth0Config.default()
            if not self.params.is_complete():
                raise ValueError("Auth0 configuration is incomplete")

            domain, client_id, audience = self.params.get_validated_config()
            self.auth0_domain = domain
            self.auth0_client_id = client_id
            self.auth0_audience = audience

        except ValueError as e:
            raise ValueError(f"Error: Auth0 configuration is invalid: {e}")

        self.headers = {"content-type": "application/x-www-form-urlencoded"}
        self.device_code_info: Optional[Auth0DeviceCodeInfo] = None
        self.refresh_token: Optional[str] = None
        self.interval = 5
        self.attempts = 10

    async def request_device_code(self) -> Auth0DeviceCodeInfo:
        """
        Requests a device code from Auth0.

        Returns:
            Auth0DeviceCodeInfo: The device code information.

        Raises:
            Exception: If there is an error requesting the device code.
        """
        device_code_url = f"https://{self.auth0_domain}/oauth/device/code"
        data = {
            "client_id": self.auth0_client_id,
            "scope": "openid profile email",
            "audience": self.auth0_audience,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(device_code_url, headers=self.headers, data=data) as response:
                if response.status == 200:
                    response_data = await response.json()
                    self.device_code_info = Auth0DeviceCodeInfo(**response_data)
                    self.interval = self.device_code_info.interval
                    return self.device_code_info
                else:
                    error_data = await response.json()
                    raise Exception("Error requesting device code:", error_data)

    def get_device_code_info(self) -> Optional[Auth0DeviceCodeInfo]:
        """
        Returns the device code information.

        Returns:
            Optional[Auth0DeviceCodeInfo]: The device code information.
        """
        return self.device_code_info

    def display_user_instructions(self) -> None:
        """
        Displays instructions for the user to authenticate.

        Raises:
            Exception: If device code information is not available.
        """
        if self.device_code_info:
            verification_uri = f"{self.device_code_info.verification_uri}?user_code={self.device_code_info.user_code}"
            user_code = self.device_code_info.user_code
            print(
                f"Please visit {verification_uri} and validate the code {user_code} to authenticate."
            )
        else:
            raise Exception("Device code information is not available.")

    async def poll_token_endpoint(self):
        """
        Polls the token endpoint to obtain an access token.

        Returns:
            str: The access token.

        Raises:
            Exception: If there is an error polling the token endpoint.
        """
        if not self.device_code_info:
            raise Exception("Device code information is not available.")

        token_url = f"https://{self.auth0_domain}/oauth/token"
        token_data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": self.device_code_info.device_code,
            "client_id": self.auth0_client_id,
        }

        async with aiohttp.ClientSession() as session:
            while self.attempts > 0:
                self.attempts = self.attempts - 1
                async with session.post(token_url, headers=self.headers, data=token_data) as token_response:
                    if token_response.status == 200:
                        # If the response status is 200, it means the access token is successfully obtained.
                        response_data = await token_response.json()
                        token_info = Auth0TokenInfo(**response_data)
                        self.refresh_token = token_info.refresh_token
                        return token_info.access_token
                    elif token_response.status == 400:
                        # If the response status is 400, check the error type.
                        error_data = await token_response.json()
                        error = error_data.get("error")
                        if error == "authorization_pending":
                            # If the error is 'authorization_pending', it means the user has not yet authorized.
                            await asyncio.sleep(self.interval)
                        elif error == "slow_down":
                            # If the error is 'slow_down', it means the server requests to slow down polling.
                            self.interval += 5
                            await asyncio.sleep(self.interval)
                    elif token_response.status == 403:
                        # If the response status is 403, it means the request is forbidden, wait and retry.
                        await asyncio.sleep(self.interval)
                    else:
                        # For other status codes, raise an exception with the error details.
                        error_data = await token_response.json()
                        raise Exception("Error:", token_response.status, error_data)
        raise Exception("Error: It was not able to authenticate. Please try again.")

    async def refresh_access_token(self, refresh_token: str):
        """
        Refreshes the access token using the refresh token.

        Args:
            refresh_token (str): The refresh token.

        Returns:
            str: The new access token.

        Raises:
            Exception: If there is an error refreshing the access token.
        """
        if not refresh_token:
            raise Exception("Refresh token is not available.")

        token_url = f"https://{self.auth0_domain}/oauth/token"
        token_data = {
            "grant_type": "refresh_token",
            "client_id": self.auth0_client_id,
            "refresh_token": refresh_token,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, headers=self.headers, data=token_data) as response:
                if response.status == 200:
                    response_data = await response.json()
                    token_info = Auth0TokenInfo(**response_data)
                    return token_info.access_token
                else:
                    error_data = await response.json()
                    raise Exception("Error refreshing access token:", error_data)
