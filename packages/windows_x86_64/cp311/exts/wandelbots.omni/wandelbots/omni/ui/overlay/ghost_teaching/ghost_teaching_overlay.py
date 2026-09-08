import asyncio
import weakref
from typing import Callable, cast

import carb
import carb.dictionary
import carb.events
import carb.settings
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.ui_scene as ui_scene
import omni.usd
from omni.usd import get_watcher
import wandelbots_api_client.v2 as wb
from omni.kit.app import SettingChangeSubscription
from omni.kit.async_engine import run_coroutine
from omni.kit.viewport.window import ViewportWindow
from pxr import Usd

import wandelbots.omni.ui.colors as color_utils
import wandelbots.omni.ui.overlay as overlay
from wandelbots.omni.core.collision.collision_export_service import (
    get_collision_export_service,
)
from wandelbots.omni.datatypes import Pose, WSPose
from wandelbots.omni.ui.overlay.manipulators import MotionGroupMesh
from wandelbots.omni.ui.overlay.overlay import ViewportOverlay
from wandelbots.omni.utils.kinematics import (
    InverseKinematicsResult,
    fetch_joint_configs_for_pose,
    sort_joint_configs_by_proximity,
)
from wandelbots.omni.utils.api import get_api_client_from_config
from wandelbots.omni.manipulators import (
    get_motion_group_current_joint_positions,
    get_motion_group_configuration_from_prim,
)
from wandelbots.omni.utils.prims import PrimPoseWatcher
from wandelbots.omni.instances.events import (
    subscribe_to_motion_group_connection_changed,
)
from wandelbots.omni.utils.teaching import (
    CARB_SETTINGS_PREFIX,
    PREFERRED_JOINT_VALUES_ATTR,
    GhostObject,
    GhostObjectUtils,
)


CARB_OVERLAY_VISIBLE = f"{CARB_SETTINGS_PREFIX}/overlay_visible"
CARB_OVERLAY_COLOR = f"{CARB_SETTINGS_PREFIX}/overlay_color"
CARB_MAX_JOINT_CONFIGS = f"{CARB_SETTINGS_PREFIX}/max_joint_configs"

GHOST_TEACHING_OVERLAY_NAME = "GhostTeachingOverlay"

EXPERIMENTAL_COLLISION_SETUP_INTEGRATION = False


class GhostTeachingOverlay(ViewportOverlay):
    def __init__(self, name: str):
        self.name = name
        self._viewport: ViewportWindow | None = None
        self._view_vstack: ui.VStack | None = None
        self._scene_view: ui_scene.SceneView | None = None
        self._motion_group_colliders: dict[str, list[MotionGroupMesh]] = {}
        self._pose_watcher: PrimPoseWatcher | None = None
        self._selected_ghost_object: Usd.Prim | None = (
            self._get_selected_ghost_object_prim()
        )
        self._cached_joints: list[list[float]] = []
        self._cached_joint_limits: list[tuple[float, float]] = []
        self._cached_description: wb.models.MotionGroupDescription | None = None
        self._active_colliders: list[MotionGroupMesh] = []
        self._stream_config = None
        self._motion_group_prim_path: str | None = None
        self._tcp_offset: WSPose | None = None
        # When the Trajectory Planner window is open it shows its own preview
        # overlay; suppress this ghost-teaching overlay so the two don't render at
        # once. Toggled via set_suppressed() from the planner window.
        self._suppressed: bool = False
        self._collision_setups: dict[str, wb.models.CollisionSetup] = {}
        self._load_task: asyncio.Task | None = None
        self.joint_configs_changed_fn: (
            Callable[[InverseKinematicsResult], None] | None
        ) = None

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
                name="ghost_teaching_overlay_stage_event",
            )
        )

        def on_visibility_changed(
            value: bool,
            change_type: carb.settings.ChangeEventType,
            weak_self=weakref.ref(self),
        ):
            self_instance = weak_self()
            if not self_instance:
                return
            if change_type == carb.settings.ChangeEventType.CHANGED:
                self_instance._scene_view.visible = value

        self._visible_setting_subscription = SettingChangeSubscription(
            CARB_OVERLAY_VISIBLE,
            on_visibility_changed,
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
                for meshes in self_instance._motion_group_colliders.values():
                    for mesh in meshes:
                        mesh.color = color

        self._color_setting_subscription = SettingChangeSubscription(
            CARB_OVERLAY_COLOR,
            on_color_changed,
        )

        self._motion_group_connection_sub = (
            subscribe_to_motion_group_connection_changed(
                lambda payload, weak_self=weakref.ref(self): (
                    weak_self()._on_motion_group_connection_changed(payload)
                    if weak_self()
                    else None
                )
            )
        )

    def _on_motion_group_connection_changed(self, payload: dict) -> None:
        if self._selected_ghost_object is None:
            return
        if self._stream_config is not None:
            if (
                payload.get("cell") != self._stream_config.cell
                or payload.get("motion_group") != self._stream_config.motion_group
            ):
                return
        elif self._motion_group_prim_path is not None:
            if payload.get("prim_path") != self._motion_group_prim_path:
                return
        else:
            return
        self._motion_group_colliders.clear()
        self._cached_joints.clear()
        self._cached_joint_limits.clear()
        self._cached_description = None
        self._stream_config = None
        if self.joint_configs_changed_fn:
            self.joint_configs_changed_fn(
                InverseKinematicsResult(joint_configs=[], joint_limits=[])
            )
        if payload.get("action") == "connected":
            self._schedule_load_scene_models()

    def _schedule_load_scene_models(self) -> None:
        if self._load_task and not self._load_task.done():
            self._load_task.cancel()
        self._load_task = run_coroutine(self.load_scene_models())

    @property
    def is_loading(self) -> bool:
        return self._load_task is not None and not self._load_task.done()

    def attach_to_viewport(self, viewport: ViewportWindow):
        self._viewport = viewport
        if not self._viewport:
            carb.log_warn(
                f"Overlay '{self.name}' could not be attached to viewport: No viewport provided."
            )
            return
        carb.log_info(f"Overlay '{self.name}' attached to viewport.")

        self.initialize_scene()
        self._schedule_load_scene_models()

    def initialize_scene(self):
        with self._viewport.get_frame(self.name):
            self._view_vstack = ui.VStack(content_clipping=False)
            with self._view_vstack:
                self._scene_view = ui_scene.SceneView()
                with self._scene_view.scene:
                    pass
        self._scene_view.visible = self.visible
        self._viewport.viewport_api.add_scene_view(self._scene_view)

    async def load_scene_models(self):
        if self._selected_ghost_object is None:
            return
        stage: Usd.Stage = omni.usd.get_context().get_stage()

        motion_group_prim = (
            GhostObjectUtils.get_linked_motion_group_to_ghost_object_prim(
                self._selected_ghost_object
            )
        )
        if not motion_group_prim:
            carb.log_verbose(
                f"Could not find motion group prim linked to ghost object at {self._selected_ghost_object.GetPath()}"
            )
            return

        motion_group_config = get_motion_group_configuration_from_prim(
            motion_group_prim
        )
        if motion_group_config is None:
            carb.log_verbose(
                f"Motion group {motion_group_prim.GetPath()} has no configuration, skipping overlay load"
            )
            return
        try:
            await motion_group_config.check_connection()
        except Exception as exception:
            carb.log_verbose(
                f"Motion group not reachable, skipping overlay load: {exception}"
            )
            return

        if not self._selected_ghost_object or not self._selected_ghost_object.IsValid():
            carb.log_warn(
                f"Could not find ghost object prim at {self._selected_ghost_object.GetPath()}"
            )
            return

        ghost_object: GhostObject | None = GhostObjectUtils.get_ghost_object_from_prim(
            self._selected_ghost_object,
            motion_group_prim.GetPath().pathString,
        )
        if not ghost_object:
            carb.log_warn(
                f"Could not find ghost object for motion group at {motion_group_prim.GetPath().pathString}"
            )
            return

        self._tcp_offset = GhostObjectUtils.get_ghost_object_tcp_offset(
            self._selected_ghost_object
        )
        if self._tcp_offset is None:
            carb.log_warn(
                f"Could not find TCP offset for ghost object at {self._selected_ghost_object.GetPath()}"
            )
            return

        self._active_colliders = await self._fetch_motion_group_colliders(
            stage, motion_group_prim
        )
        if len(self._active_colliders) == 0:
            return

        carb.log_verbose("Loading motion group collider meshes...")

        if motion_group_prim.GetPath().pathString not in self._motion_group_colliders:
            if self.visible:
                nm.post_notification(
                    text=f"Loading {motion_group_prim.GetPath().pathString} collider mesh",
                )
            with self._scene_view.scene:
                for mesh in self._active_colliders:
                    await mesh.load_meshes()
            self._motion_group_colliders[motion_group_prim.GetPath().pathString] = (
                self._active_colliders
            )
        else:
            for mesh in self._motion_group_colliders[
                motion_group_prim.GetPath().pathString
            ]:
                with self._scene_view.scene:
                    for _, _, child in mesh._link_meshes:
                        child.invalidate()

        carb.log_verbose("Motion group collider meshes loaded.")

        for meshes in self._motion_group_colliders.values():
            for mesh in meshes:
                mesh.visible = False

        self._stream_config = self._active_colliders[
            0
        ].motion_group_configuration.motion_stream_configuration
        self._motion_group_prim_path = motion_group_prim.GetPath().pathString

        # Fetch and cache the motion group description once per selection so it
        # does not need to be re-fetched on every IK call as the ghost is moved.
        try:
            api_config = self._stream_config.get_api_configuration()
            async with get_api_client_from_config(api_config) as api_client:
                self._cached_description = await wb.MotionGroupApi(
                    api_client
                ).get_motion_group_description(
                    cell=self._stream_config.cell,
                    controller=self._stream_config.controller,
                    motion_group=self._stream_config.motion_group,
                )
        except Exception as e:
            carb.log_warn(f"Could not pre-fetch motion group description: {e}")
            self._cached_description = None

        self._collision_setups = {}
        if EXPERIMENTAL_COLLISION_SETUP_INTEGRATION:
            collision_world_overlay: overlay.CollisionWorldOverlay = (
                overlay.get_overlay_registry().get_overlay(
                    overlay.COLLISION_WORLD_OVERLAY_NAME
                )
            )
            if collision_world_overlay.selection:
                selection = collision_world_overlay.selection
                self._collision_setups[
                    "ghost_teaching"
                ] = await get_collision_export_service().get_collision_setup_by_cell(
                    cell=selection.cell,
                    api_configuration=selection.api_configuration,
                    setup_name=selection.collision_setup_name,
                )

        self._cached_joints.clear()

        self._preferred_values_sub = get_watcher().subscribe_to_change_info_path(
            self._selected_ghost_object.GetPath(),
            lambda path=None, weak_self=weakref.ref(self): (
                weak_self()._on_ghost_prim_changed(path) if weak_self() else None
            ),
        )

        self._max_joint_configs_sub = SettingChangeSubscription(
            CARB_MAX_JOINT_CONFIGS,
            lambda value, change_type, weak_self=weakref.ref(self): (
                weak_self()._apply_joint_configs()
                if weak_self() and change_type == carb.settings.ChangeEventType.CHANGED
                else None
            ),
        )

        self._pose_watcher = GhostObjectUtils.create_ghost_object_pose_watcher(
            ghost_object_prim=self._selected_ghost_object,
            pose_changed_fn=lambda pose, weak_self=weakref.ref(self): (
                run_coroutine(weak_self()._on_pose_changed(pose))
                if weak_self()
                else None
            ),
        )

        await self._on_pose_changed(self._pose_watcher.current_pose)

    async def update_tcp_offset(self, tcp_name: str) -> None:
        """Re-fetch the TCP offset by name and re-run IK for the current pose."""
        if not self._stream_config or not self._cached_description:
            return
        tcps = self._cached_description.tcps or {}
        tcp_offset_obj = tcps.get(tcp_name)
        if tcp_offset_obj is None:
            carb.log_warn(f"TCP '{tcp_name}' not found in motion group description")
            return
        pose = tcp_offset_obj.pose
        self._tcp_offset = WSPose(pose=[*pose.position, *pose.orientation])
        if self._pose_watcher:
            await self._on_pose_changed(self._pose_watcher.current_pose)

    async def _on_pose_changed(self, pose: Pose):
        if not self._stream_config:
            return
        preferred = GhostObjectUtils.get_preferred_joint_values(
            self._selected_ghost_object
        )

        description = self._cached_description
        if description is not None and (
            description.operation_limits is None
            or description.operation_limits.auto_limits is None
        ):
            try:
                api_config = self._stream_config.get_api_configuration()
                async with get_api_client_from_config(api_config) as api_client:
                    description = await wb.MotionGroupApi(
                        api_client
                    ).get_motion_group_description(
                        cell=self._stream_config.cell,
                        controller=self._stream_config.controller,
                        motion_group=self._stream_config.motion_group,
                    )
                self._cached_description = description
            except Exception as e:
                carb.log_verbose(f"Could not refresh motion group description: {e}")

        ik_result = await fetch_joint_configs_for_pose(
            stream_config=self._stream_config,
            pose=pose,
            tcp_offset=self._tcp_offset,
            preferred_joint_values=preferred,
            collision_setups=self._collision_setups or None,
            description=description,
        )

        if len(ik_result.joint_configs) == 0:
            carb.log_warn(
                "No inverse kinematics solution found for ghost object "
                f"'{self._selected_ghost_object.GetPath()}' at pose {pose} — the "
                "pose may be unreachable with the linked TCP, or the motion group "
                "description was not ready yet."
            )
            for mesh in self._active_colliders:
                mesh.visible = False
            return

        if len(ik_result.joint_configs) > len(self._active_colliders):
            carb.log_warn(
                f"IK returned {len(ik_result.joint_configs)} configs but only {len(self._active_colliders)} meshes available."
            )

        self._cached_joints.clear()
        self._cached_joints.extend(ik_result.joint_configs)
        self._cached_joint_limits = ik_result.joint_limits

        if self._motion_group_prim_path:
            mg_prim = (
                omni.usd.get_context()
                .get_stage()
                .GetPrimAtPath(self._motion_group_prim_path)
            )
            current_positions = get_motion_group_current_joint_positions(mg_prim)
            self._cached_joints[:] = sort_joint_configs_by_proximity(
                self._cached_joints, current_positions
            )

        self._apply_joint_configs()
        if self.joint_configs_changed_fn:
            self.joint_configs_changed_fn(
                InverseKinematicsResult(
                    joint_configs=list(self._cached_joints),
                    joint_limits=list(self._cached_joint_limits),
                )
            )

    def _on_ghost_prim_changed(self, path=None):
        if not path or PREFERRED_JOINT_VALUES_ATTR not in path.pathString:
            return
        self._apply_joint_configs()
        if self.joint_configs_changed_fn:
            self.joint_configs_changed_fn(
                InverseKinematicsResult(
                    joint_configs=list(self._cached_joints),
                    joint_limits=list(self._cached_joint_limits),
                )
            )

    def set_suppressed(self, suppressed: bool) -> None:
        """Hide (or restore) the ghost overlay while another overlay owns the view.

        Called by the Trajectory Planner window so that only its own preview shows
        while it is open. Restoring re-renders the current selection's IK configs.
        """
        if suppressed == self._suppressed:
            return
        self._suppressed = suppressed
        if suppressed:
            for meshes in self._motion_group_colliders.values():
                for mesh in meshes:
                    mesh.visible = False
            for mesh in self._active_colliders:
                mesh.visible = False
        elif self._selected_ghost_object and self._selected_ghost_object.IsValid():
            self._apply_joint_configs()

    def _apply_joint_configs(self):
        if self._suppressed:
            for mesh in self._active_colliders:
                mesh.visible = False
            return
        joints = self._cached_joints
        colliders = self._active_colliders
        if not joints:
            for mesh in colliders:
                mesh.visible = False
            return

        max_display = (
            carb.settings.get_settings().get_as_int(CARB_MAX_JOINT_CONFIGS) or 9
        )
        stored_preferred = GhostObjectUtils.get_preferred_joint_values(
            self._selected_ghost_object
        )
        preferred_idx = None
        if stored_preferred is not None:
            for i, config in enumerate(joints):
                if len(config) == len(stored_preferred) and all(
                    abs(a - b) < 1e-4 for a, b in zip(config, stored_preferred)
                ):
                    preferred_idx = i
                    break
        base_color = color_utils.hex_to_float_array(self.overlay_color)

        usable_count = min(len(joints), len(colliders), max_display)
        usable = list(joints[:usable_count])

        if (
            preferred_idx is not None
            and preferred_idx >= usable_count
            and preferred_idx < len(joints)
        ):
            usable[-1] = joints[preferred_idx]
            preferred_idx = usable_count - 1

        order = list(range(len(usable)))
        if preferred_idx is not None and preferred_idx < len(usable):
            order.remove(preferred_idx)
            order.append(preferred_idx)

        for mesh_slot, joint_idx in enumerate(order):
            colliders[mesh_slot].set_joint_values(usable[joint_idx])
            colliders[mesh_slot].visible = True
            colliders[mesh_slot].color = base_color
            colliders[mesh_slot].filled = joint_idx == preferred_idx

        for idx in range(len(usable), len(colliders)):
            colliders[idx].set_joint_values(
                [0.0 for _ in range(colliders[idx].joint_count)]
            )
            colliders[idx].visible = False

    async def _fetch_motion_group_colliders(
        self, stage: Usd.Stage, motion_group_prim: Usd.Prim
    ) -> list[MotionGroupMesh]:
        try:
            return self._motion_group_colliders.get(
                motion_group_prim.GetPath().pathString,
                [
                    MotionGroupMesh(
                        motion_group_prim=stage.GetPrimAtPath(
                            motion_group_prim.GetPath().pathString
                        ),
                        color=color_utils.hex_to_float_array(self.overlay_color),
                        filled=False,
                    )
                    for _ in range(9)
                ],
            )
        except Exception as e:
            carb.log_warn(
                f"Failed to fetch motion group colliders for motion group at {motion_group_prim.GetPath().pathString}: {e}"
            )
            return []

    def __del__(self):
        carb.log_verbose(f"Overlay '{self.name}' detached from viewport.")
        if self._viewport and self._scene_view:
            self._viewport.viewport_api.remove_scene_view(self._scene_view)
            self._viewport = None
            self._scene_view = None

    def _reset_selection(self):
        self._selected_ghost_object = None
        self._cached_description = None
        self._stream_config = None
        self._cached_joints.clear()
        self._cached_joint_limits.clear()
        for meshes in self._motion_group_colliders.values():
            for mesh in meshes:
                mesh.visible = False

    def _get_selected_ghost_object_prim(self) -> Usd.Prim | None:
        prim_paths: list[str] = (
            omni.usd.get_context().get_selection().get_selected_prim_paths()
        )

        prims: list[Usd.Prim] = [
            omni.usd.get_context().get_stage().GetPrimAtPath(prim_path)
            for prim_path in prim_paths
        ]

        ghost_objects = [
            prim
            for prim in prims
            if prim.IsValid() and GhostObjectUtils.is_ghost_object(prim)
        ]

        if not ghost_objects:
            return None
        return ghost_objects[0]

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type == int(omni.usd.StageEventType.SELECTION_CHANGED):
            prim = self._get_selected_ghost_object_prim()
            if not prim:
                self._reset_selection()
                return
            self._selected_ghost_object = prim
            self._schedule_load_scene_models()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._reset_selection()
        elif event.type == int(omni.usd.StageEventType.OPENED):
            self._reset_selection()

    @property
    def cached_joints(self) -> list[list[float]]:
        return list(self._cached_joints)

    @property
    def cached_joint_limits(self) -> list[tuple[float, float]]:
        return list(self._cached_joint_limits)

    @property
    def visible(self) -> bool:
        settings: carb.settings.ISettings = carb.settings.get_settings()
        if settings.get(CARB_OVERLAY_VISIBLE) is None:
            return True
        return settings.get_as_bool(CARB_OVERLAY_VISIBLE)

    @property
    def overlay_color(self) -> str:
        settings: carb.settings.ISettings = carb.settings.get_settings()
        setting_color = settings.get_as_string(CARB_OVERLAY_COLOR)
        if not setting_color or setting_color == "":
            return "#A936DA16"
        return setting_color
