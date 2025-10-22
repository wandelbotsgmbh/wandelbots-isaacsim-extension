import carb
import re
from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from wandelbots.omni.datatypes import Auth0Credentials
from wandelbots.omni.router.v2 import (
    cameras_router,
    ui_router,
    prims_router,
    motion_groups_router,
    stage_router,
    teaching_router,
    trajectory_router,
    collision_world_router,
    colliders_router,
)
from wandelbots.omni.utils.auth import (
    get_auth_token,
    store_auth_token,
    validate_request,
)
from wandelbots.omni.utils.base import get_versions_of_enabled_extensions
import traceback


omniservice_app = FastAPI(
    title="Wandelbots Omniservice",
    description="A microservice-based framework for managing Omniverse functionalities",
    version="2.10.1",
    docs_url=None,
    redoc_url=None,
)

omniservice_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@omniservice_app.middleware("http")
async def log_requests(request: Request, call_next):
    # Log the HTTP method and path

    carb.log_verbose(f"{request.method} {request.url.path}")
    return await call_next(request)


@omniservice_app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    log_message = f"{request.method} {request.url.path} {exc.status_code} {exc.detail}"
    if exc.status_code >= 500:
        carb.log_verbose(traceback.format_exc())
        carb.log_error(log_message)
    else:
        carb.log_warn(log_message)
    return PlainTextResponse(exc.detail, status_code=exc.status_code)


omniservice_app.include_router(router=stage_router)
omniservice_app.include_router(router=prims_router)
omniservice_app.include_router(router=cameras_router)
omniservice_app.include_router(router=ui_router)
omniservice_app.include_router(router=teaching_router)
omniservice_app.include_router(router=motion_groups_router)
omniservice_app.include_router(router=trajectory_router)
omniservice_app.include_router(router=collision_world_router)
omniservice_app.include_router(router=colliders_router)


@omniservice_app.get("/status", status_code=status.HTTP_200_OK)
async def get_status():
    """
    This endpoint returns you the status of the service. Usually `OK` when it is running."""
    return {"status": "OK"}


@omniservice_app.get("/version", status_code=status.HTTP_200_OK)
async def get_versions():
    """
    This endpoint returns you a list of installed extensions with their version accordingly.
    """
    return get_versions_of_enabled_extensions()


@omniservice_app.post(
    "/auth/token",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="authenticate",
    responses={
        204: {"description": "Authenticated successfully"},
        400: {"description": "Authentication failed"},
    },
)
async def authenticate(
    credentials: Auth0Credentials = Body(
        ..., description="Auth0 credentials from NOVA"
    ),
) -> None:
    """
    Starting with 24.8: This endpoint allows you to authenticate via the provided access token of your NOVA instance.
    """
    try:
        protocol = "https" if credentials.is_secured else "http"
        base_url = f"{protocol}://{credentials.host}/"
        if credentials.access_token:
            token_to_validate = credentials.access_token
        else:
            token_to_validate = get_auth_token()

        await validate_request(token_to_validate, base_url)
        if token_to_validate is not None:
            host = ensure_portal_host(credentials.host)
            store_auth_token(token=token_to_validate, host=host)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to authenticate with NOVA: {e}",
        )


def ensure_portal_host(host: str) -> str:
    """Convert *.instance.*.wandelbots.io to auth.portal.*.wandelbots.io"""
    if re.match(r".*\.instance\..*\.wandelbots\.io", host):
        parts = host.split(".")
        parts[0] = "auth"
        parts[1] = "portal"
        converted_host = ".".join(parts)
        return converted_host
    return host


@omniservice_app.get("/ui", include_in_schema=False)
async def api_documentation():
    return HTMLResponse("""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">
    <title>Wandelbots Omniservice</title>
    <!-- Embed elements Elements via Web Component -->
    <script src="https://unpkg.com/@stoplight/elements/web-components.min.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/@stoplight/elements/styles.min.css">
  </head>
  <body>

    <elements-api
      apiDescriptionUrl="openapi.json"
      router="hash"
      layout="responsive"
      hideExport="true"
      hideSchemas="true"
    />

  </body>
</html>""")


@omniservice_app.get("/", include_in_schema=False)
async def root():
    return {"service": "Omniservice"}
