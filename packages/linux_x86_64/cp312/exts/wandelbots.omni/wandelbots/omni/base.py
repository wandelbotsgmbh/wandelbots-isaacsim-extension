from fastapi import FastAPI
from wandelbots.omni.router.v2.base import omniservice_app as v2_app

omniservice_base_app = FastAPI(
    title="Wandelbots Omniservice",
    description="A microservice-based framework for managing Omniverse functionalities",
    docs_url=None,
    redoc_url=None,
)

omniservice_base_app.mount("/api/v2", v2_app)
