from wandelbots.omni.datatypes import OAuthCredentials
from wandelbots.omni.utils.auth import Auth0Model
from wandelbots.omni.utils.base import get_versions_of_enabled_extensions
from fastapi import Body, FastAPI, Request
from fastapi import status as st
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse


from .router.v1 import (
    camera_router,
    configuration_router,
    ghost_teaching_router,
    object_router,
    robot_router,
    scene_router,
    stream_router,
    tool_router,
    ui_router,
)

omniservice_app = FastAPI(
    title="Wandelbots Omniservice",
    description="A microservice-based framework for managing Omniverse functionalities",
    version="1.43.5",
    redoc_url=None,
)
omniservice_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@omniservice_app.get("/status", status_code=st.HTTP_200_OK)
async def status():
    """
    This endpoint returns you the status of the service. Usually `OK` when its running."""
    return "OK"


@omniservice_app.get("/version", status_code=st.HTTP_200_OK)
async def get_versions():
    """
    This endpoint returns you a list of installed extensions with their version accordingly.
    """
    return get_versions_of_enabled_extensions()


@omniservice_app.post(
    "/authenticate",
    status_code=st.HTTP_204_NO_CONTENT,
    operation_id="authenticate",
    response_model=None,
)
async def authenticate(credentials: OAuthCredentials = Body()) -> None:
    """
    Starting with 24.8: This endpoint allows you to authenticate via the provided access token of your NOVA instance.

    Args:
        is_secured: bool
        access_token: str

    Returns:
        None

    """
    try:
        protocol = "https" if credentials.is_secured else "http"
        base_url = f"{protocol}://{credentials.host}/"

        if credentials.access_token:
            token_to_validate = credentials.access_token
        else:
            token_to_validate = Auth0Model.get_token()

        await Auth0Model.validate_request(token_to_validate, base_url)

        Auth0Model.store_token(token_to_validate)
    except Exception as e:
        raise ValueError(f"Can not authenticate with Wandelbots NOVA. {e}")


@omniservice_app.get("/api", include_in_schema=False)
async def api_documentation(request: Request):
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


omniservice_app.include_router(router=scene_router)
omniservice_app.include_router(router=configuration_router)
omniservice_app.include_router(router=object_router)
omniservice_app.include_router(router=camera_router)
omniservice_app.include_router(router=robot_router)
omniservice_app.include_router(router=tool_router)
omniservice_app.include_router(router=stream_router)
omniservice_app.include_router(router=ghost_teaching_router)
omniservice_app.include_router(router=ui_router)
    
    