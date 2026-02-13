from fastapi import FastAPI
from wandelbots.omni.router.v2.base import (
    API_TITLE,
    API_DESCRIPTION,
    omniservice_app as v2_app,
)

omniservice_base_app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    docs_url=None,
    redoc_url=None,
)

omniservice_base_app.mount("/api/v2", v2_app)
