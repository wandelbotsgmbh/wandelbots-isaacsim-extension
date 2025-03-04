import carb
import httpx

from fastapi import HTTPException
from wandelbots.omni.environment import credential_store, load_env


class Auth0Model:
    
    @staticmethod
    def get_token():
        config = load_env()
        domain = config("AUTH0_DOMAIN")
        if domain in credential_store:
            return credential_store[domain]
        return None

    @staticmethod
    def store_token(token: str):
        config = load_env()
        domain = config("AUTH0_DOMAIN")
        credential_store[domain] = token
        carb.log_info("Stored token.")

    @staticmethod
    async def validate_request(token: str | None, base_url: str):
        if token is not None:
            try:
                async with httpx.AsyncClient() as client:
                    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}

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
                        403,
                        "Authentication error: Forbidden access. You might not have the necessary permissions",
                    )
            except Exception as e:
                raise HTTPException(400, "Invalid authentication details") from e

        else:
            headers = {
                "Accept": "application/json",
            }
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(url=base_url, timeout=3, headers=headers)
                    if response.status_code != 200:
                        raise HTTPException(
                            400,
                            "Unable to ping server after establishing connection",
                        )
                except Exception as e:
                    raise HTTPException(
                        401,
                        "Unable to reach server. Check if authentication details are required",
                    ) from e

