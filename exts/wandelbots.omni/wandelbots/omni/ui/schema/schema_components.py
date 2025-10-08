import carb
from .widgets import SchemaComponent
import wandelbots.usd as wb_schema
from pxr import UsdPhysics, Usd
from wandelbots.omni.utils.teaching import GhostObjectUtils, TCPSource
from wandelbots.omni.utils.prims import PrimUtils


class ToolApiSchema(SchemaComponent):
    def __init__(self):
        super().__init__("Tool", wb_schema.ToolAPI)

    def can_add(self, prim):
        return all(
            [
                super().can_add(prim),
                not prim.HasAPI(wb_schema.MotionGroupAPI),
                not prim.HasAPI(wb_schema.GhostObjectAPI),
            ]
        )


class MotionGroupApiSchema(SchemaComponent):
    def __init__(self):
        super().__init__(
            "Motion Group",
            wb_schema.MotionGroupAPI,
            [
                "enabled",
                "cell",
                "controller",
                "motionGroup",
                "externalJointStream",
                "host",
                "secure",
                "responseRate",
            ],
        )

    def can_add(self, prim):
        return all(
            [
                super().can_add(prim),
                prim.HasAPI(UsdPhysics.ArticulationRootAPI),
                not prim.HasAPI(wb_schema.GhostObjectAPI),
                not prim.HasAPI(wb_schema.ToolAPI),
            ]
        )


class GhostObjectApiSchema(SchemaComponent):
    def __init__(self):
        super().__init__("Ghost Object", wb_schema.GhostObjectAPI)

    def can_add(self, prim):
        # Ghost objects prims will be created from prims with tool api
        return False

    def can_create_ghost_object(payload: dict) -> bool:
        prim_list: list[Usd.Prim] = payload.get("prim_list", [])
        if len(prim_list) == 0 or len(prim_list) > 1:
            return False
        return prim_list[0].HasAPI(wb_schema.ToolAPI)

    def create_ghost_object_from_payload(payload: dict) -> bool:
        prim_list: list[Usd.Prim] = payload.get("prim_list", [])
        if len(prim_list) == 0 or len(prim_list) > 1:
            carb.log_warn("Cannot create a ghost object for multiple prims")
            return False

        GhostObjectApiSchema.create_ghost_object_from_prim(prim_list[0])

    def create_ghost_object_from_prim(tool_prim: Usd.Prim) -> bool:
        tcp_sources: TCPSource = GhostObjectUtils.get_all_tcp_sources(tool_prim)
        if len(tcp_sources) == 0:
            carb.log_warn(
                f"Cannot create ghost object for {tool_prim.GetPath().pathString} because no TCP source found"
            )
            return False

        selected_tcp: TCPSource = tcp_sources[0]
        if len(tcp_sources) > 1:
            carb.log_warn(
                f"Multiple TCP sources found for {tool_prim.GetPath().pathString}, using {selected_tcp.prim_path}"
            )

        pose = PrimUtils.get_prim_pose(
            selected_tcp.prim_path,
            coordinate_system="world",
        )

        GhostObjectUtils.add_ghost_object(tool_prim.GetPath().pathString, pose)
        return True


schema_components: list[SchemaComponent] = [
    ToolApiSchema(),
    MotionGroupApiSchema(),
    GhostObjectApiSchema(),
]
