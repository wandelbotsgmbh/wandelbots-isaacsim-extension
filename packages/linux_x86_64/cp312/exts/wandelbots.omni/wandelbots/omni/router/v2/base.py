import carb
import re
from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from wandelbots.omni.datatypes import Auth0Credentials, AuthProvider, EntraIDCredentials
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
    nucleus_router,
)
from wandelbots.omni.utils.auth import (
    store_auth_tokens,
    validate_request,
    get_auth_configs,
)
from wandelbots.omni.utils.base import get_versions_of_enabled_extensions
import traceback

API_TITLE = "Wandelbots NOVA - Isaac Sim Extension API"
API_DESCRIPTION = (
    "This extension enables seamless connection between NVIDIA Isaac Sim™ and Wandelbots NOVA. "
    "Wandelbots NOVA simplifies the programming of industrial robots and cobots from multiple brands, "
    "allowing users to easily configure various robot models and teach them through an intuitive interface "
    "or by leveraging their preferred programming languages via APIs. "
    "Start programming your favorite robot brands like ABB, FANUC, KUKA, Universal Robots and Yaskawa "
    "in an Omniverse simulation scene, benefitting from its realistic behaviour.\n\n"
    "The API provides comprehensive capabilities including:\n"
    "- **Stage Management**: Load, save, and manipulate USD stages\n"
    "- **Prims & Scene Graph**: Create and modify scene primitives and hierarchies\n"
    "- **Cameras**: Configure virtual cameras and viewports for visualization\n"
    "- **Motion Groups**: Define and control robot motion groups and kinematics\n"
    "- **Teaching & Trajectory**: Record, playback, and execute robot trajectories\n"
    "- **Collision Detection**: Manage collision worlds and colliders for safe path planning\n"
    "- **Nucleus Integration**: Access Omniverse Nucleus storage and collaboration features\n"
    "- **UI Control**: Interact with the Isaac Sim user interface programmatically"
)

omniservice_app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version="2.26.0",
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
omniservice_app.include_router(router=nucleus_router)


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
    credentials: Auth0Credentials | EntraIDCredentials = Body(
        ..., description="Authentication credentials from NOVA"
    ),
) -> None:
    """
    This Endpoint allows you to authenticate with Wandelbots NOVA using your access token.
    """
    try:
        # Get available auth configs
        auth_configs = get_auth_configs()

        # Validate identifier exists in auth configs
        if credentials.id not in auth_configs:
            available_identifiers = ", ".join(auth_configs.keys())
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid identifier '{credentials.id}'. Available identifiers: {available_identifiers}",
            )

        # Get auth config for the identifier
        auth_config = auth_configs[credentials.id]

        host = auth_config.domain
        provider = auth_config.provider

        if not credentials.access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="access_token is required",
            )

        if provider == AuthProvider.AUTH0:
            base_url = f"https://api.{host.replace('auth', '')}/v1/instances"
        else:
            base_url = f"https://{host}/instances"

        # Validate the token by making a request to the host
        await validate_request(credentials.access_token, base_url, None)

        # Store the access token
        token_response = {"access_token": credentials.access_token}
        store_auth_tokens(token_response, credentials.id)
        carb.log_info(
            f"Successfully authenticated and stored token for id '{credentials.id}'"
        )

    except HTTPException:
        raise
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
    <title>Wandelbots NOVA - Isaac Sim Extension API</title>
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
