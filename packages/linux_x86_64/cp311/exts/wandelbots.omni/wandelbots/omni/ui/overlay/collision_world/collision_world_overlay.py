from dataclasses import dataclass
from typing import cast
import carb.events
import carb
import weakref
from omni.kit.viewport.window import ViewportWindow
import omni.ui as ui
import omni.ui_scene as ui_scene
from wandelbots.omni.core.collision.collision_export_service import (
    get_collision_export_service,
)
from wandelbots.omni.ui.overlay.collision_world.utils import (
    CARB_OVERLAY_COLOR,
    CARB_OVERLAY_RENDER_MODE,
    RenderMode,
    get_overlay_color,
    get_overlay_render_mode,
    set_overlay_color,
    set_overlay_render_mode,
)
from wandelbots.omni.usd.schema_utils import SchemaUtils
from wandelbots.omni.utils.prims import PrimUtils, PrimPoseWatcher
from wandelbots.omni.ui.overlay.overlay import ViewportOverlay
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


COLLISION_WORLD_OVERLAY_NAME = "CollisionWorldOverlay"


@dataclass
class LoadedCollisionSetup:
    setup_name: str
    collider_manipulators: dict[str, ManipulatorMesh]
    link_chain_manipulator: MotionGroupMesh
    tool_manipulators: dict[str, ManipulatorMesh]


@dataclass
class CollisionSetupSelection:
    base_prim: Usd.Prim | None
    motion_group_prim: Usd.Prim
    collision_setup_name: str


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
        self._collision_export_service = get_collision_export_service()
        self._selected_prim_path: str | None = None

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
                    for (
                        _,
                        _,
                        mesh,
                    ) in loaded_collision_setup.link_chain_manipulator._link_meshes:
                        mesh.color = color
                    for mesh in loaded_collision_setup.tool_manipulators.values():
                        mesh.color = color

        self._color_setting_subscription = SettingChangeSubscription(
            CARB_OVERLAY_COLOR,
            on_color_changed,
        )

        def on_render_mode_changed(
            value: str,
            change_type: carb.settings.ChangeEventType,
            weak_self=weakref.ref(self),
        ):
            self_instance = weak_self()
            if not self_instance:
                return
            if change_type == carb.settings.ChangeEventType.CHANGED:
                self_instance._update_collider_visibility()

        self._render_mode_setting_subscription = SettingChangeSubscription(
            CARB_OVERLAY_RENDER_MODE,
            on_render_mode_changed,
        )

    def _update_collider_visibility(self):
        if self._selection is None:
            return

        stage: Usd.Stage = self._selection.base_prim.GetStage()

        render_mode = self.render_mode

        selection: omni.usd.Selection = omni.usd.get_context().get_selection()
        selected_prims: list[Usd.Prim] = [
            stage.GetPrimAtPath(prim_path)
            for prim_path in selection.get_selected_prim_paths()
        ]

        def is_in_selection_range(prim_path: str) -> bool:
            for selected_prim in selected_prims:
                selected_path = str(selected_prim.GetPath())
                if prim_path == selected_path or prim_path.startswith(
                    selected_path + "/"
                ):
                    return True
            return False

        for loaded_collision_setup in self._collision_setups.values():
            # Colliders
            for (
                collider_id,
                mesh,
            ) in loaded_collision_setup.collider_manipulators.items():
                if render_mode == "None":
                    mesh.visible = False
                elif render_mode == "All":
                    mesh.visible = True
                elif render_mode == "Selected":
                    mesh.visible = is_in_selection_range(collider_id)
                mesh.invalidate()

            # Tool
            for collider_id, mesh in loaded_collision_setup.tool_manipulators.items():
                if render_mode == "None":
                    mesh.visible = False
                elif render_mode == "All":
                    mesh.visible = True
                elif render_mode == "Selected":
                    mesh.visible = is_in_selection_range(collider_id)
                mesh.invalidate()

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
        stage: Usd.Stage = self._selection.base_prim.GetStage()

        carb.log_verbose("Loading motion group collider meshes...")

        loaded_collision_setup = self._collision_setups.get(
            self._selection.collision_setup_name
        )

        if loaded_collision_setup is None:
            if self.visible:
                # We load them anyway because it easier to already have them in cache and just toggle the scene visibility
                # We just do not show the user that we are fetching in the background
                nm.post_notification(
                    text=f"Loading collision setup {self._selection.collision_setup_name}",
                )
            self._scene_view.scene.clear()
            with self._scene_view.scene:
                await self.load_collision_setup(
                    self._selection.collision_setup_name,
                    self._selection.base_prim,
                    self._selection.motion_group_prim,
                )
        else:
            collision_setup = self._collision_setups[
                self._selection.collision_setup_name
            ]
            for mesh in collision_setup.collider_manipulators.values():
                mesh.color = color_utils.hex_to_float_array(self.overlay_color)
                mesh.invalidate()
            for _, _, mesh in collision_setup.link_chain_manipulator._link_meshes:
                mesh.color = color_utils.hex_to_float_array(self.overlay_color)
                mesh.invalidate()

        carb.log_verbose("Motion group collider meshes loaded.")

        tool_manipulators = self._collision_setups[
            self._selection.collision_setup_name
        ].tool_manipulators

        collision_setup = await self._collision_export_service.get_collision_setup(
            motion_group_prim=self._selection.motion_group_prim,
            setup_name=self._selection.collision_setup_name,
        )

        async def _pose_changed_fn(pose: Pose, weak_self=weakref.ref(self)):
            weak_self_instance = weak_self()
            if not weak_self_instance:
                return

            stage_meters_per_unit = SceneUtils.get_stage_units()
            unit_factor = stage_meters_per_unit / 1000.0  # mm to stage units
            tcp_transform = nova_pose_to_scene_matrix(pose.pose, stage_meters_per_unit)

            for collider_id, collider_manipulator in tool_manipulators.items():
                # collider_manipulator.visible = False
                collider_pose = collision_setup.tool[collider_id].pose
                collider_manipulator.set_transform(
                    tcp_transform
                    * nova_pose_to_scene_matrix(
                        collider_pose.position + collider_pose.orientation,
                        stage_meters_per_unit,
                    )
                    * sc.Matrix44.get_scale_matrix(
                        unit_factor,
                        unit_factor,
                        unit_factor,  # Scale vertices from mm
                    )
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
        motion_group_prim: Usd.Prim,
    ):
        if collision_setup_name in self._collision_setups:
            carb.log_info(f"Refreshing collision setup '{collision_setup_name}'")

        collision_setup = await get_collision_export_service().get_collision_setup(
            motion_group_prim=motion_group_prim, setup_name=collision_setup_name
        )

        carb.log_info(
            f"Loaded collision setup with: {len(collision_setup.colliders.keys())} colliders"
        )

        stage_meters_per_unit = SceneUtils.get_stage_units(base_prim.GetStage())
        unit_factor = stage_meters_per_unit / 1000.0  # mm to stage units

        # Motion group transform from USD prim pose
        base_prim_transform = nova_pose_to_scene_matrix(
            PrimUtils.get_prim_pose(
                base_prim.GetPath(),
                coordinate_system="world",
                stage=base_prim.GetStage(),
            ).pose,
            stage_meters_per_unit,
        )

        collider_manipulators = {}
        for collider_id, collider in collision_setup.colliders.items():
            # Collider pose: position in mm, orientation as rotation vector
            collider_pose = collider.pose.position + collider.pose.orientation
            collider_transform = nova_pose_to_scene_matrix(
                collider_pose, stage_meters_per_unit
            ) * sc.Matrix44.get_scale_matrix(
                unit_factor,
                unit_factor,
                unit_factor,  # Scale vertices from mm
            )

            transform = base_prim_transform * collider_transform

            mesh_manipulator = create_from_collider(
                collider,
                transform,
                color=color_utils.hex_to_float_array(self.overlay_color),
            )

            if not mesh_manipulator:
                carb.log_verbose(
                    f"Collider {collider_id} with shape type '{collider.shape.actual_instance.shape_type}' is not supported"
                )
                continue
            collider_manipulators[collider_id] = mesh_manipulator

        tool_manipulators = {}
        if collision_setup.tool:
            for collider_id, collider in collision_setup.tool.items():
                # Collider pose: position in mm, orientation as rotation vector
                collider_pose = collider.pose.position + collider.pose.orientation
                collider_transform = nova_pose_to_scene_matrix(
                    collider_pose, stage_meters_per_unit
                ) * sc.Matrix44.get_scale_matrix(
                    unit_factor,
                    unit_factor,
                    unit_factor,  # Scale vertices from mm
                )

                transform = base_prim_transform * collider_transform

                mesh_manipulator = create_from_collider(
                    collider,
                    transform,
                    color=color_utils.hex_to_float_array(self.overlay_color),
                )

                if not mesh_manipulator:
                    carb.log_warn(
                        f"Collider {collider_id} with shape type '{collider.shape.actual_instance.shape_type}' is not supported"
                    )
                    continue
                tool_manipulators[collider_id] = mesh_manipulator

        link_chain_manipulator = MotionGroupMesh(
            motion_group_prim=self._selection.motion_group_prim,
            color=color_utils.hex_to_float_array(self.overlay_color),
            filled=True,
        )
        await link_chain_manipulator.load_meshes()

        loaded_collision_setup = LoadedCollisionSetup(
            setup_name=collision_setup_name,
            collider_manipulators=collider_manipulators,
            tool_manipulators=tool_manipulators,
            link_chain_manipulator=link_chain_manipulator,
        )
        self._collision_setups[collision_setup_name] = loaded_collision_setup

        # Apply initial visibility based on render mode
        self._update_collider_visibility()

    def __del__(self):
        carb.log_verbose(f"Overlay '{self.name}' detached from viewport.")

        if self._viewport and self._scene_view:
            self._viewport.viewport_api.remove_scene_view(self._scene_view)
            self._viewport = None
            self._scene_view = None

    def _reset_selection(self):
        self._selected_ghost_object = None
        for collision_setups in self._collision_setups.values():
            for mesh in collision_setups.collider_manipulators.values():
                mesh.visible = False
            for mesh in collision_setups.tool_manipulators.values():
                mesh.visible = False
            if collision_setups.link_chain_manipulator:
                collision_setups.link_chain_manipulator.visible = False

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type == int(omni.usd.StageEventType.SELECTION_CHANGED):
            self._update_collider_visibility()
        if event.type == int(omni.usd.StageEventType.CLOSED):
            self._reset_selection()
        elif event.type == int(omni.usd.StageEventType.OPENED):
            self._reset_selection()

    @property
    def render_mode(self) -> RenderMode:
        return get_overlay_render_mode()

    @render_mode.setter
    def render_mode(self, value: RenderMode):
        set_overlay_render_mode(value)

    @property
    def overlay_color(self) -> str:
        return get_overlay_color()

    @overlay_color.setter
    def overlay_color(self, value: str):
        set_overlay_color(value)

    @property
    def visible(self) -> bool:
        return self.render_mode != "None"

    @property
    def selection(self) -> CollisionSetupSelection | None:
        return self._selection

    @selection.setter
    def selection(self, value: CollisionSetupSelection | None):
        self._selection = value
        if self._selection.collision_setup_name in self._collision_setups:
            del self._collision_setups[self._selection.collision_setup_name]
        run_coroutine(self.load_scene_models())
