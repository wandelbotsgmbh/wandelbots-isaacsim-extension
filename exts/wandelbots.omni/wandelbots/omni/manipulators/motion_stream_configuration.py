from functools import cached_property

from pydantic import BaseModel, Field


class MotionStreamConfiguration(BaseModel):
    host: str = Field(example="127.0.0.1", description="NOVA instance origin")
    secure_connection: bool = Field(
        default=False, description="Wether connection to NOVA instance is secure"
    )
    cell: str = Field(example="cell")
    motion_group: str = Field(example="0@ur10e")
    response_rate: int = Field(default=32, description="Response rate of motion stream")
    use_external_joint_stream: bool = Field(
        default=False,
        description="If true the robot will use the external joint stream endpoint to synchronize its articulation state with Wandelbots NOVA.",
    )

    @cached_property
    def motion_group_id(self):
        return self.motion_group.split("@")[0]

    @cached_property
    def controller_id(self):
        return self.motion_group.split("@")[1]

    @cached_property
    def identifier(self):
        return f"{self.host}-{self.cell}-{self.controller_id}-{self.motion_group}"
