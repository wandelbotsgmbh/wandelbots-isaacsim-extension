import asyncio
from dataclasses import dataclass
from typing import cast
import aiohttp
import carb.events
import carb
import weakref
from omni.kit.viewport.window import ViewportWindow
import omni.ui as ui
import omni.ui_scene as ui_scene
from wandelbots.omni.core.collision.collision_export_service import (
    get_collision_export_service,
    get_motion_group_configuration_from_prim,
)
from wandelbots.omni.utils.api import (
    ApiConfiguration,
    describe_api_error,
    get_api_client_from_config,
)
from wandelbots.omni.ui.overlay.collision_world.utils import (
    CARB_OVERLAY_COLOR,
    get_overlay_color,
    set_overlay_color,
)
from wandelbots.omni.core.collision.utils import validate_setup_matches_motion_group
from wandelbots.omni.usd.schema_utils import SchemaUtils
from wandelbots.omni.utils.prims import PrimUtils, PrimPoseWatcher
from wandelbots.omni.ui.overlay.overlay import ViewportOverlay
from wandelbots.omni.manipulators import MotionStreamConfiguration
from wandelbots.omni.ui.overlay.manipulators import MotionGroupMesh
from omni.kit.async_engine import run_coroutine
from pxr import Usd
import omni.usd
import omni.kit.notification_manager as nm
from omni.kit.app import SettingChangeSubscription
import carb.settings
import carb.dictionary
from wandelbots.omni.utils.math import nova_pose_to_scene_matrix
from wandelbots.omni.utils.scene import SceneUtils
import omni.ui.scene as sc
from wandelbots.omni.ui.overlay.manipulators import (
    create_from_collider,
    ManipulatorMesh,
)
from wandelbots.omni.datatypes import Pose
import wandelbots.omni.ui.colors as color_utils
import wandelbots_api_client.v2 as wb
from wandelbots_api_client.v2.exceptions import OpenApiException

COLLISION_WORLD_OVERLAY_NAME = "CollisionWorldOverlay"

# Everything the NOVA client raises for a failed call. Catching plain Exception
# here would swallow bugs in the request we build.
_NOVA_REQUEST_ERRORS = (OpenApiException, aiohttp.ClientError, asyncio.TimeoutError)


def collider_transform(
    anchor_transform: sc.Matrix44,
    collider: wb.models.Collider,
    stage_meters_per_unit: float,
) -> sc.Matrix44:
    """Place one collider in the anchor's frame. Collider poses and vertices are
    in millimeters, so they are scaled to stage units."""
    unit_factor = 0.001 / stage_meters_per_unit
    collider_pose = collider.pose.position + collider.pose.orientation
    return (
        anchor_transform
        * nova_pose_to_scene_matrix(collider_pose, stage_meters_per_unit)
        * sc.Matrix44.get_scale_matrix(unit_factor, unit_factor, unit_factor)
    )


def _build_collider_manipulators(
    colliders: dict[str, wb.models.Collider],
    anchor_transform: sc.Matrix44,
    stage_meters_per_unit: float,
    color: color_utils.ColorRGBA,
) -> dict[str, ManipulatorMesh]:
    manipulators = {}
    for collider_id, collider in colliders.items():
        mesh_manipulator = create_from_collider(
            collider,
            collider_transform(anchor_transform, collider, stage_meters_per_unit),
            color=color,
        )
        if not mesh_manipulator:
            # A plane, for example: stored and collision-checked by NOVA, but
            # the overlay has no mesh for it.
            carb.log_verbose(
                f"Collider {collider_id} of shape "
                f"'{collider.shape.actual_instance.shape_type}' is not drawn"
            )
            continue
        manipulators[collider_id] = mesh_manipulator
    return manipulators


def _position_collider_manipulators(
    manipulators: dict[str, ManipulatorMesh],
    colliders: dict[str, wb.models.Collider],
    anchor_transform: sc.Matrix44,
    stage_meters_per_unit: float,
) -> None:
    for collider_id, manipulator in manipulators.items():
        manipulator.set_transform(
            collider_transform(
                anchor_transform, colliders[collider_id], stage_meters_per_unit
            )
        )


async def _apply_live_joint_state(
    link_chain_manipulator: MotionGroupMesh,
    api_configuration: ApiConfiguration,
    cell: str,
    motion_stream_configuration: MotionStreamConfiguration,
) -> None:
    """Pose the link chain from the robot's joint state on NOVA."""
    try:
        async with get_api_client_from_config(api_configuration) as api_client:
            state = await wb.MotionGroupApi(api_client).get_current_motion_group_state(
                cell=cell,
                controller=motion_stream_configuration.controller,
                motion_group=motion_stream_configuration.motion_group,
            )
    except _NOVA_REQUEST_ERRORS as exc:
        # Fires on every pose change, so keep it to one line.
        carb.log_warn(f"Could not fetch motion group state: {describe_api_error(exc)}")
        return
    link_chain_manipulator.set_joint_values(state.joint_position)


@dataclass
class LoadedCollisionSetup:
    setup_name: str
    # The setup as fetched at load time, so live tracking works off the exact
    # document the manipulators were built from.
    collision_setup: wb.models.CollisionSetup
    collider_manipulators: dict[str, ManipulatorMesh]
    link_chain_manipulator: MotionGroupMesh | None
    tool_manipulators: dict[str, ManipulatorMesh]


@dataclass
class CollisionSetupSelection:
    base_prim: Usd.Prim | None
    collision_setup_name: str
    api_configuration: ApiConfiguration
    cell: str
    # A robot in the scene to track for the tool and link chain overlay.
    # Without one there is no live pose to place them against, so only the
    # static colliders anchored at base_prim are shown.
    motion_group_prim: Usd.Prim | None = None


class CollisionWorldOverlay(ViewportOverlay):
    def __init__(self, name: str):
        self.name = name
        self._viewport: ViewportWindow | None = None
        self._view_frame: ui.VStack | None = None
        self._scene_view: ui_scene.SceneView | None = None
        self._collision_setups: dict[
            str, LoadedCollisionSetup
        ] = {}  # setup_name -> LoadedCollisionSetup
        self._selection: CollisionSetupSelection | None = None
        self._tcp_watcher: PrimPoseWatcher | None = None
        self._pose_watcher: PrimPoseWatcher | None = None

        self._stage_event_subscription = (
            cast(
                omni.usd.UsdContext,
                omni.usd.get_context(),
            )
            .get_stage_event_stream()
            .create_subscription_to_pop(
                lambda event, weak_self=weakref.proxy(self): weak_self._on_stage_event(
                    event
                ),
                name="collision_setup_overlay_stage_event",
            )
        )

        def on_color_changed(
            value: carb.dictionary.Item,
            change_type: carb.settings.ChangeEventType,
            weak_self=weakref.ref(self),
        ):
            self_instance = weak_self()
            if not self_instance:
                return
            if change_type == carb.settings.ChangeEventType.CHANGED:
                dict = carb.dictionary.acquire_dictionary_interface()
                color = color_utils.hex_to_float_array(dict.get_as_string(value))
                for loaded_collision_setup in self_instance._collision_setups.values():
                    for mesh in loaded_collision_setup.collider_manipulators.values():
                        mesh.color = color
                    if loaded_collision_setup.link_chain_manipulator:
                        link_chain = loaded_collision_setup.link_chain_manipulator
                        for mesh in link_chain.meshes:
                            mesh.color = color
                    for mesh in loaded_collision_setup.tool_manipulators.values():
                        mesh.color = color

        self._color_setting_subscription = SettingChangeSubscription(
            CARB_OVERLAY_COLOR,
            on_color_changed,
        )

    def attach_to_viewport(self, viewport: ViewportWindow):
        self._viewport = viewport
        if not self._viewport:
            carb.log_warn(
                f"Overlay '{self.name}' could not be attached to viewport: No viewport provided."
            )
            return
        carb.log_info(f"Overlay '{self.name}' attached to viewport.")

        self._initialize_scene()
        run_coroutine(self.load_scene_models())

    def _initialize_scene(self):
        with self._viewport.get_frame(self.name):
            self._view_frame = ui.Frame(content_clipping=False)
            with self._view_frame:
                self._scene_view = ui_scene.SceneView()
                with self._scene_view.scene:
                    pass
        self._viewport.viewport_api.add_scene_view(self._scene_view)

    async def load_scene_models(self):
        if self._selection is None:
            return

        if not self._selection.base_prim:
            carb.log_info("Base prim is None, falling back to /World")
            stage: Usd.Stage = omni.usd.get_context().get_stage()
            self._selection.base_prim = stage.GetPrimAtPath("/World")

        carb.log_verbose("Loading motion group collider meshes...")

        # Always rebuild from a clean slate. Reusing manipulators is how stale
        # geometry from an earlier export of the same setup name survives a
        # re-load.
        self._pose_watcher = None
        self._collision_setups.clear()
        self._scene_view.scene.clear()

        nm.post_notification(
            text=f"Loading collision setup {self._selection.collision_setup_name}",
        )
        with self._scene_view.scene:
            await self.load_collision_setup(
                self._selection.collision_setup_name,
                self._selection.base_prim,
                self._selection.motion_group_prim,
            )

        if self._selection.collision_setup_name not in self._collision_setups:
            # Nothing was built, so there is nothing to track.
            return

        carb.log_verbose("Motion group collider meshes loaded.")

        if self._selection.motion_group_prim is None:
            # No robot to track, and the meshes are already in place.
            return

        manipulators = self._collision_setups[self._selection.collision_setup_name]
        tool_manipulators = manipulators.tool_manipulators
        link_chain_manipulator = manipulators.link_chain_manipulator
        collision_setup = manipulators.collision_setup

        # The prim's own MotionGroupAPI attributes may carry a stale host, so
        # only the robot's identity is taken from it and the calls go to the
        # instance the user selected.
        motion_stream_configuration = get_motion_group_configuration_from_prim(
            self._selection.motion_group_prim
        ).motion_stream_configuration

        api_client_config = self._selection.api_configuration
        cell = self._selection.cell

        async def _pose_changed_fn(pose: Pose, weak_self=weakref.ref(self)):
            if weak_self() is None:
                return

            stage_meters_per_unit = SceneUtils.get_stage_units()
            if link_chain_manipulator is not None:
                await _apply_live_joint_state(
                    link_chain_manipulator,
                    api_client_config,
                    cell,
                    motion_stream_configuration,
                )

            # The tool hangs off the same chain as the link meshes, so both
            # halves of the robot-mounted display share one source of truth.
            # The stage TCP prim only serves as the anchor when there is no
            # chain to follow, since a stopped timeline leaves it at a stale
            # pose while the chain follows NOVA's state.
            anchor_transform = (
                link_chain_manipulator.flange_transform
                if link_chain_manipulator is not None
                else None
            )
            if anchor_transform is None:
                anchor_transform = nova_pose_to_scene_matrix(
                    pose.pose, stage_meters_per_unit
                )

            _position_collider_manipulators(
                tool_manipulators,
                collision_setup.tool,
                anchor_transform,
                stage_meters_per_unit,
            )

        tcp_prim = SchemaUtils.find_motion_group_tcp(self._selection.motion_group_prim)
        if not tcp_prim:
            carb.log_warn(
                f"Could not find TCP prim for motion group at {self._selection.motion_group_prim.GetPath()}"
            )
            return

        self._pose_watcher = PrimPoseWatcher(
            prim=tcp_prim,
            pose_changed_fn=lambda pose: run_coroutine(_pose_changed_fn(pose)),
        )

        await _pose_changed_fn(self._pose_watcher.current_pose)

    async def load_collision_setup(
        self,
        collision_setup_name: str,
        base_prim: Usd.Prim,
        motion_group_prim: Usd.Prim | None,
    ):
        if collision_setup_name in self._collision_setups:
            carb.log_info(f"Refreshing collision setup '{collision_setup_name}'")

        collision_setup = await self._fetch_collision_setup(collision_setup_name)
        if collision_setup is None:
            return

        carb.log_info(
            f"Loaded collision setup with: {len(collision_setup.colliders.keys())} colliders"
        )

        if motion_group_prim is not None and motion_group_prim.IsValid():
            self._warn_on_motion_group_mismatch(
                collision_setup, collision_setup_name, motion_group_prim
            )

        stage_meters_per_unit = SceneUtils.get_stage_units(base_prim.GetStage())
        base_prim_transform = nova_pose_to_scene_matrix(
            PrimUtils.get_prim_pose(
                base_prim.GetPath(),
                coordinate_system="world",
                stage=base_prim.GetStage(),
            ).pose,
            stage_meters_per_unit,
        )
        collider_manipulators = _build_collider_manipulators(
            collision_setup.colliders,
            base_prim_transform,
            stage_meters_per_unit,
            color_utils.hex_to_float_array(self.overlay_color),
        )

        # Tool colliders are flange-relative and the link chain needs live joint
        # state, so both halves exist only with a motion group to track.
        tool_manipulators = {}
        link_chain_manipulator = None
        if motion_group_prim is not None:
            tool_manipulators = self._build_tool_manipulators(
                collision_setup, motion_group_prim, stage_meters_per_unit
            )
            link_chain_manipulator = await self._build_link_chain_manipulator(
                collision_setup, motion_group_prim
            )

        self._collision_setups[collision_setup_name] = LoadedCollisionSetup(
            setup_name=collision_setup_name,
            collision_setup=collision_setup,
            collider_manipulators=collider_manipulators,
            tool_manipulators=tool_manipulators,
            link_chain_manipulator=link_chain_manipulator,
        )

    async def _fetch_collision_setup(
        self, collision_setup_name: str
    ) -> wb.models.CollisionSetup | None:
        """Read the setup from NOVA, bypassing the cache: an explicit load must
        reflect what is stored right now, not a copy from an earlier session."""
        collision_setup = (
            await get_collision_export_service().get_collision_setup_by_cell(
                cell=self._selection.cell,
                api_configuration=self._selection.api_configuration,
                setup_name=collision_setup_name,
                force_refresh=True,
            )
        )
        if collision_setup is None:
            nm.post_notification(
                text=(
                    f"Collision setup '{collision_setup_name}' could not be loaded "
                    "from NOVA (it may have been deleted)."
                ),
                status=nm.NotificationStatus.WARNING,
            )
        return collision_setup

    def _warn_on_motion_group_mismatch(
        self,
        collision_setup: wb.models.CollisionSetup,
        collision_setup_name: str,
        motion_group_prim: Usd.Prim,
    ) -> None:
        """Provenance check on the stored document: the robot-mounted content
        may come from another motion group. A heuristic, so it warns and loads
        the setup anyway."""
        mismatches = validate_setup_matches_motion_group(
            collision_setup, motion_group_prim
        )
        if not mismatches:
            return
        text = (
            f"Collision setup '{collision_setup_name}' may not match "
            "the selected motion group:\n- " + "\n- ".join(mismatches)
        )
        carb.log_warn(text)
        nm.post_notification(text=text, status=nm.NotificationStatus.WARNING)

    def _build_tool_manipulators(
        self,
        collision_setup: wb.models.CollisionSetup,
        motion_group_prim: Usd.Prim,
        stage_meters_per_unit: float,
    ) -> dict[str, ManipulatorMesh]:
        """Build the tool meshes anchored at the robot's current TCP pose on the
        stage. Anchoring them at the base prim instead would draw them at the
        world origin until live tracking repositions them."""
        if not collision_setup.tool:
            return {}

        tcp_prim = SchemaUtils.find_motion_group_tcp(motion_group_prim)
        if not tcp_prim or not tcp_prim.IsValid():
            carb.log_warn(
                f"Could not find TCP prim for motion group at "
                f"{motion_group_prim.GetPath()} - tool colliders are "
                "flange-relative and cannot be positioned, skipping them."
            )
            return {}

        tcp_transform = nova_pose_to_scene_matrix(
            PrimUtils.get_prim_pose(
                tcp_prim.GetPath(),
                coordinate_system="world",
                stage=motion_group_prim.GetStage(),
            ).pose,
            stage_meters_per_unit,
        )
        return _build_collider_manipulators(
            collision_setup.tool,
            tcp_transform,
            stage_meters_per_unit,
            color_utils.hex_to_float_array(self.overlay_color),
        )

    async def _build_link_chain_manipulator(
        self,
        collision_setup: wb.models.CollisionSetup,
        motion_group_prim: Usd.Prim,
    ) -> MotionGroupMesh | None:
        """Build the robot's link chain. Non-fatal: it is an enhancement on top
        of the static colliders, so a robot that cannot be reached must not keep
        the rest of the setup from rendering."""
        configuration = get_motion_group_configuration_from_prim(motion_group_prim)
        stream = configuration.motion_stream_configuration if configuration else None
        if stream is None or not (stream.controller and stream.motion_group):
            # NOVA needs the controller and motion group names to describe the
            # robot; a prim that was never connected carries neither.
            carb.log_info(
                f"No link chain for '{motion_group_prim.GetPath()}': the robot is "
                "not assigned to a NOVA controller."
            )
            nm.post_notification(
                text=(
                    "Loaded the collision setup without the robot: "
                    f"'{motion_group_prim.GetName()}' is not assigned to a NOVA "
                    "controller, so its link chain cannot be fetched."
                ),
                status=nm.NotificationStatus.WARNING,
            )
            return None

        try:
            link_chain_manipulator = MotionGroupMesh(
                motion_group_prim=motion_group_prim,
                color=color_utils.hex_to_float_array(self.overlay_color),
                filled=False,
                api_configuration=self._selection.api_configuration,
                cell=self._selection.cell,
            )
            await link_chain_manipulator.load_meshes(
                extra_link_colliders=collision_setup.link_chain
            )
        except (*_NOVA_REQUEST_ERRORS, ValueError) as exc:
            reason = describe_api_error(exc)
            carb.log_warn(
                f"Could not load link chain for motion group "
                f"'{motion_group_prim.GetPath()}': {reason}"
            )
            nm.post_notification(
                text=(
                    "Loaded collision setup without link chain: the motion "
                    f"group could not be reached ({reason})."
                ),
                status=nm.NotificationStatus.WARNING,
            )
            return None

        # The link meshes are built hidden, so they need to be shown once the
        # chain is complete.
        link_chain_manipulator.visible = True
        return link_chain_manipulator

    def __del__(self):
        carb.log_verbose(f"Overlay '{self.name}' detached from viewport.")

        if self._viewport and self._scene_view:
            self._viewport.viewport_api.remove_scene_view(self._scene_view)
            self._viewport = None
            self._scene_view = None

    def _hide_collision_setups(self):
        for collision_setups in self._collision_setups.values():
            for mesh in collision_setups.collider_manipulators.values():
                mesh.visible = False
            for mesh in collision_setups.tool_manipulators.values():
                mesh.visible = False
            if collision_setups.link_chain_manipulator:
                collision_setups.link_chain_manipulator.visible = False

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type in (
            int(omni.usd.StageEventType.CLOSED),
            int(omni.usd.StageEventType.OPENED),
        ):
            self._hide_collision_setups()

    @property
    def overlay_color(self) -> str:
        return get_overlay_color()

    @overlay_color.setter
    def overlay_color(self, value: str):
        set_overlay_color(value)

    @property
    def selection(self) -> CollisionSetupSelection | None:
        return self._selection

    @selection.setter
    def selection(self, value: CollisionSetupSelection | None):
        self._selection = value
        if value is None:
            self._collision_setups.clear()
            self._pose_watcher = None
            if self._scene_view:
                self._scene_view.scene.clear()
            return
        run_coroutine(self.load_scene_models())
