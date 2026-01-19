from functools import cached_property
from typing import Literal
from wandelbots.omni.utils.api import ApiConfiguration
from pydantic import BaseModel, Field
from wandelbots.omni.utils.api import get_api_client_from_config
from wandelbots.omni.instances.instances_api import get_instances_api


class MotionStreamConfiguration(BaseModel):
    host: str = Field(example="127.0.0.1", description="NOVA instance origin")
    secure_connection: bool = Field(
        default=False, description="Wether connection to NOVA instance is secure"
    )
    cell: str = Field(example="cell")
    motion_group: str = Field(example="0@ur10e")
    controller: str = Field(
        default=None,
        example="ur10e",
        description="Id of controller. A of <A>@<B> is used if motion group has this format",
    )
    response_rate: int = Field(default=32, description="Response rate of motion stream")
    use_external_joint_stream: bool = Field(
        default=False,
        description="If true the robot will use the external joint stream endpoint to synchronize its articulation state with Wandelbots NOVA.",
    )

    @cached_property
    def motion_group_id(self):
        return self.motion_group.split("@")[0]

    def get_api_configuration(
        self, version: Literal["v1", "v2"] = "v2"
    ) -> ApiConfiguration:
        return ApiConfiguration(
            host=self.host,
            secure_connection=self.secure_connection,
            access_token=get_instances_api().get_auth_token_from_host(self.host),
            version=version,
        )

    def get_api_client(self):
        return get_api_client_from_config(self.get_api_configuration())
