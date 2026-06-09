from typing import Annotated
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator
from wandelbots.omni.core.nucleus.nucleus_service import (
    NucleusService,
    NucleusServerModel,
    NucleusServerMetadata,
    get_nucleus_service,
)

nucleus_router = APIRouter(prefix="/nucleus", tags=["Nucleus"])

NucleusServiceDep = Annotated[NucleusService, Depends(get_nucleus_service)]


class NucleusTokenModel(BaseModel):
    name: str = Field(description="Display name of the Nucleus server to authenticate.")
    token: str = Field(
        description="API token used to authenticate against the Nucleus server."
    )

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


@nucleus_router.post("/server", status_code=204)
async def add_nucleus_server(
    server: NucleusServerModel, nucleus_service: NucleusServiceDep
):
    nucleus_service.add_nucleus_server(server)
    return Response(status_code=204)


@nucleus_router.get("/servers", status_code=200)
async def list_nucleus_servers(
    nucleus_service: NucleusServiceDep,
) -> dict[str, NucleusServerMetadata]:
    return nucleus_service.list_nucleus_servers()


@nucleus_router.post("/server/token", status_code=204)
async def add_nucleus_api_token(
    token: NucleusTokenModel, nucleus_service: NucleusServiceDep
):
    server = nucleus_service.list_nucleus_servers().get(token.name)
    if server is None:
        return Response(
            content=f"Nucleus server {token.name} not found", status_code=404
        )
    nucleus_service.add_api_token(server.url, token.token)
    return Response(status_code=204)


@nucleus_router.delete("/server/token", status_code=204)
async def remove_nucleus_api_token(
    name: Annotated[str, Query()], nucleus_service: NucleusServiceDep
):
    server = nucleus_service.list_nucleus_servers().get(name)
    if server is None:
        return Response(content=f"Nucleus server {name} not found", status_code=404)
    nucleus_service.remove_api_token(server.url)
    return Response(status_code=204)


@nucleus_router.delete("/server/tokens", status_code=204)
async def remove_all_nucleus_api_tokens(nucleus_service: NucleusServiceDep):
    nucleus_service.remove_all_api_tokens()
    return Response(status_code=204)
