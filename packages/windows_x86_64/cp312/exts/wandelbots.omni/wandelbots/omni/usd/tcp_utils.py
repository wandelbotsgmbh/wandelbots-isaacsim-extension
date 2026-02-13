import weakref

import carb
import isaacsim.core.utils.stage as stage_utils
import omni
import omni.kit
import omni.kit.commands
from omni.kit.property.usd import prim_selection_payload
from pxr import Sdf, Usd, UsdGeom

import wandelbots.usd as wb_schema


class TcpUtils:
    @staticmethod
    def create_tcp_from_payload(payload: dict) -> bool:
        prim_list: list[Usd.Prim] = payload.get("prim_list", [])
        if len(prim_list) == 0 or len(prim_list) > 1:
            carb.log_warn("Cannot create a TCP for multiple prims")
            return False
        TcpUtils.create_tcp_prim(prim_list[0])
        return True

    @staticmethod
    def create_tcp_prim(parent_prim: Usd.Prim, name: str = "tcp") -> Usd.Prim:
        tcp_prim: UsdGeom.Xform = wb_schema.ToolCenterPoint.Define(
            parent_prim.GetStage(),
            stage_utils.get_next_free_path(
                parent_prim.GetPath().AppendPath(Sdf.Path(name))
            ),
        )

        omni.kit.commands.execute(
            "AddXformOp",
            payload=prim_selection_payload.PrimSelectionPayload(
                weakref.ref(parent_prim.GetStage()), paths=[tcp_prim.GetPath()]
            ),
            precision=UsdGeom.XformOp.PrecisionDouble,
            rotation_order="ZYX",
            add_translate_op=True,
            add_rotate_xyz_op=False,
            add_orient_op=True,
            add_scale_op=True,
            add_transform_op=False,
            add_pivot_op=False,
        )

        return tcp_prim

    @staticmethod
    def is_tcp(prim: Usd.Prim) -> bool:
        return (
            prim.GetPath().pathString.split("/")[-1].lower().startswith("tcp_")
            or prim.GetTypeName() == "ToolCenterPoint"
        )
