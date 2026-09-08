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
    def get_link_number(prim: Usd.Prim) -> int | None:
        """The link's position in the kinematic chain for canonically named
        links (`link_<n>`), None otherwise."""
        match = re.match(r"link_(\d+)$", prim.GetName())
        return int(match.group(1)) if match else None

    @staticmethod
    def get_motion_group_links_ordered(motion_group_prim: Usd.Prim) -> list[Usd.Prim]:
        # The robot_links relationship is skipped on purpose: the auto-applied
        # schema only targets prims carrying RigidBodyAPI, so it can hold
        # nothing but the articulation root.
        links = [
            prim
            for prim in Usd.PrimRange(motion_group_prim, Usd.TraverseInstanceProxies())
            if RobotSchemaUtils.is_robot_link(prim)
        ]

        def link_order(prim: Usd.Prim) -> tuple[int, int, str]:
            number = RobotSchemaUtils.get_link_number(prim)
            if number is None:
                # Unnumbered links sort after the numbered chain, by path.
                return (1, 0, prim.GetPath().pathString)
            return (0, number, "")

        return sorted(links, key=link_order)
