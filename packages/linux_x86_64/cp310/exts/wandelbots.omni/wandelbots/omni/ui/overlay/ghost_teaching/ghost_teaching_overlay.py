import weakref
from typing import cast

import carb
import carb.dictionary
import carb.events
import carb.settings
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.ui_scene as ui_scene
import omni.usd
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
from wandelbots.omni.utils.api import get_api_client_from_config
from wandelbots.omni.utils.prims import PrimPoseWatcher
from wandelbots.omni.utils.teaching import (
    CARB_SETTINGS_PREFIX,
    GhostObject,
    GhostObjectUtils,
)


CARB_OVERLAY_VISIBLE = f"{CARB_SETTINGS_PREFIX}/overlay_visible"
CARB_OVERLAY_COLOR = f"{CARB_SETTINGS_PREFIX}/overlay_color"

GHOST_TEACHING_OVERLAY_NAME = "GhostTeachingOverlay"

EXPERIMENTAL_COLLISION_SETUP_INTEGRATION = False


class GhostTeachingOverlay(ViewportOverlay):
    def __init__(self, name: str):
        self.name = name
        self._viewport: ViewportWindow | None = None
        self._view_vstack: ui.VStack | None = None
        self._scene_view: ui_scene.SceneView | None = None
        self._motion_group_colliders: dict[
            str, list[MotionGroupMesh]
        ] = {}  # motion_group_path -> list of meshes
        self._pose_watcher: PrimPoseWatcher | None = None
        self._selected_ghost_object: Usd.Prim | None = (
            self._get_selected_ghost_object_prim()
        )

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

    def attach_to_viewport(self, viewport: ViewportWindow):
        self._viewport = viewport
        if not self._viewport:
            carb.log_warn(
                f"Overlay '{self.name}' could not be attached to viewport: No viewport provided."
            )
            return
        carb.log_info(f"Overlay '{self.name}' attached to viewport.")

        self.initialize_scene()
        run_coroutine(self.load_scene_models())

    def initialize_scene(self):
        with self._viewport.get_frame(self.name):
            self._view_vstack = ui.VStack(content_clipping=False)
            with self._view_vstack:
                self._scene_view = ui_scene.SceneView()
                with self._scene_view.scene:
                    pass
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
            carb.log_warn(
                f"Could not find motion group prim linked to ghost object at {self._selected_ghost_object.GetPath()}"
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

        tcp_offset: WSPose | None = GhostObjectUtils.get_ghost_object_tcp_offset(
            self._selected_ghost_object
        )
        if tcp_offset is None:
            carb.log_warn(
                f"Could not find TCP offset for ghost object at {self._selected_ghost_object.GetPath()}"
            )
            return

        motion_group_colliders = await self._fetch_motion_group_colliders(
            stage, motion_group_prim
        )

        if len(motion_group_colliders) == 0:
            return

        carb.log_verbose("Loading motion group collider meshes...")

        if motion_group_prim.GetPath().pathString not in self._motion_group_colliders:
            if self.visible:
                # We load them anyway because it easier to already have them in cache and just toggle the scene visibility
                # We just do not show the user that we are fetching in the background
                nm.post_notification(
                    text=f"Loading {motion_group_prim.GetPath().pathString} collider mesh",
                )
            with self._scene_view.scene:
                for mesh in motion_group_colliders:
                    await mesh.load_meshes()
            self._motion_group_colliders[motion_group_prim.GetPath().pathString] = (
                motion_group_colliders
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

        stream_config = motion_group_colliders[
            0
        ].motion_group_configuration.motion_stream_configuration

        api_client_config = stream_config.get_api_configuration()

        collision_setups: dict[str, wb.models.CollisionSetup] = {}

        if EXPERIMENTAL_COLLISION_SETUP_INTEGRATION:
            collision_world_overlay: overlay.CollisionWorldOverlay = (
                overlay.get_overlay_registry().get_overlay(
                    overlay.COLLISION_WORLD_OVERLAY_NAME
                )
            )
            if collision_world_overlay.selection:
                selection = collision_world_overlay.selection
                selection.collision_setup_name
                collision_setups[
                    "ghost_teaching"
                ] = await get_collision_export_service().get_collision_setup(
                    selection.motion_group_prim, selection.collision_setup_name
                )

        async def _pose_changed_fn(pose: Pose, weak_self=weakref.ref(self)):
            weak_self_instance = weak_self()
            if not weak_self_instance:
                return

            async with get_api_client_from_config(api_client_config) as api_client:
                joint_limits = motion_group_colliders[
                    0
                ].motion_group_description.operation_limits.auto_limits
                joint_position_limits = (
                    [joint.position for joint in joint_limits.joints]
                    if joint_limits
                    else None
                )

                joints = await _fetch_joint_configurations(
                    api_client, pose, joint_position_limits
                )

                if len(joints) == 0:
                    carb.log_verbose(
                        f"No inverse kinematics solution found for motion group at {motion_group_prim.GetPath().pathString}"
                    )
                    for mesh in motion_group_colliders:
                        mesh.visible = False
                    return

                # render joint configs

                if len(joints) > len(motion_group_colliders):
                    carb.log_warn(
                        f"Number of joint configurations returned by IK ({len(joints)}) exceeds the number of motion group collider meshes ({len(motion_group_colliders)}). Consider increasing the number of collider meshes."
                    )

                for idx, joint_configuration in enumerate(
                    joints[: len(motion_group_colliders)]
                ):
                    motion_group_colliders[idx].set_joint_values(joint_configuration)
                    motion_group_colliders[idx].visible = True

                # hide unused motion group meshes
                for idx in range(
                    len(joints),
                    len(motion_group_colliders),
                ):
                    motion_group_colliders[idx].set_joint_values(
                        [0.0 for _ in range(motion_group_colliders[idx].joint_count)]
                    )
                    motion_group_colliders[idx].visible = False

        async def _fetch_joint_configurations(
            api_client, pose, joint_position_limits
        ) -> list[list[float]]:
            try:
                response = await wb.KinematicsApi(api_client).inverse_kinematics(
                    cell=stream_config.cell,
                    inverse_kinematics_request=wb.models.InverseKinematicsRequest(
                        motion_group_model=motion_group_colliders[
                            0
                        ].motion_group_description.motion_group_model,
                        joint_position_limits=joint_position_limits,
                        tcp_poses=[pose.to_nova_pose()],
                        tcp_offset=tcp_offset.to_nova_pose(),
                        collision_setups=collision_setups,
                    ),
                )
                return response.joints[0]
            except Exception as e:
                carb.log_verbose(f"Joint configurations could not be calculated: {e}")
                return []

        self._pose_watcher = GhostObjectUtils.create_ghost_object_pose_watcher(
            ghost_object_prim=self._selected_ghost_object,
            pose_changed_fn=lambda pose: run_coroutine(_pose_changed_fn(pose)),
        )

        await _pose_changed_fn(self._pose_watcher.current_pose)

    async def _fetch_motion_group_colliders(self, stage, motion_group_prim):
        try:
            return self._motion_group_colliders.get(
                motion_group_prim.GetPath().pathString,
                [
                    MotionGroupMesh(
                        motion_group_prim=stage.GetPrimAtPath(
                            motion_group_prim.GetPath().pathString
                        ),
                        color=color_utils.hex_to_float_array(self.overlay_color),
                        filled=True,
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

        if len(ghost_objects) == 0:
            return None
        return ghost_objects[0]

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type == int(omni.usd.StageEventType.SELECTION_CHANGED):
            prim = self._get_selected_ghost_object_prim()
            if not prim:
                self._reset_selection()
                return
            self._selected_ghost_object = prim
            run_coroutine(self.load_scene_models())
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._reset_selection()
        elif event.type == int(omni.usd.StageEventType.OPENED):
            self._reset_selection()

    @property
    def visible(self) -> bool:
        settings: carb.settings.ISettings = carb.settings.get_settings()
        return settings.get_as_bool(CARB_OVERLAY_VISIBLE)

    @property
    def overlay_color(self) -> str:
        settings: carb.settings.ISettings = carb.settings.get_settings()
        return settings.get_as_string(CARB_OVERLAY_COLOR)
