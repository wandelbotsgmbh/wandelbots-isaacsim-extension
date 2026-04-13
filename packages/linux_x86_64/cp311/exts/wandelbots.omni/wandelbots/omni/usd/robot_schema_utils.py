import usd.schema.isaac.robot_schema as rs
from pxr import Usd
import re


class RobotSchemaUtils:
    @staticmethod
    def is_robot_link(prim: Usd.Prim) -> bool:
        """
        Matches on LinkAPI or link_<number>
        """
        return (
            prim.HasAPI(rs.Classes.LINK_API.value)
            or re.match(r"link_\d+$", prim.GetPath().pathString.split("/")[-1])
            is not None
        )

    @staticmethod
    def get_link_parent(prim: Usd.Prim) -> Usd.Prim | None:
        current_prim = prim
        while current_prim:
            if RobotSchemaUtils.is_robot_link(current_prim):
                return current_prim
            current_prim = current_prim.GetParent()
        return None

    @staticmethod
    def get_motion_group_links_ordered(motion_group_prim: Usd.Prim) -> list[Usd.Prim]:
        stage: Usd.Stage = motion_group_prim.GetStage()
        if motion_group_prim.HasAPI(rs.Classes.ROBOT_API.value):
            robot_links: Usd.Relationship = motion_group_prim.GetRelationship(
                rs.Relations.ROBOT_LINKS.name
            )
            link_targets = robot_links.GetForwardedTargets()
            return [stage.GetPrimAtPath(link_path) for link_path in link_targets]
        else:
            link_prims = [
                prim
                for prim in Usd.PrimRange(motion_group_prim)
                if RobotSchemaUtils.is_robot_link(prim)
            ]
            return sorted(link_prims, key=lambda prim: prim.GetPath().pathString)
