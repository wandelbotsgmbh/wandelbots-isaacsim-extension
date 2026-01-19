import os
import carb.settings
from pydantic import BaseModel, Field
import carb

CARB_NUCLEUS_ENV_API_TOKEN = (
    "/persistent/exts/wandelbots.omni/nucleus/env_omni_api_token"
)


class NucleusTokenModel(BaseModel):
    auth_type: str = "env_api_token"
    nucleus_api_token: str = Field(
        ...,
        description="The authentication token for accessing the Nucleus server. To create a token follow https://docs.omniverse.nvidia.com/nucleus/latest/config-and-info/api_tokens.html",
        example="your_auth_token_here",
    )


class NucleusUtils:
    def remove_nucleus_api_token_environment():
        if "OMNI_USER" in os.environ:
            del os.environ["OMNI_USER"]
        if "OMNI_PASS" in os.environ:
            del os.environ["OMNI_PASS"]

        carb.settings.get_settings().set(CARB_NUCLEUS_ENV_API_TOKEN, "")

    def set_omni_api_token_environment(nucleus_api_token: str, persistent=True):
        os.environ["OMNI_USER"] = "$omni-api-token"
        os.environ["OMNI_PASS"] = nucleus_api_token

        if persistent:
            carb.settings.get_settings().set_string(
                CARB_NUCLEUS_ENV_API_TOKEN, nucleus_api_token
            )

    def set_omni_api_token_environment_from_carb_settings():
        settings_token: str | None = carb.settings.get_settings().get_as_string(
            CARB_NUCLEUS_ENV_API_TOKEN
        )
        if not settings_token:
            return
        carb.log_info("Setting Nucleus API token from carb settings")
        NucleusUtils.set_omni_api_token_environment(settings_token, persistent=False)

    def list_nucleus_authentication_setups() -> list[NucleusTokenModel]:
        if "OMNI_PASS" not in os.environ:
            return []
        return [NucleusTokenModel(nucleus_api_token="")]
