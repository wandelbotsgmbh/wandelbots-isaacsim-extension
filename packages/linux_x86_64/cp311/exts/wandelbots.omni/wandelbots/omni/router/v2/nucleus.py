from fastapi import APIRouter, Response
from wandelbots.omni.utils.nucleus import NucleusUtils, NucleusTokenModel

nucleus_router = APIRouter(prefix="/nucleus", tags=["Nucleus"])


@nucleus_router.post("/accounts", status_code=204)
async def set_nucleus_account(token_model: NucleusTokenModel):
    """
    Adds a nucleus authentication
    """

    NucleusUtils.set_omni_api_token_environment(
        token_model.nucleus_api_token, persistent=True
    )

    return Response(status_code=204)


@nucleus_router.get("/accounts")
async def get_nucleus_accounts() -> list[NucleusTokenModel]:
    """
    Lists all configured nucleus authentications
    """
    return NucleusUtils.list_nucleus_authentication_setups()


@nucleus_router.delete("/accounts", status_code=204)
async def clear_nucleus_accounts():
    """
    Deletes the configured nucleus authentication
    """
    NucleusUtils.remove_nucleus_api_token_environment()
    return Response(status_code=204)
