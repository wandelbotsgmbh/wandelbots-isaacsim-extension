from typing import Callable, Literal
import re
import carb
import numpy as np
import omni.physx.bindings._physx as physx_bindings
import omni.physx
import omni.usd
from pxr import Sdf, UsdUtils, Usd, UsdGeom, UsdPhysics
import pydantic
import omni.timeline
from pxr import PhysicsSchemaTools
from wandelbots.omni.core.collision.collision_setup_cache import PrimCollisionSetupCache
from wandelbots.omni.core.collision import authored_geometry
import wandelbots.omni.core.collision.shapes as collision_shapes
from wandelbots.omni.utils.prims import Pose, PrimUtils, WSPose
from wandelbots.omni.utils.math import pose_to_matrix, matrix_to_pose
import wandelbots_api_client.v2 as wb
from wandelbots.omni.utils.scene import SceneUtils
from wandelbots.omni.utils.api import ApiConfiguration
from wandelbots.omni.manipulators import (
    MotionStreamConfiguration,
    get_motion_group_configuration_from_prim,
    compute_forward_kinematics_chain,
)
from wandelbots.omni.core.collision.utils import to_nova_collider
from wandelbots.omni.usd import SchemaUtils, RobotSchemaUtils
from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVAInstance


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


# NOVA models name the per-link collider scope 'visuals' (arms) or
# 'visuals_<n>' (positioners/rails); it sits directly under the link prim.
_ROBOT_VISUALS_SCOPE = re.compile(r"^visuals(_\d+)?$")

# Stands for the primitive shapes, which carry no authored approximation.
_UNKNOWN_APPROXIMATION = "unknown"

# Mesh approximations PhysX cooks into convex hulls.
_COOKED_APPROXIMATIONS = ("convexHull", "convexDecomposition")
# Mesh approximations that can be built from the authored points alone, without
# PhysX. They enclose the mesh exactly, so exporting them is not a fallback.
_BOUNDING_APPROXIMATIONS = ("boundingCube", "boundingSphere")

# Prim types NOVA has a parametric collider for.
_PARAMETRIC_SHAPE_BUILDERS = {
    "Sphere": collision_shapes.sphere_to_collider,
    "Cylinder": collision_shapes.cylinder_to_collider,
    "Capsule": collision_shapes.capsule_to_collider,
    "Cube": collision_shapes.cube_to_collider,
    "Plane": collision_shapes.plane_to_collider,
}


class UnsupportedLinkAttachmentError(RuntimeError):
    """Equipment is attached to a link the robot's kinematic chain does not reach."""


def is_stage_authored_equipment(
    collider_prim: Usd.Prim, link_prim: Usd.Prim, stage: Usd.Stage
) -> bool:
    """Is this link-parented collider additional equipment (a link extra), as
    opposed to the robot's own geometry?

    The robot's own geometry always comes from NOVA's canonical collision
    model; storing it as extras renders and collision-checks the whole robot
    twice. Name matching ("visuals") is not enough - robot assets differ in
    how deep their collider prims sit (e.g. link_N/visuals vs.
    link_N/visuals/0..31). Structural rule instead: an extra must be
    introduced in the stage's own layer stack (session/root/sublayers)
    somewhere between the link and the collider - a user-created prim, or a
    user-created prim referencing an equipment asset. Prims defined only
    inside the referenced robot asset are the robot itself. The link's own
    'visuals' collider scope (a direct child of the link) is additionally
    treated as robot geometry, which also covers robots flattened into the
    stage layers; a 'visuals' scope nested deeper (e.g. inside a referenced
    equipment asset: link_N/<equipment>/visuals/...) stays equipment.
    """
    local_layers = set(stage.GetLayerStack())
    introduced_locally = False
    link_path = link_prim.GetPath()
    current = collider_prim
    while current and current.GetPath() != link_path:
        parent = current.GetParent()
        if (
            parent
            and parent.GetPath() == link_path
            and _ROBOT_VISUALS_SCOPE.match(current.GetName())
        ):
            return False
        if not introduced_locally:
            for spec in current.GetPrimStack():
                if spec.specifier == Sdf.SpecifierDef and spec.layer in local_layers:
                    introduced_locally = True
                    break
        current = parent
    return introduced_locally


def raise_for_unreachable_link_attachments(
    link_attachments: dict[str, dict[str, collision_shapes.Collider]],
    link_index_by_path: dict[str, int],
    link_count: int,
) -> None:
    """Refuse to export equipment attached to a link the kinematic chain does
    not reach. Such a collider has no frame to be expressed in, and dropping it
    would ship a collision setup that silently misses geometry."""
    unreachable = sorted(
        collider_id
        for link_path, link_colliders in link_attachments.items()
        if link_index_by_path[link_path] >= link_count
        for collider_id in link_colliders
    )
    if not unreachable:
        return
    raise UnsupportedLinkAttachmentError(
        f"{len(unreachable)} collider(s) are attached to links outside the "
        f"{link_count} links of the kinematic chain: {', '.join(unreachable)}"
    )


class _CollisionSetupKeepingEmptyLinks(wb.models.CollisionSetup):
    """Keeps the empty `link_chain` entries that the generated client's
    to_dict() drops as falsy values. Without them every populated entry shifts
    to a lower link index, so a link_4 extra arrives as a base-link collider.
    Remove once the upstream client preserves empty entries."""

    def to_dict(self):
        result = super().to_dict()
        if self.link_chain is not None:
            result["link_chain"] = [
                {key: collider.to_dict() for key, collider in link.items()}
                for link in self.link_chain
            ]
        return result


class CollisionExportService:
    def __init__(self):
        carb.log_verbose("Acquire physx interfaces")
        self._physx_cooking = omni.physx.get_physx_cooking_interface()
        self._physx_sweep = omni.physx.get_physx_scene_query_interface()
        self._physx_query = omni.physx.get_physx_property_query_interface()
        self._cached_collision_setups = PrimCollisionSetupCache()

    def get_prim_collider(self, prim: Usd.Prim) -> list[collision_shapes.Collider]:
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            carb.log_verbose(f"Prim {prim.GetPath()} has no collision API.")
            return []

        if not authored_geometry.collision_enabled(prim):
            carb.log_verbose(f"Collider disabled for export {prim.GetPath()}")
            return []

        time = Usd.TimeCode.Default()
        collision_approximation = (
            authored_geometry.authored_approximation(prim, time)
            or _UNKNOWN_APPROXIMATION
        )
        carb.log_verbose(
            f"Collider PrimType path={prim.GetPath()} type={prim.GetTypeName()} "
            f"approx={collision_approximation}"
        )

        if prim.IsA(UsdGeom.Gprim):
            return self._gprim_collider(prim, collision_approximation)

        # CollisionAPI on an assembly prim (Xform/Scope - e.g. Isaac Sim's
        # Colliders Preset applied to a group) makes every geometry prim in its
        # subtree a collider.
        colliders: list[collision_shapes.Collider] = []
        for geometry_prim, approximation in authored_geometry.expand_collider_prims(
            [prim], time
        ):
            colliders.extend(
                self._gprim_collider(
                    geometry_prim, approximation or collision_approximation
                )
            )
        if not colliders:
            carb.log_warn(
                f"Collider {prim.GetPath()} ({prim.GetTypeName()}) has no "
                "gprims in its subtree to export."
            )
        return colliders

    def _gprim_collider(
        self, prim: Usd.Prim, collision_approximation: str
    ) -> list[collision_shapes.Collider]:
        """Convert one geometry prim into NOVA colliders under the given
        effective approximation (the prim's own, or one inherited from the
        CollisionAPI-carrying ancestor it was expanded from)."""
        build_parametric_shape = _PARAMETRIC_SHAPE_BUILDERS.get(prim.GetTypeName())
        if build_parametric_shape:
            collider = build_parametric_shape(prim)
            if collider is not None:
                return [collider]
            reason = "its axis or scale is not expressible as a NOVA primitive"
        elif collision_approximation in _COOKED_APPROXIMATIONS:
            cooked_colliders = self._cooked_convex_colliders(prim)
            if cooked_colliders:
                return cooked_colliders
            reason = "PhysX cooking returned no hulls"
        elif collision_approximation in _BOUNDING_APPROXIMATIONS:
            reason = ""
        else:
            reason = (
                f"NOVA has no equivalent for "
                f"'{prim.GetTypeName()}/{collision_approximation}'"
            )
        return self._hull_collider(prim, collision_approximation, reason)

    def _cooked_convex_colliders(
        self, prim: Usd.Prim
    ) -> list[collision_shapes.Collider]:
        """Convex hulls PhysX cooked for the prim, empty when cooking yields none."""
        stage_id: int = UsdUtils.StageCache.Get().GetId(prim.GetStage()).ToLongInt()
        prim_id = PhysicsSchemaTools.sdfPathToInt(prim.GetPath())
        cooked_colliders = collision_shapes.get_convex_hull_colliders(
            self._physx_cooking, stage_id, prim, prim_id
        )
        return list(cooked_colliders.values())

    @staticmethod
    def _hull_collider(
        prim: Usd.Prim, collision_approximation: str, reason: str
    ) -> list[collision_shapes.Collider]:
        """Export the authored geometry as a convex hull enclosing it. NOVA's
        collision world is convex-only, so a hull is the conservative stand-in
        whenever the authored shape has no NOVA representation. An empty
        `reason` means the hull is an exact fit and needs no warning."""
        collider = collision_shapes.points_hull_collider(prim, collision_approximation)
        if collider is None:
            carb.log_warn(
                f"Unsupported collider {prim.GetPath()} with type "
                f"'{prim.GetTypeName()}/{collision_approximation}' for export"
                f"{f': {reason}' if reason else ''}."
            )
            return []
        if not reason:
            # The bounding approximations enclose the mesh exactly.
            carb.log_info(
                f"Collider {prim.GetPath()} exported as its "
                f"'{collision_approximation}' hull."
            )
            return [collider]
        carb.log_warn(f"Collider {prim.GetPath()} exported as a convex hull: {reason}.")
        return [collider]

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

        colliders: dict[str, collision_shapes.Collider] = dict()

        def on_sweep_hit(hit: physx_bindings.SweepHit) -> bool:
            prim: Usd.Prim = stage.GetPrimAtPath(hit.collision)
            carb.log_verbose(f"on_sweep_hit collider {prim.GetPath()}")
            self._add_prim_colliders(colliders, prim)
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
                self._add_prim_colliders(colliders, prim)
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

    def _add_prim_colliders(
        self, colliders: dict[str, collision_shapes.Collider], prim: Usd.Prim
    ) -> None:
        """Add a prim's colliders to the sweep result, keyed by prim path. A
        prim that converts into several hulls gets one entry per hull."""
        prim_path = prim.GetPath().pathString
        if prim_path in colliders:
            return
        prim_colliders = self.get_prim_collider(prim)
        if len(prim_colliders) == 1:
            colliders[prim_path] = prim_colliders[0]
            return
        for hull_index, collider in enumerate(prim_colliders):
            colliders[f"{prim_path}/{hull_index}"] = collider

    async def collision_sweep_to_collision_setup(
        self,
        sweep_parameters: SweepParameters,
        motion_group_prim: Usd.Prim | None = None,
        tool_prim: Usd.Prim | None = None,
        export_tool_colliders: bool = False,
        export_link_colliders: bool = False,
        self_collision: bool = True,
        stabilization_wait_time: float = 1.0,
    ) -> wb.models.CollisionSetup:
        """Sweep the stage and build a CollisionSetup.

        Storage frames, fixed by convention: `colliders` are static obstacles in
        the stage world frame, `tool` colliders are in the flange frame, and
        `link_chain` holds only the equipment attached to robot links, in
        link-local frames. Extras ids are prim paths relative to the exporting
        motion group ('link_4/Cube'), tool ids absolute stage paths, so both
        double as provenance for validate_setup_matches_motion_group.
        """
        if motion_group_prim is None and tool_prim is not None:
            raise ValueError(
                "tool_prim requires motion_group_prim to resolve the flange/TCP frame it is swept relative to."
            )

        tool_colliders: dict[str, wb.models.Collider] = {}
        link_chain_colliders: list[dict[str, wb.models.Collider]] | None = None

        async with SceneUtils.playing_timeline(stabilization_wait_time):
            # No reference pose: the shape builders already produce absolute
            # stage-world poses, which is the storage frame for static colliders.
            colliders = self.collision_sweep(sweep_args=sweep_parameters)
            swept_count = len(colliders)

            if motion_group_prim is not None:
                # Link extras must be collected before the strip below removes
                # them together with the robot body.
                if export_link_colliders:
                    (
                        link_chain_colliders,
                        colliders,
                    ) = await self._collect_link_extras(
                        colliders=colliders,
                        motion_group_prim=motion_group_prim,
                    )

                if export_tool_colliders and tool_prim and tool_prim.IsValid():
                    tool_colliders = self._sweep_tool_colliders(
                        motion_group_prim=motion_group_prim,
                        tool_prim=tool_prim,
                    )

                self._strip_robot_colliders(colliders, motion_group_prim, tool_prim)

        return self._build_collision_setup(
            colliders=colliders,
            tool_colliders=tool_colliders,
            link_chain_colliders=link_chain_colliders,
            self_collision=self_collision,
            swept_count=swept_count,
        )

    @staticmethod
    def _build_collision_setup(
        colliders: dict[str, collision_shapes.Collider],
        tool_colliders: dict[str, wb.models.Collider],
        link_chain_colliders: list[dict[str, wb.models.Collider]] | None,
        self_collision: bool,
        swept_count: int,
    ) -> wb.models.CollisionSetup:
        extras_count = (
            sum(len(link) for link in link_chain_colliders)
            if link_chain_colliders
            else 0
        )
        stripped_count = swept_count - len(colliders) - extras_count
        static_colliders = {
            shape_id: to_nova_collider(shape) for shape_id, shape in colliders.items()
        }

        carb.log_info(
            f"Swept {swept_count} colliders: {len(static_colliders)} statics (world), "
            f"{len(tool_colliders)} tool colliders (flange), "
            f"{extras_count} link extras (link-local), {stripped_count} stripped."
        )

        return _CollisionSetupKeepingEmptyLinks(
            colliders=static_colliders,
            link_chain=link_chain_colliders if extras_count else None,
            tool=tool_colliders if tool_colliders else None,
            self_collision_detection=self_collision,
        )

    async def export_collision_sweep_to_nova(
        self,
        instance: NOVAInstance,
        sweep_parameters: SweepParameters,
        collision_setup_id: str,
        progress_callback_fn: Callable[[float], None],
        motion_group_prim: Usd.Prim | None = None,
        tool_prim: Usd.Prim | None = None,
        export_tool_colliders: bool = False,
        export_link_colliders: bool = False,
        self_collision: bool = True,
        stabilization_wait_time: float = 1.0,
    ) -> tuple[wb.models.CollisionSetup, str]:
        """Returns the stored CollisionSetup and the cell it was stored in."""
        cell_id = await get_instances_api().fetch_primary_cell_id(instance)
        if not cell_id:
            raise ValueError(
                f"Instance '{instance.display_name}' has no cells available."
            )

        api_client = get_instances_api().create_api_client_for_instance(instance)
        if api_client is None:
            raise ValueError(f"Cannot connect to instance '{instance.display_name}'.")

        if progress_callback_fn:
            progress_callback_fn(0.1)

        try:
            # collision_sweep_to_collision_setup owns starting the timeline (if
            # needed) and restoring it to its prior state afterward.
            collision_setup = await self.collision_sweep_to_collision_setup(
                sweep_parameters=sweep_parameters,
                motion_group_prim=motion_group_prim,
                tool_prim=tool_prim,
                export_tool_colliders=export_tool_colliders,
                export_link_colliders=export_link_colliders,
                self_collision=self_collision,
                stabilization_wait_time=stabilization_wait_time,
            )

            if progress_callback_fn:
                progress_callback_fn(0.5)

            await wb.StoreCollisionSetupsApi(api_client).store_collision_setup(
                cell=cell_id,
                setup=collision_setup_id,
                collision_setup=collision_setup,
            )
        finally:
            await api_client.close()

        if progress_callback_fn:
            progress_callback_fn(1.0)
        return collision_setup, cell_id

    @staticmethod
    def _strip_prim_subtree_colliders(
        colliders: dict[str, collision_shapes.Collider], prim: Usd.Prim
    ) -> None:
        """Remove every swept collider that lives under `prim`'s subtree, in place."""
        prim_path = prim.GetPath().pathString
        prefix = prim_path + "/"
        for collider_id in list(colliders.keys()):
            collider_prim_path = colliders[collider_id].prim_path
            if collider_prim_path == prim_path or collider_prim_path.startswith(prefix):
                del colliders[collider_id]

    def _strip_robot_colliders(
        self,
        colliders: dict[str, collision_shapes.Collider],
        motion_group_prim: Usd.Prim,
        tool_prim: Usd.Prim | None,
    ) -> None:
        """Drop the robot's own and its tools' colliders from the statics.

        Robot-attached geometry left in the statics is baked in as a frozen
        obstacle at wherever it stood during the sweep. Every ToolAPI-linked
        tool is stripped, not just the picked one: clearing the tool picker must
        not turn the gripper into a static.
        """
        self._strip_prim_subtree_colliders(colliders, motion_group_prim)
        for linked_tool in SchemaUtils.list_motion_group_tools(motion_group_prim):
            if linked_tool and linked_tool.IsValid():
                self._strip_prim_subtree_colliders(colliders, linked_tool)
        if tool_prim and tool_prim.IsValid():
            self._strip_prim_subtree_colliders(colliders, tool_prim)

    def _sweep_tool_colliders(
        self,
        motion_group_prim: Usd.Prim,
        tool_prim: Usd.Prim,
    ) -> dict[str, wb.models.Collider]:
        """Sweep the tool subtree and express its colliders in the flange frame."""
        flange_tcp_prim = SchemaUtils.find_motion_group_tcp(motion_group_prim)
        if not flange_tcp_prim or not flange_tcp_prim.IsValid():
            raise RuntimeError(
                f"Could not find flange TCP for motion group prim '{motion_group_prim.GetPath()}'"
            )
        tool_colliders = self.collision_sweep(
            stage=motion_group_prim.GetStage(),
            sweep_args=TreeSweepParameters(
                sweep_type="tree", base_prim_path=tool_prim.GetPath().pathString
            ),
            reference_prim_pose=PrimUtils.get_prim_pose(
                flange_tcp_prim.GetPath().pathString, coordinate_system="world"
            ),
        )
        return {
            shape_id: to_nova_collider(shape)
            for shape_id, shape in tool_colliders.items()
        }

    async def _get_motion_group_dh_param_and_joint_position(
        self, motion_stream_config: MotionStreamConfiguration
    ) -> tuple[list[wb.models.DHParameter], list[float], wb.models.Pose | None]:
        async with motion_stream_config.get_api_client() as api:
            motion_group_description: wb.models.MotionGroupDescription = (
                await wb.MotionGroupApi(api).get_motion_group_description(
                    cell=motion_stream_config.cell,
                    controller=motion_stream_config.controller,
                    motion_group=motion_stream_config.motion_group,
                )
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
                motion_group_state.joint_position,
                getattr(motion_group_description, "kinematic_chain_offset", None),
            )

    async def _collect_link_extras(
        self,
        colliders: dict[str, collision_shapes.Collider],
        motion_group_prim: Usd.Prim,
    ) -> tuple[
        list[dict[str, wb.models.Collider]], dict[str, collision_shapes.Collider]
    ]:
        """Move the equipment attached to robot links out of the statics into an
        extras-only link chain with link-local poses.

        The result holds one dict per link, indexed like NOVA's collision model,
        and never the robot's own geometry: consumers merge it onto the
        canonical model they fetch from NOVA.
        """
        positional_link_indices = self._link_indices_by_position(motion_group_prim)
        link_attachments, link_index_by_path = self._take_link_attachments(
            colliders, motion_group_prim, positional_link_indices
        )

        extras: list[dict[str, wb.models.Collider]] = [
            {}
            for _ in range(
                max(
                    [
                        *positional_link_indices.values(),
                        *link_index_by_path.values(),
                    ],
                    default=-1,
                )
                + 1
            )
        ]

        if not link_attachments:
            return extras, colliders

        link_transforms = await self._link_transforms(motion_group_prim)
        raise_for_unreachable_link_attachments(
            link_attachments, link_index_by_path, len(link_transforms)
        )

        # Swept collider poses are in the stage world frame; the FK chain is
        # rooted at the kinematic base (link_0), so express them there first.
        base_world_pose = PrimUtils.get_motion_group_base_world_pose(motion_group_prim)
        if base_world_pose is None:
            raise RuntimeError(
                f"Motion group '{motion_group_prim.GetPath()}' has no kinematic "
                "base (link_0) to express its link extras in."
            )
        world_to_base = np.linalg.inv(pose_to_matrix(base_world_pose.pose))
        motion_group_path_prefix = motion_group_prim.GetPath().pathString + "/"

        for link_path, link_colliders in link_attachments.items():
            link_index = link_index_by_path[link_path]
            base_to_link = np.linalg.inv(link_transforms[link_index])
            for collider_id, collider in link_colliders.items():
                self._pose_collider_in_link_frame(collider, world_to_base, base_to_link)
                # Id = prim path relative to the motion group (e.g.
                # 'link_4/Cube'): unique within the setup and usable as
                # provenance by validate_setup_matches_motion_group.
                extras[link_index][
                    collider_id.removeprefix(motion_group_path_prefix)
                ] = to_nova_collider(collider)

        return extras, colliders

    @staticmethod
    def _link_indices_by_position(motion_group_prim: Usd.Prim) -> dict[str, int]:
        """Chain index per link path, taken from the ordered link list.

        Only a fallback for links without a `link_<n>` name: the auto-populated
        robot_links relationship has been observed both incomplete and polluted,
        so a link's own number wins wherever it has one.
        """
        motion_group_links = RobotSchemaUtils.get_motion_group_links_ordered(
            motion_group_prim
        )
        carb.log_info(
            "Link extras: candidate links "
            f"{[link.GetPath().pathString for link in motion_group_links]}"
        )
        return {
            link.GetPath().pathString: index
            for index, link in enumerate(motion_group_links)
        }

    def _take_link_attachments(
        self,
        colliders: dict[str, collision_shapes.Collider],
        motion_group_prim: Usd.Prim,
        positional_link_indices: dict[str, int],
    ) -> tuple[dict[str, dict[str, collision_shapes.Collider]], dict[str, int]]:
        """Remove the link-attached equipment from `colliders` and group it by
        link path, together with each link's chain index."""
        stage = motion_group_prim.GetStage()
        motion_group_path_prefix = motion_group_prim.GetPath().pathString + "/"
        link_attachments: dict[str, dict[str, collision_shapes.Collider]] = {}
        link_index_by_path: dict[str, int] = {}

        for collider_id, collider in list(colliders.items()):
            if not collider.prim_path.startswith(motion_group_path_prefix):
                continue  # Only process attachments from the selected motion group
            collider_prim = stage.GetPrimAtPath(collider.prim_path)
            if not collider_prim or not collider_prim.IsValid():
                carb.log_warn(
                    f"Collider prim '{collider_id}' is not valid, skipping link attachment processing."
                )
                continue

            link_prim, link_index = self._nearest_link(
                collider_prim, motion_group_path_prefix, positional_link_indices
            )
            if link_prim is None:
                continue
            link_path = link_prim.GetPath().pathString

            if not is_stage_authored_equipment(collider_prim, link_prim, stage):
                carb.log_verbose(
                    f"Skipping robot-own collider {collider_id} for link "
                    f"{link_path} (canonical model provides the robot geometry)"
                )
                continue

            carb.log_info(
                f"Link attachment: {collider_id} -> chain index {link_index} ({link_path})"
            )
            link_attachments.setdefault(link_path, {})[collider_id] = collider
            link_index_by_path[link_path] = link_index
            del colliders[collider_id]

        return link_attachments, link_index_by_path

    @staticmethod
    def _nearest_link(
        collider_prim: Usd.Prim,
        motion_group_path_prefix: str,
        positional_link_indices: dict[str, int],
    ) -> tuple[Usd.Prim | None, int | None]:
        """Nearest ancestor link with a resolvable chain index: its own
        `link_<n>` number, or the enumeration position for unnumbered
        LinkAPI-only links."""
        current = collider_prim
        while current and current.GetPath().pathString.startswith(
            motion_group_path_prefix
        ):
            if RobotSchemaUtils.is_robot_link(current):
                link_index = RobotSchemaUtils.get_link_number(current)
                if link_index is None:
                    link_index = positional_link_indices.get(
                        current.GetPath().pathString
                    )
                if link_index is not None:
                    return current, link_index
            current = current.GetParent()
        return None, None

    async def _link_transforms(self, motion_group_prim: Usd.Prim) -> list[np.ndarray]:
        """Forward kinematics of the chain at the current joint values, so the
        link frames match the poses the sweep captured."""
        configuration = get_motion_group_configuration_from_prim(motion_group_prim)
        motion_stream_config = (
            configuration.motion_stream_configuration if configuration else None
        )
        if motion_stream_config is None or not motion_stream_config.is_connectable:
            # The link frames come from NOVA's kinematic model, which needs the
            # robot's controller assignment.
            raise RuntimeError(
                f"Cannot export link colliders for "
                f"'{motion_group_prim.GetName()}': the robot is not assigned to a "
                "NOVA controller. Connect it, or export without link colliders."
            )

        (
            dh_parameters,
            current_joint_values,
            kinematic_chain_offset,
        ) = await self._get_motion_group_dh_param_and_joint_position(
            motion_stream_config
        )

        return compute_forward_kinematics_chain(
            dh_parameters=dh_parameters,
            dh_unit_to_stage_unit_factor=1,
            joint_values_rad=current_joint_values,
            kinematic_chain_offset=kinematic_chain_offset,
        )

    @staticmethod
    def _pose_collider_in_link_frame(
        collider: collision_shapes.Collider,
        world_to_base: np.ndarray,
        base_to_link: np.ndarray,
    ) -> None:
        """Rewrite a swept collider's world pose (mm plus rotation vector) into
        the link frame, in place."""
        world_pose = list(collider.pose.position) + list(
            collider.pose.orientation if collider.pose.orientation else [0, 0, 0]
        )
        collider_in_link = base_to_link @ world_to_base @ pose_to_matrix(world_pose)
        link_relative_pose = matrix_to_pose(collider_in_link)
        collider.pose.position = link_relative_pose[:3]
        collider.pose.orientation = link_relative_pose[3:]

    async def get_collision_setup_by_cell(
        self,
        cell: str,
        api_configuration: ApiConfiguration,
        setup_name: str,
        force_refresh: bool = False,
    ) -> wb.models.CollisionSetup | None:
        return await self._cached_collision_setups.get_by_cell(
            cell,
            api_configuration,
            setup_name,
            force_refresh,
        )

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
