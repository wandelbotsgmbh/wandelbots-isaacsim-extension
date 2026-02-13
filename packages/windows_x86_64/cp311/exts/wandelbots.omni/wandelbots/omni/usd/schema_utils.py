import wandelbots.usd as wb_schema  # type: ignore
import carb
from pxr import Sdf, Usd, UsdPhysics
from .tcp_utils import TcpUtils


class SchemaUtils:
    @staticmethod
    def find_motion_group_tcp(motion_group: Usd.Prim) -> Usd.Prim | None:
        """
        Get the TCP prim path of a motion group.
        """
        if not motion_group.IsValid():
            return None
        if not motion_group.HasAPI(wb_schema.MotionGroupAPI):
            carb.log_warn(
                f"Motion group {motion_group.GetPath()} does not have MotionGroupAPI."
            )
            return None

        stage: Usd.Stage = motion_group.GetStage()

        child_prim: Usd.Prim
        for child_prim in stage.Traverse():
            if not child_prim.GetPath().pathString.startswith(
                motion_group.GetPath().pathString
            ):
                continue
            if TcpUtils.is_tcp(child_prim):
                return child_prim
        return None

    @staticmethod
    def find_parent_motion_group(child_prim: Usd.Prim) -> Usd.Prim | None:
        """
        Traverse up the hierarchy to find the parent motion group of a child prim.
        """
        if not child_prim.IsValid():
            return None
        if child_prim.HasAPI(wb_schema.MotionGroupAPI):
            return child_prim

        parent = child_prim.GetParent()
        if not parent:
            return None

        return SchemaUtils.find_parent_motion_group(parent)

    @staticmethod
    def find_parent_tool(child_prim: Usd.Prim) -> Usd.Prim | None:
        """
        Traverse up the hierarchy to find the parent motion group of a child prim.
        """
        if not child_prim.IsValid():
            return None
        if child_prim.HasAPI(wb_schema.ToolAPI):
            return child_prim

        parent = child_prim.GetParent()
        if not parent:
            return None

        return SchemaUtils.find_parent_tool(parent)

    @staticmethod
    def find_tool_linked_motion_group(tool_prim: Usd.Prim) -> Usd.Prim | None:
        """
        Get the motion group linked to the tool prim.
        """
        if not tool_prim.HasAPI(wb_schema.ToolAPI):
            carb.log_warn(f"Tool prim {tool_prim.GetPath()} does not have ToolAPI.")
            return None

        tool_api = wb_schema.ToolAPI.Get(tool_prim.GetStage(), tool_prim.GetPath())
        link_body_relationship: Usd.Relationship = tool_api.GetLinkBodyRel()
        link_bodies: list[Sdf.Path] = link_body_relationship.GetForwardedTargets()

        if len(link_bodies) == 0:
            carb.log_error(f"Tool prim {tool_prim.GetPath()} has no linked tool body.")
            return None
        if len(link_bodies) > 1:
            carb.log_warn(
                f"Tool prim {tool_prim.GetPath()} has multiple linked tool bodies: {link_bodies}. Using the first one."
            )

        link_body = link_bodies[0]
        linked_prims = SchemaUtils.get_joint_connected_prim_tree(
            tool_prim.GetStage().GetPrimAtPath(link_body)
        )

        linked_motion_group: Usd.Prim | None = None
        for link_chain in linked_prims:
            for prim in link_chain:
                linked_motion_group = SchemaUtils.find_parent_motion_group(prim)
                if linked_motion_group:
                    break
            # Find the first motion group in the chain
            if linked_motion_group:
                break

        return linked_motion_group

    @staticmethod
    def get_joint_connected_prim_tree(root_prim: Usd.Prim) -> list[list[Usd.Prim]]:
        """
        Traverse all linked prims starting from the root prim. A link is defined by a physics joint.
        The root prim is excluded from the result.
        """
        if not root_prim.IsValid():
            carb.log_verbose(f"Invalid root prim {root_prim.GetPath()} provided.")
            return []
        stage: Usd.Stage = root_prim.GetStage()

        scene_joints: list[UsdPhysics.Joint] = []
        child: Usd.Prim
        for child in stage.Traverse():
            if child.GetTypeName().endswith("Joint"):
                joint = UsdPhysics.Joint(child)
                if not joint.GetJointEnabledAttr().Get():
                    continue
                scene_joints.append(joint)

        visited_prims: list[str] = [root_prim.GetPath().pathString]
        prim_chains: list[list[Usd.Prim]] = []
        current_prim_chain: list[Usd.Prim] = []
        current_prim = root_prim

        while True:
            any_prim_found = False
            for joint in scene_joints:
                # Traverse joint until one joint has a body relationship that matches the current prim
                body_0_path = joint.GetBody0Rel().GetForwardedTargets()
                body_1_path = joint.GetBody1Rel().GetForwardedTargets()
                if len(body_0_path) == 0 or len(body_1_path) == 0:
                    continue

                body_0 = stage.GetPrimAtPath(body_0_path[0])
                body_1 = stage.GetPrimAtPath(body_1_path[0])

                next_prim = None
                if body_0.GetPath() == current_prim.GetPath():
                    next_prim = body_1
                elif body_1.GetPath() == current_prim.GetPath():
                    next_prim = body_0
                if not next_prim:
                    continue

                if next_prim.GetPath().pathString in visited_prims:
                    continue
                current_prim_chain.append(next_prim)
                current_prim = next_prim
                any_prim_found = True
                visited_prims.append(current_prim.GetPath().pathString)

            if any_prim_found:
                # As long as we find prims connected to the chain, we continue
                continue

            if len(current_prim_chain) > 0:
                prim_chains.append(current_prim_chain)
            else:
                # If we didn't find any prims, we can stop
                break

            current_prim_chain = []
            current_prim = root_prim

        return prim_chains

    @staticmethod
    def get_flange_tcp_from_tool_tcp(tool_tcp: Usd.Prim) -> Usd.Prim | None:
        if not tool_tcp.IsValid():
            carb.log_error(f"Invalid tool TCP prim {tool_tcp.GetPath()} provided.")
            return None
        tool_prim = SchemaUtils.find_parent_tool(tool_tcp)
        if not tool_prim:
            carb.log_error(
                f"Tool prim not found for TCP {tool_tcp.GetPath()}. Cannot find tool. Make sure the tcp prim has a parent with the ToolAPI applied."
            )
            return None
        motion_group_prim = SchemaUtils.find_tool_linked_motion_group(tool_prim)
        if not motion_group_prim:
            carb.log_error(
                f"Motion group not found for tool {tool_prim.GetPath()}. Cannot find motion group. Make sure the tool is physically linked to a configured motion group prim and the tool has its rigid body linked."
            )
            return None
        return SchemaUtils.find_motion_group_tcp(motion_group_prim)

    @staticmethod
    def list_motion_group_tools(motion_group: Usd.Prim) -> list[Usd.Prim]:
        if not motion_group.IsValid():
            return []
        stage: Usd.Stage = motion_group.GetStage()

        tool_prims: list[Usd.Prim] = []
        tool_prim: Usd.Prim
        for tool_prim in stage.Traverse():
            if not tool_prim.HasAPI(wb_schema.ToolAPI):
                continue
            linked_motion_group = SchemaUtils.find_tool_linked_motion_group(tool_prim)
            if (
                linked_motion_group
                and linked_motion_group.GetPath() == motion_group.GetPath()
            ):
                tool_prims.append(tool_prim)

        return tool_prims
