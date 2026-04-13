from __future__ import annotations
import carb
from typing import Optional
from pydantic import BaseModel, field_validator, Field
from wandelbots_api_client.v2.models.controller_description import ControllerDescription
import wandelbots_api_client.v2 as wb_v2
from wandelbots.omni.utils.api import get_api_client
from wandelbots.omni.ui.colors import NOVAColor
from packaging.version import Version
from wandelbots_api_client import __version__ as _api_client_version

MIN_VERSION = Version(_api_client_version)


class NOVAInstance(BaseModel):
    host: str
    is_secure_connection: bool = False
    is_reachable: bool = True
    cells: Optional[list["NOVACellData"]] = None
    version: Optional[str] = None

    @field_validator("host")
    @classmethod
    def host_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("host must not be empty")
        return v

    @property
    def display_name(self) -> str:
        raise NotImplementedError("Subclasses must implement the display_name property")

    @property
    def instance_id(self) -> str:
        raise NotImplementedError("Subclasses must implement the instance_id property")

    @property
    def status(self) -> str:
        raise NotImplementedError("Subclasses must implement the status property")

    def create_api_client(self) -> wb_v2.ApiClient:
        raise NotImplementedError("Subclasses must implement the get_api_client method")

    @property
    def is_compatible(self) -> bool:
        if self.version is None:
            return False
        try:
            return self.version >= MIN_VERSION
        except Exception:
            return False

    @property
    def is_running(self) -> bool:
        return self.status.lower() == "running"

    @property
    def status_color(self):
        if not self.is_reachable:
            return NOVAColor.ERROR_MAIN.color
        elif not self.is_compatible:
            return NOVAColor.WARNING_MAIN.color
        elif self.cells:
            return NOVAColor.SUCCESS_MAIN.color
        else:
            return NOVAColor.WARNING_MAIN.color


class NOVACloudInstance(NOVAInstance):
    model_config = {"populate_by_name": True}
    auth_config_id: str
    expires_at: int
    id: str = Field(alias="instance_id")
    obsolete_at: int
    sandbox_name: str
    status_field: str = Field(alias="status")
    is_secure_connection: bool = True

    @property
    def display_name(self) -> str:
        return self.sandbox_name

    @property
    def instance_id(self) -> str:
        return self.id

    @property
    def status(self) -> str:
        return self.status_field

    @field_validator("expires_at", "id", "obsolete_at", "sandbox_name", "status_field")
    @classmethod
    def fields_must_not_be_empty(cls, v, info):
        if not v or not str(v).strip():
            raise ValueError(f"{info.field_name} must not be empty")
        return v

    def create_api_client(self, token: Optional[str] = None) -> wb_v2.ApiClient:
        try:
            carb.log_info(
                f"Creating API client for cloud instance {self.display_name} at {self.host}"
            )
            return get_api_client(
                host=self.host,
                secure=True,
                token=token,
            )
        except Exception:
            return None


class NOVACustomInstance(NOVAInstance):
    name: str

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def instance_id(self) -> str:
        return self.host

    @property
    def status(self) -> str:
        return "running" if self.is_reachable else "unreachable"

    def create_api_client(self) -> wb_v2.ApiClient:
        try:
            return get_api_client(
                host=self.host,
                secure=self.is_secure_connection,
            )
        except Exception:
            return None


class NOVAMotionGroupData(BaseModel):
    name: str
    motion_group_model_name: str


class NOVAControllerData(BaseModel):
    name: str
    description: Optional[ControllerDescription] = None
    cell_name: str
    motion_groups: list[NOVAMotionGroupData] = []


class NOVACellData(BaseModel):
    name: str
    controllers: list[NOVAControllerData] = []
