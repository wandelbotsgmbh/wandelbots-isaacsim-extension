from typing import Callable, Literal
import asyncio
import carb
import numpy as np
import omni.physx.bindings._physx as physx_bindings
import omni.physx
import omni.usd
from pxr import UsdUtils, Usd, UsdPhysics
import pydantic
import omni.timeline
from pxr import PhysicsSchemaTools
from wandelbots.omni.core.collision.collision_setup_cache import PrimCollisionSetupCache
import wandelbots.omni.core.collision.shapes as collision_shapes
from wandelbots.omni.utils.prims import Pose, PrimUtils, WSPose
from wandelbots.omni.utils.math import pose_to_matrix, matrix_to_pose
import wandelbots_api_client.v2 as wb
from wandelbots.omni.utils.api import get_api_client_from_config
from wandelbots.omni.utils.scene import SceneUtils
from wandelbots.omni.manipulators import (
    MotionStreamConfiguration,
    get_motion_group_configuration_from_prim,
    compute_forward_kinematics_chain,
)
from .utils import to_nova_collider
from wandelbots.omni.usd import SchemaUtils, RobotSchemaUtils


class TreeSweepParameters(pydantic.BaseModel):
    sweep_type: Literal["tree"]
    base_prim_path: str = pydantic.Field(
        "/World", description="Base prim path to perform the tree sweep from"
    )


class SphereSweepParameters(pydantic.BaseModel):
    sweep_type: Literal["sphere"]
    radius: float = pydantic.Field(10.0, description="Radius [stage_units]")
    position: list[float] = pydantic.Field(
        [0.0, 0.0, 0.0],
        min_length=3,
        max_length=3,
        description="Position of the sphere sweep [stage_units]",
    )
    direction: list[float] = pydantic.Field(
        [0.0, 0.0, -1.0],
        min_length=3,
        max_length=3,
        description="Direction of the sphere sweep",
    )
    max_distance: float = pydantic.Field(
        0.0, description="Sweep distance [stage_units]"
    )


class BoxSweepParameters(pydantic.BaseModel):
    sweep_type: Literal["box"]
    half_extent: list[float] = pydantic.Field(
        [5.0, 5.0, 5.0],
        min_length=3,
        max_length=3,
        description="Half extent of the box [stage_units]",
    )
    sphere_radius: float = 0.5
    position: list[float] = pydantic.Field(
        [0.0, 0.0, 0.0],
        min_length=3,
        max_length=3,
        description="Position of the box sweep [stage_units]",
    )
    rotation: list[float] = pydantic.Field(
        [0.0, 0.0, 0.0, 1.0],
        min_length=4,
        max_length=4,
        description="Rotation of the box sweep in quaternion format",
    )
    direction: list[float] = pydantic.Field(
        [0.0, 0.0, -1.0],
        min_length=3,
        max_length=3,
        description="Direction of the box sweep",
    )
    max_distance: float = pydantic.Field(
        0.0, description="Sweep distance [stage_units]"
    )


SweepParameters = TreeSweepParameters | SphereSweepParameters | BoxSweepParameters


class CollisionExportService:
    def __init__(self):
        carb.log_verbose("Acquire physx interfaces")
        self._physx_cooking = omni.physx.get_physx_cooking_interface()
        self._physx_sweep = omni.physx.get_physx_scene_query_interface()
        self._physx_query = omni.physx.get_physx_property_query_interface()
        self._cached_collision_setups = PrimCollisionSetupCache()

    def get_prim_collider(self, prim: Usd.Prim) -> list[collision_shapes.Collider]:
        prim_path: str = prim.GetPath().pathString

        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            carb.log_verbose(f"Prim {prim.GetPath()} has no collision API.")
            return []

        if (
            not UsdPhysics.CollisionAPI.Get(prim.GetStage(), prim.GetPath())
            .GetCollisionEnabledAttr()
            .Get()
        ):
            carb.log_verbose(f"Collider disabled for export {prim.GetPath()}")
            return []

        # Unknown usually stands for primitive shape like colliders
        collision_approximation = "unknown"

        if prim.HasAttribute("physics:approximation"):
            collision_approximation = prim.GetAttributeAtPath(
                f"{prim_path}.physics:approximation"
            ).Get(Usd.TimeCode.Default())
        carb.log_verbose(
            f"Collider PrimType path={prim_path} type={prim.GetTypeName()} approx={collision_approximation}"
        )

        # Generate collider based on the prim type
        if prim.GetTypeName() == "Sphere":
            return [collision_shapes.sphere_to_collider(prim)]
        if prim.GetTypeName() == "Cube":
            return [
                collision_shapes.cube_to_collider(
                    prim,
                )
            ]
        if prim.GetTypeName() == "Cylinder":
            collider = collision_shapes.cylinder_to_collider(prim)
            if collider is None:
                carb.log_warn(
                    f"Unsupported cylinder collider {prim.GetPath()} with type '{prim.GetTypeName()}/{collision_approximation}' for export."
                )
                return []
            return [collider]
        if prim.GetTypeName() == "Capsule":
            collider = collision_shapes.capsule_to_collider(prim)
            if collider is None:
                carb.log_warn(
                    f"Unsupported capsule collider {prim.GetPath()} with type '{prim.GetTypeName()}/{collision_approximation}' for export."
                )
                return []
            return [collider]

        if prim.GetTypeName() == "Plane":
            collider = collision_shapes.plane_to_collider(prim)
            if collider is None:
                carb.log_warn(
                    f"Unsupported plane collider {prim.GetPath()} with type '{prim.GetTypeName()}/{collision_approximation}' for export."
                )
                return []
            return [collider]
        if (
            collision_approximation == "convexHull"
            or collision_approximation == "convexDecomposition"
        ):
            stage_id: int = UsdUtils.StageCache.Get().GetId(prim.GetStage()).ToLongInt()
            prim_id = PhysicsSchemaTools.sdfPathToInt(prim.GetPath())
            convex_colliders = collision_shapes.get_convex_hull_colliders(
                self._physx_cooking, stage_id, prim, prim_id
            )

            if convex_colliders is None or len(convex_colliders) == 0:
                carb.log_warn(
                    f"Could not fallback to convex hull {prim.GetPath()} collision type '{prim.GetTypeName()}/{collision_approximation}' is not supported for export."
                )
                return []

            return [collider for _, collider in convex_colliders.items()]

        carb.log_warn(
            f"Unsupported collider {prim.GetPath()} with type '{prim.GetTypeName()}/{collision_approximation}' for export."
        )
        return []

    def collision_sweep(
        self,
        sweep_args: SweepParameters,
        stage: Usd.Stage = None,
        reference_prim_pose: Pose = None,
    ) -> dict[str, collision_shapes.Collider]:
        """Performs a collision sweep in the current stage and returns all colliders hit. Only sphere and box sweeps require the timeline to be playing."""

        if not omni.timeline.get_timeline_interface().is_playing():
            raise RuntimeError(
                "Timeline is not playing. Please start the timeline before performing a collision sweep."
            )

        stage: Usd.Stage = stage if stage else omni.usd.get_context().get_stage()

        carb.log_verbose(f"collision_sweep on stage {stage} {sweep_args}")

        visited_colliders: set[str] = set()
        colliders: dict[str, collision_shapes.Collider] = dict()

        def on_sweep_hit(hit: physx_bindings.SweepHit) -> bool:
            prim: Usd.Prim = stage.GetPrimAtPath(hit.collision)
            carb.log_verbose(f"on_sweep_hit collider {prim.GetPath()}")
            if prim.GetPath().pathString in visited_colliders:
                return True

            carb.log_verbose(
                f"Collision will be added to list collision={hit.collision}"
            )

            prim_colliders = self.get_prim_collider(prim)
            if len(prim_colliders) == 1:
                colliders[prim.GetPath().pathString] = prim_colliders[0]
            elif len(prim_colliders) > 1:
                colliders.update(
                    [
                        (f"{prim.GetPath().pathString}/{hull_index}", collider)
                        for hull_index, collider in enumerate(prim_colliders)
                    ]
                )
            return True

        if sweep_args.sweep_type == "sphere":
            self._physx_sweep.sweep_sphere_all(
                sweep_args.radius,
                carb.Float3(*sweep_args.position),
                carb.Float3(*sweep_args.direction),
                sweep_args.max_distance,
                on_sweep_hit,
            )
        elif sweep_args.sweep_type == "box":
            self._physx_sweep.sweep_box_all(
                halfExtent=sweep_args.half_extent,
                pos=carb.Float3(*sweep_args.position),
                dir=carb.Float3(*sweep_args.direction),
                rot=carb.Float4(*sweep_args.rotation),
                distance=sweep_args.max_distance,
                reportFn=on_sweep_hit,
            )
        elif sweep_args.sweep_type == "tree":
            base_prim: Usd.Prim = stage.GetPrimAtPath(sweep_args.base_prim_path)
            if not base_prim or not base_prim.IsValid():
                raise ValueError(
                    f"Base prim path '{sweep_args.base_prim_path}' is not valid."
                )

            def traverse_children(prim: Usd.Prim):
                if prim.HasAPI(UsdPhysics.CollisionAPI):
                    yield prim
                for child in prim.GetAllChildren():
                    yield from traverse_children(child)

            for prim in traverse_children(base_prim):
                carb.log_verbose(f"Tree sweep visiting prim {prim.GetPath()}")
                prim_colliders = self.get_prim_collider(prim)
                if len(prim_colliders) == 1:
                    colliders[prim.GetPath().pathString] = prim_colliders[0]
                elif len(prim_colliders) > 1:
                    colliders.update(
                        [
                            (f"{prim.GetPath().pathString}/{hull_index}", collider)
                            for hull_index, collider in enumerate(prim_colliders)
                        ]
                    )
        else:
            raise ValueError(f"Unknown sweep type: {sweep_args.sweep_type}")
        return {
            prim_path: collision_shapes.Collider(
                shape=collider.shape,
                pose=PrimUtils.get_relative_pose(
                    reference_prim_pose,
                    WSPose(pose=collider.pose.position + collider.pose.orientation),
                ).to_nova_pose()
                if reference_prim_pose
                else collider.pose,
                prim_path=collider.prim_path,
            )
            for prim_path, collider in colliders.items()
            if collider is not None
        }

    async def collision_sweep_to_collision_setup(
        self,
        reference_prim: Usd.Prim,
        motion_group_prim: Usd.Prim,
        sweep_parameters: SweepParameters,
        tool_prim: Usd.Prim | None = None,
        self_collision: bool = True,
        stabilization_wait_time: float = 1.0,
    ) -> wb.models.CollisionSetup:
        timeline, _ = SceneUtils.check_simulation()
        if not timeline.is_playing():
            timeline.play()

        while timeline.is_stopped():
            # waiting for the timeline to start
            await asyncio.sleep(0.1)

        await asyncio.sleep(
            stabilization_wait_time
        )  # wait a bit to ensure physics stabilizes

        reference_pose = PrimUtils.get_prim_pose(
            reference_prim.GetPath().pathString, coordinate_system="world"
        )

        colliders = self.collision_sweep(
            stage=motion_group_prim.GetStage(),
            sweep_args=sweep_parameters,
            reference_prim_pose=reference_pose,
        )
        collider_count = len(colliders)

        tool_colliders, colliders = await self._extract_tool_colliders(
            colliders=colliders,
            motion_group_prim=motion_group_prim,
            tool_prim=tool_prim,
        )
        tool_collider_count = len(tool_colliders)

        link_chain_colliders, colliders = await self._extract_link_attachments(
            colliders=colliders,
            motion_group_prim=motion_group_prim,
        )
        link_collider_count = collider_count - len(colliders) - tool_collider_count

        colliders = {
            shape_id: to_nova_collider(shape) for shape_id, shape in colliders.items()
        }

        carb.log_info(
            f"Found {collider_count} colliders: {len(colliders)} static colliders, {tool_collider_count} tool colliders, {link_collider_count} link chain colliders."
        )

        return wb.models.CollisionSetup(
            colliders=colliders,
            link_chain=link_chain_colliders,
            tool=tool_colliders if tool_colliders else None,
            self_collision_detection=self_collision,
        )

    async def export_collision_sweep_to_nova(
        self,
        reference_prim: Usd.Prim,
        motion_group_prim: Usd.Prim,
        sweep_parameters: SweepParameters,
        collision_setup_id: str,
        progress_callback_fn: Callable[[float], None],
        tool_prim: Usd.Prim | None = None,
        self_collision: bool = True,
        stabilization_wait_time: float = 1.0,
    ) -> wb.models.CollisionSetup:
        motion_group = get_motion_group_configuration_from_prim(motion_group_prim)
        if progress_callback_fn:
            progress_callback_fn(0.1)

        timeline, _ = SceneUtils.check_simulation()
        if not timeline.is_playing():
            timeline.play()

        collision_setup = await self.collision_sweep_to_collision_setup(
            reference_prim=reference_prim,
            motion_group_prim=motion_group_prim,
            sweep_parameters=sweep_parameters,
            tool_prim=tool_prim,
            self_collision=self_collision,
            stabilization_wait_time=stabilization_wait_time,
        )

        if progress_callback_fn:
            progress_callback_fn(0.5)

        stream_config = motion_group.motion_stream_configuration

        async with get_api_client_from_config(
            stream_config.get_api_configuration()
        ) as api:
            await wb.StoreCollisionSetupsApi(api).store_collision_setup(
                cell=stream_config.cell,
                setup=collision_setup_id,
                collision_setup=collision_setup,
            )

        if progress_callback_fn:
            progress_callback_fn(1.0)
        return collision_setup

    async def _extract_tool_colliders(
        self,
        colliders: dict[str, collision_shapes.Collider],
        motion_group_prim: Usd.Prim,
        tool_prim: Usd.Prim,
    ) -> tuple[
        dict[str, collision_shapes.Collider], dict[str, collision_shapes.Collider]
    ]:
        for collider_id in list(colliders.keys()):
            collider = colliders[collider_id]
            if tool_prim and collider.prim_path.startswith(
                tool_prim.GetPath().pathString
            ):
                del colliders[collider_id]
                continue

        tool_colliders: dict[str, wb.models.Collider] = dict()
        if tool_prim and tool_prim.IsValid():
            flange_tcp_prim = SchemaUtils.find_motion_group_tcp(motion_group_prim)
            if not flange_tcp_prim or not flange_tcp_prim.IsValid():
                raise RuntimeError(
                    f"Could not find flange TCP for motion group prim '{motion_group_prim.GetPath()}'"
                )
            tool_colliders = get_collision_export_service().collision_sweep(
                stage=motion_group_prim.GetStage(),
                sweep_args=TreeSweepParameters(
                    sweep_type="tree", base_prim_path=tool_prim.GetPath().pathString
                ),
                reference_prim_pose=PrimUtils.get_prim_pose(
                    flange_tcp_prim.GetPath().pathString, coordinate_system="world"
                ),
            )
            tool_colliders = {
                shape_id: to_nova_collider(shape)
                for shape_id, shape in tool_colliders.items()
            }
        return tool_colliders, colliders

    async def _get_motion_group_dh_param_and_link_chain_and_joint_position(
        self, motion_stream_config: MotionStreamConfiguration
    ) -> tuple[
        list[wb.models.DHParameter],
        dict[int, dict[str, wb.models.Collider]],
        list[float],
    ]:
        async with motion_stream_config.get_api_client() as api:
            motion_group_description: wb.models.MotionGroupDescription = (
                await wb.MotionGroupApi(api).get_motion_group_description(
                    cell=motion_stream_config.cell,
                    controller=motion_stream_config.controller,
                    motion_group=motion_stream_config.motion_group,
                )
            )

            link_chain = await wb.MotionGroupModelsApi(
                api
            ).get_motion_group_collision_model(
                motion_group_model=motion_group_description.motion_group_model,
            )

            motion_group_state = await wb.MotionGroupApi(
                api
            ).get_current_motion_group_state(
                cell=motion_stream_config.cell,
                controller=motion_stream_config.controller,
                motion_group=motion_stream_config.motion_group,
            )

            return (
                motion_group_description.dh_parameters,
                link_chain,
                motion_group_state.joint_position,
            )

    async def _extract_link_attachments(
        self,
        colliders: dict[str, collision_shapes.Collider],
        motion_group_prim: Usd.Prim,
    ) -> tuple[
        dict[str, collision_shapes.Collider], dict[str, collision_shapes.Collider]
    ]:
        link_attachments: dict[str, dict[str, collision_shapes.Collider]] = {}
        motion_group_links = RobotSchemaUtils.get_motion_group_links_ordered(
            motion_group_prim
        )

        link_path_index_mapping: dict[str, int] = {
            link.GetPath().pathString: index
            for index, link in enumerate(motion_group_links)
        }

        # Collect all colliders which are parented under a motion group link
        for collider_id in list(colliders.keys()):
            collider = colliders[collider_id]
            collider_prim = motion_group_prim.GetStage().GetPrimAtPath(
                collider.prim_path
            )
            if not collider_prim or not collider_prim.IsValid():
                carb.log_warn(
                    f"Collider prim '{collider_id}' is not valid, skipping link attachment processing."
                )
                continue
            link_parent = RobotSchemaUtils.get_link_parent(collider_prim)
            if not link_parent:
                continue
            if link_parent.GetPath().pathString not in link_path_index_mapping:
                continue  # Only process attachments from the selected motion group
            link_path = link_parent.GetPath().pathString
            if link_path not in link_attachments:
                link_attachments[link_path] = {}

            if collider_id.split("/")[-1] == "visuals":
                carb.log_verbose(
                    f"Skipping visuals collider {collider_id} for link {link_path}"
                )
                continue  # skip visuals colliders (they are provided by nova collider model api)
            carb.log_verbose(
                f"Found link attachment: {collider_id} for link: {link_path}"
            )
            link_attachments[link_path][collider_id] = colliders[collider_id]
            del colliders[collider_id]

        motion_stream_config = get_motion_group_configuration_from_prim(
            motion_group_prim
        ).motion_stream_configuration

        (
            dh_parameters,
            link_chain,
            current_joint_values,
        ) = await self._get_motion_group_dh_param_and_link_chain_and_joint_position(
            motion_stream_config
        )

        # Compute FK at current joint values so link transforms match the swept poses
        link_transforms = compute_forward_kinematics_chain(
            dh_parameters=dh_parameters,
            dh_unit_to_stage_unit_factor=1,
            joint_values_rad=current_joint_values,
        )

        # The colliders are added relative to the the link frame/transform
        # This loop calculates the relative pose of each collider to the link frame and updates the collider poses
        # accordingly before adding them to the link chain colliders
        for link_path, link_colliders in link_attachments.items():
            link_index = link_path_index_mapping[link_path]

            if link_index >= len(link_transforms):
                carb.log_warn(
                    f"Link index {link_index} exceeds available transforms "
                    f"({len(link_transforms)}), skipping {link_path} attachments."
                )
                continue

            # FK transform from robot base to this link frame (in mm)
            link_T_base = link_transforms[link_index]
            base_T_link = np.linalg.inv(link_T_base)

            for collider_id, collider in link_colliders.items():
                # Collider pose is relative to robot base (in mm + rotvec)
                collider_pose_list = list(collider.pose.position) + list(
                    collider.pose.orientation
                    if collider.pose.orientation
                    else [0, 0, 0]
                )

                carb.log_verbose(
                    f"Link attachment {collider_id}: "
                    f"robot-relative pose = {collider_pose_list}"
                )

                # Convert to 4x4 matrix, transform to link frame, convert back
                collider_T_base = pose_to_matrix(collider_pose_list)
                collider_T_link = base_T_link @ collider_T_base
                link_relative_pose = matrix_to_pose(collider_T_link)

                carb.log_verbose(f"Link-relative pose = {link_relative_pose}")

                collider.pose.position = link_relative_pose[:3]
                collider.pose.orientation = link_relative_pose[3:]

                collider_id = collider_id.removeprefix(link_path + "/")

                link_chain[link_index][collider_id] = to_nova_collider(collider)

        return link_chain, colliders

    async def get_collision_setup(
        self, motion_group_prim: Usd.Prim, setup_name: str, force_refresh: bool = False
    ) -> wb.models.CollisionSetup | None:
        motion_group = get_motion_group_configuration_from_prim(motion_group_prim)
        if not motion_group:
            carb.log_warn(
                f"Prim '{motion_group_prim.GetPath().pathString}' is not part of a motion group, cannot fetch collision setup."
            )
            return None

        return await self._cached_collision_setups.get(
            motion_group_prim,
            setup_name,
            force_refresh,
        )


_collision_export_service = CollisionExportService()


def get_collision_export_service() -> CollisionExportService:
    return _collision_export_service
