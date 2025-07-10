import carb
import omni.isaac.core.utils.prims as prims_utils
import omni.isaac.core.utils.stage as stage_utils
import omni.usd
from omni.usd import duplicate_prim
from omni.usd.commands import DeletePrimsCommand
from pxr import Usd, UsdPhysics, UsdShade
from wandelbots.omni.datatypes import (
    GHOST_MATERIAL_MDL_FILE,
    SHADER_IDENTIFIER,
    GhostObject,
    GhostObjectSource,
    TCPSource,
    WSPose,
)
from wandelbots.omni.utils.prims import PrimUtils


class GhostObjectUtils:
    @staticmethod
    async def add_material_to_prim(prim: Usd.Prim):
        """
        Applies ghost material and shader to the prim given its path
        """
        stage = prim.GetStage()
        looks_path = prim.GetPath().AppendChild("Looks")
        if not stage.GetPrimAtPath(looks_path):
            stage.DefinePrim(looks_path, "Scope")

        material_path = looks_path.AppendChild("GhostMaterial")
        material = UsdShade.Material.Define(stage, material_path)

        shader_path = material_path.AppendChild("Shader")
        shader = UsdShade.Shader.Define(stage, shader_path)
        shader.SetSourceAsset(GHOST_MATERIAL_MDL_FILE, "mdl")
        shader.SetSourceAssetSubIdentifier(SHADER_IDENTIFIER)

        material.CreateSurfaceOutput().ConnectToSource(
            shader.ConnectableAPI(), "surface"
        )
        material.CreateDisplacementOutput().ConnectToSource(
            shader.ConnectableAPI(), "displacement"
        )
        material.CreateVolumeOutput().ConnectToSource(shader.ConnectableAPI(), "volume")
        UsdShade.MaterialBindingAPI(prim).Bind(
            material, UsdShade.Tokens.strongerThanDescendants
        )

    @staticmethod
    def register_ghost_object(prim: Usd.Prim, source_ghost: bool):
        """
        Registers custom data to ghost object to make them discoverable
        """
        custom_data = prim.GetCustomData()
        custom_data.setdefault("metadata", {})
        custom_data["metadata"].update({"is_ghost": True, "source_ghost": source_ghost})
        prim.SetCustomData(custom_data)

    @staticmethod
    def get_ghost_object_sources():
        ghost_objects = []
        for prim in stage_utils.traverse_stage():
            metadata = prim.GetCustomData().get("metadata", {})
            if metadata.get("is_ghost") and metadata.get("source_ghost"):
                ghost_objects.append(
                    GhostObjectSource(
                        name=prim.GetPrimPath().pathString.split("/")[-1],
                        prim_path=prim.GetPrimPath().pathString,
                    )
                )
        return ghost_objects

    async def add_ghost_object(prim_path: str, ref_pose: WSPose):
        carb.log_info(f"Add ghost object {prim_path} ref_pose={ref_pose}")
        ghost_base_path = "/".join(prim_path.split("/")[:-2])
        ghost_object_name = prim_path.split("/")[-1]
        clone_prim_path = f"{ghost_base_path}/poses/{ghost_object_name}"
        target_path = stage_utils.get_next_free_path(clone_prim_path)
        stage = omni.usd.get_context().get_stage()
        source_prim = stage.GetPrimAtPath(prim_path)

        # Find the first available tcp when traversed
        ghost_object_source_path = next(
            (
                ghost.prim_path
                for ghost in GhostObjectUtils.get_ghost_object_sources()
                if ghost.prim_path == prim_path
            ),
            None,
        )
        if ghost_object_source_path is None:
            raise ValueError("Source ghost object is not created")

        try:
            if not duplicate_prim(
                stage,
                ghost_object_source_path,
                target_path,
            ):
                raise RuntimeError(
                    f"Failed to duplicate {ghost_object_source_path} to {target_path}"
                )
        except Exception:
            raise ValueError(
                "Source ghost object is not created. Make sure that the tool is in robot workspace and the robot is created"
            )
        carb.log_info(
            f"Ghost object created at {target_path} from {ghost_object_source_path}"
        )

        # set visibility
        target_prim = stage.DefinePrim(target_path, source_prim.GetTypeName())
        target_prim.GetAttribute("visibility").Set("inherited")

        # register prim
        GhostObjectUtils.register_ghost_object(target_prim, source_ghost=False)

        # set ghost object to active TCP pose
        if ref_pose:
            PrimUtils.set_prim_pose(target_path, ref_pose)

    def get_ghost_objects() -> list[GhostObject]:
        ghost_objects: list[GhostObject] = []
        for prim in stage_utils.traverse_stage():
            metadata = prim.GetCustomData().get("metadata", {})

            # Check if the prim has metadata and 'is_ghost' object is present
            if metadata.get("is_ghost") and not metadata.get("source_ghost"):
                path = prim.GetPrimPath().pathString
                name = path.split("/")[-1]
                ws_pose = PrimUtils.get_prim_pose(path)
                robot_prim_path = GhostObjectUtils.get_robot_prim_path(prim)
                ghost_objects.append(
                    GhostObject(
                        prim_path=path,
                        name=name,
                        robot_prim_path=robot_prim_path,
                        pose=ws_pose,
                    )
                )
        return ghost_objects

    def delete_ghost_objects(prim_paths: list[str]) -> None:
        ghost_paths = {
            ghost.prim_path for ghost in GhostObjectUtils.get_ghost_objects()
        }
        valid_paths = ghost_paths.intersection(prim_paths)
        for path in valid_paths:
            prims_utils.delete_prim(path)

    def remove_physics_attributes(prim):
        # If this is a joint prim, remove it and stop further recursion
        if prim.GetTypeName() in ["PhysicsPrismaticJoint", "PhysicsFixedJoint"]:
            DeletePrimsCommand([prim.GetPath()], destructive=False).do()
            return

        # Delete all configured physics APIs
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            prim.RemoveAPI(UsdPhysics.RigidBodyAPI)
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            prim.RemoveAPI(UsdPhysics.CollisionAPI)

        # Recurse into child prims
        for child in prim.GetChildren():
            GhostObjectUtils.remove_physics_attributes(child)

    @staticmethod
    def get_robot_prim_path(prim: Usd.Prim):
        # get parent prim and fetch first robot from that workspace
        parent_workspace_prim = prim.GetParent().GetParent()
        search_prims = prims_utils.get_all_matching_child_prims(
            parent_workspace_prim.GetPrimPath().pathString,
            lambda prim_path: "tool_" not in prim_path,
        )
        for prim in search_prims:
            if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
                return prim.GetPrimPath().pathString
        carb.log_error("No robot found in the workspace")
        return None

    @staticmethod
    def get_possible_ghost_object_sources() -> list[GhostObjectSource]:
        """
        Return the prim paths of all prims that are sources for ghost objects i.e. tools.
        These ghost object sources must follow a strict predicate `tool_`.
        """
        children_prims = prims_utils.get_all_matching_child_prims(
            "/", lambda x: "tool_" in x.lower() and "ghost" not in x.lower()
        )
        return [
            GhostObjectSource(name=prim.GetPath().name, prim_path=str(prim.GetPath()))
            for prim in set(children_prims)
            if prim.GetPath().name.lower().startswith("tool_")
        ]

    def get_all_tcp_sources(base_prim_path: str = "/") -> list[TCPSource]:
        """
        Return the prim paths of all tcps that are defined in the scene which follows a strict predicate `tcp_` or 'TCP_'
        """
        children_prims = prims_utils.get_all_matching_child_prims(
            base_prim_path, lambda x: x.lower().split("/")[-1].startswith("tcp_")
        )
        flange_prims = [
            prim
            for prim in children_prims
            if "flange" in prim.GetPrimPath().pathString.lower()
        ]
        tcp_sources = []
        for prim in set(children_prims):
            name = str(prim.GetPath()).rsplit("/", 1)[-1]
            prim_path = str(prim.GetPath())
            if prim in flange_prims:
                tcp_sources.append(
                    TCPSource(
                        name=name,
                        prim_path=prim_path,
                        value=WSPose(pose=[0, 0, 0, 0, 0, 0]),
                    )
                )
                continue

            # ToDo: optimise this search workspace
            search_workspace = prim.GetParent().GetParent().GetParent()
            matching_flange_prims = prims_utils.get_all_matching_child_prims(
                search_workspace.GetPrimPath().pathString,
                lambda x: lambda x: x.lower().split("/")[-1].startswith("tcp_flange"),
            )
            if not matching_flange_prims:
                continue
            flange_prim = matching_flange_prims[0]
            rel_pose = PrimUtils.get_relative_pose(
                prim_path, flange_prim.GetPrimPath().pathString
            )
            tcp_sources.append(
                TCPSource(name=name, prim_path=prim_path, value=rel_pose, mode="normal")
            )
        return tcp_sources

    async def add_source_ghost_object(source_prim_path: str) -> None:
        """
        Create a ghost object from the prim under the specified path.
        This will clone the prim, apply the specified material and shift the origin of the prim.
        """
        source_ghost_base_path = "/".join(source_prim_path.split("/")[:-1])
        source_ghost_object_name = source_prim_path.split("/")[-1]
        stage = omni.usd.get_context().get_stage()
        stage.DefinePrim(f"{source_ghost_base_path}/source_ghosts", "Scope")
        stage.DefinePrim(f"{source_ghost_base_path}/poses", "Scope")

        clone_prim_path = (
            f"{source_ghost_base_path}/source_ghosts/{source_ghost_object_name}"
        )
        tcp_sources = GhostObjectUtils.get_all_tcp_sources(
            base_prim_path=source_prim_path
        )
        if not tcp_sources:
            raise ValueError(
                "TCP is not configured for the given tool. Source ghost cannot be created"
            )

        source_prim = stage.GetPrimAtPath(source_prim_path)
        for tcp_source in tcp_sources:
            tcp_name = tcp_source.prim_path.split("/")[-1]
            target_path = (
                stage_utils.get_next_free_path(clone_prim_path) + f"_{tcp_name}"
            )

            if not duplicate_prim(stage, source_prim_path, target_path):
                raise RuntimeError(
                    f"Failed to duplicate {source_prim_path} to {target_path}"
                )

            # fix visibility
            target_prim = stage.DefinePrim(target_path, source_prim.GetTypeName())
            target_prim.GetAttribute("visibility").Set("invisible")

            # fix transformation
            for child in target_prim.GetChildren():
                rel_pose = PrimUtils.get_relative_pose(
                    child.GetPrimPath().pathString,
                    tcp_source.prim_path,
                    mode="inverse_both",
                )
                PrimUtils.set_prim_pose(child.GetPrimPath().pathString, rel_pose)

            # add ghost material to object
            await GhostObjectUtils.add_material_to_prim(target_prim)

            # Remove all physics attributes from the prim and its children
            GhostObjectUtils.remove_physics_attributes(target_prim)

            # register prim
            GhostObjectUtils.register_ghost_object(target_prim, source_ghost=True)
