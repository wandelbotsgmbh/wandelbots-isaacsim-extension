import asyncio
import carb.events
from typing import Any, Callable, cast
import weakref
from attr import dataclass
from pxr import Usd
import omni.kit.actions.core
import omni.ui as ui
import omni.kit.app
import omni.kit.viewport.utility
from .widgets.ghost_object_selector import GhostObjectSelector
from omni.kit.async_engine import run_coroutine
import omni.kit.menu.utils
from wandelbots.omni.utils.teaching import GhostObjectUtils, GhostObject
import carb.input
from wandelbots.omni.manipulators import get_motion_group_configuration_from_prim
import omni.kit.notification_manager as nm
import omni.usd
from wandelbots.omni.ui.widgets import TcpSelector
from wandelbots.omni.ui.utils import get_icon
from wandelbots.omni.ui.colors import NOVAColor
from .widgets.ghost_teaching_settings_window import (
    GhostTeachingSettingsWindow,
    SettingsModel,
    load_ghost_teaching_carb_settings,
    save_ghost_teaching_carb_settings,
)
from .widgets.move_to_button import MoveToButton, MoveToExecuteSettings
import wandelbots_api_client.v2.models as wb_models
import wandelbots.omni.ui.tool.action_planner.utils as planner_utils
from .utils import GhostObjectsSubscription

WINDOW_MENU_ROOT = "Tools"


class GhostTeachingToolBar:
    def __init__(self):
        self._tool_bar: ui.ToolBar = None
        self._ghost_objects: list = []
        self._motion_group_prim: Usd.Prim = None
        self._selected_ghost_object_path = None
        self._move_to_settings: MoveToExecuteSettings = None
        self._move_to_button: MoveToButton = None
        self._settings_model: SettingsModel = load_ghost_teaching_carb_settings()
        self._settings_window: GhostTeachingSettingsWindow | None = None
        self._tcp_selector: TcpSelector = None
        self._ghost_object_selector: GhostObjectSelector = None
        self._deferred_build_task: asyncio.Task = None
        self._ghost_objects_subscription: GhostObjectsSubscription = (
            GhostObjectsSubscription(
                ghost_object_changed_fn=lambda weak_self=weakref.proxy(
                    self
                ): weak_self._ghost_object_changed_fn()
            )
        )

        self._tcp_selection_cache: dict[
            str, str
        ] = {}  # "motion_group_prim_path": "tcp_name"

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
                name="ghost_teaching_stage_event",
            )
        )

        self._settings_model.property_changed_fn = (
            lambda prop,
            old,
            new,
            obj=weakref.proxy(self): obj._on_settings_property_changed(prop, old, new)
        )

        # Select the motion group if a ghost object is already selected in the scene while this windows is being build
        self._select_motion_group_from_stage_selection()

    def __del__(self):
        carb.log_verbose("Destroying GhostTeachingToolBar")

    def _build_ui(self):
        if self._tool_bar is None:
            carb.log_verbose("GhostTeachingToolBar is already destroyed")
            return
        stream_config = (
            get_motion_group_configuration_from_prim(
                self._motion_group_prim
            ).motion_stream_configuration
            if self._motion_group_prim
            else None
        )

        with self._tool_bar.frame:
            with ui.ScrollingFrame(
                height=ui.Pixel(25),
                vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
            ):
                with ui.HStack(spacing=4):
                    if stream_config is None:
                        ui.Label("Select a ghost object from the scene", height=30)
                        return

                    ui.Spacer(width=ui.Pixel(10))
                    ui.Label(
                        f"{stream_config.cell}/{stream_config.motion_group}",
                        style={"color": NOVAColor.TEXT_SECONDARY.color},
                    )
                    ui.Spacer(width=ui.Fraction(1))

                    with ui.HStack(width=ui.Pixel(200)):
                        selected_tcp = self._tcp_selection_cache.get(
                            self._motion_group_prim.GetPath().pathString, None
                        )
                        if selected_tcp is None and self._tcp_selector:
                            selected_tcp = self._tcp_selector.selected_tcp

                        def cache_tcp_selection(
                            tcp_name: str,
                            obj: GhostTeachingToolBar = weakref.proxy(self),
                        ):
                            obj._tcp_selection_cache[
                                obj._motion_group_prim.GetPath().pathString
                            ] = tcp_name

                        self._tcp_selector = TcpSelector(
                            api_configuration=stream_config.get_api_configuration(),
                            cell=stream_config.cell,
                            controller=stream_config.controller,
                            motion_group=stream_config.motion_group,
                            selected_tcp=selected_tcp,
                            tcp_changed_fn=cache_tcp_selection,
                        )

                    # Refresh ghost objects in case it moved

                    if len(self._ghost_objects) > 0:

                        def ghost_object_changed_fn(
                            ghost_object: GhostObject,
                            obj: GhostTeachingToolBar = weakref.proxy(self),
                        ):
                            obj._assign_ghost_object(ghost_object)

                        selected_ghost_object = (
                            self._ghost_objects.get(
                                self._selected_ghost_object_path,
                                None,
                            )
                            if self._selected_ghost_object_path
                            else None
                        )
                        with ui.VStack(width=ui.Pixel(300)):
                            self._ghost_object_selector = GhostObjectSelector(
                                list(self._ghost_objects.values()),
                                selected_ghost_object,
                                ghost_object_changed_fn=ghost_object_changed_fn,
                                can_select_in_scene=self._settings_model.select_ghost_object_in_scene,
                                can_select_from_scene=True,
                            )

                    # Recreating the button would lead to losing the move to task and blocking the motion group control so we recycle the existing one
                    if self._move_to_button is None:
                        self._move_to_button = MoveToButton(
                            configure_execution_fn=lambda weak_self=weakref.proxy(
                                self
                            ): weak_self._get_planning_configuration()
                        )
                    else:
                        self._move_to_button._build_ui()

                    ui.Button(
                        image_url=get_icon("settings.svg"),
                        width=ui.Pixel(30),
                        height=ui.Fraction(1),
                        mouse_pressed_fn=lambda x,
                        y,
                        button,
                        modifier,
                        obj=weakref.proxy(self): obj._on_settings_button_mouse_pressed(
                            x, y, button, modifier
                        ),
                        style={
                            "margin": 0,
                            "background_color": "transparent",
                            "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                            ":hovered": {
                                "background_color": NOVAColor.BACKGROUND_PAPER.color
                            },
                        },
                    )

    def _deferred_build_ui(self):
        async def _build_ui_async():
            await omni.kit.app.get_app().next_update_async()
            self._build_ui()

        if self._deferred_build_task and not self._deferred_build_task.done():
            self._deferred_build_task.cancel()
        self._deferred_build_task = asyncio.ensure_future(_build_ui_async())

    def _on_settings_button_mouse_pressed(
        self, x: float, y: float, button: int, modifier
    ):
        self._settings_window.show(x, y)

    def _assign_ghost_object(self, ghost_object: GhostObject):
        self._selected_ghost_object_path = ghost_object.prim_path
        stage: Usd.Stage = omni.usd.get_context().get_stage()
        self._assign_motion_group_prim(
            stage.GetPrimAtPath(ghost_object.robot_prim_path)
        )

    def _get_planning_configuration(self) -> MoveToExecuteSettings | None:
        motion_group = get_motion_group_configuration_from_prim(self._motion_group_prim)
        carb.log_info(
            f"Get motion group from {self._motion_group_prim} with {motion_group}"
        )
        if motion_group is None:
            nm.post_notification(
                f"Ghost object robot {self._motion_group_prim} has no motion group configured",
                duration=5.0,
                notification_type=nm.NotificationStatus.WARNING,
            )
            return None

        motion_stream_configuration = motion_group.motion_stream_configuration

        self._refresh_ghost_objects()
        if self._selected_ghost_object_path not in self._ghost_objects:
            carb.log_error(
                f"Selected ghost object {self._selected_ghost_object_path} not found"
            )
            return None
        target_pose = self._ghost_objects[self._selected_ghost_object_path].pose

        motion_command = None
        if self.settings.motion_command == "joint_p2p":
            motion_command = asyncio.get_event_loop().run_until_complete(
                planner_utils.create_joint_p2p_command_from_pose(
                    motion_stream_configuration,
                    tcp=self._tcp_selector.selected_tcp,
                    target_pose=target_pose,
                )
            )
        elif self.settings.motion_command == "cartesian_p2p":
            motion_command = wb_models.MotionCommand(
                path=wb_models.MotionCommandPath(
                    wb_models.PathCartesianPTP(target_pose=target_pose.to_nova_pose())
                )
            )
        elif self.settings.motion_command == "line":
            motion_command = wb_models.MotionCommand(
                path=wb_models.MotionCommandPath(
                    wb_models.PathLine(
                        target_pose=target_pose.to_nova_pose(),
                    )
                )
            )

        if motion_command is None:
            carb.log_error(
                f"Unsupported motion command type: {self.settings.motion_command}"
            )
            return None

        return MoveToExecuteSettings(
            motion_stream_configuration=motion_stream_configuration,
            tcp=self._tcp_selector.selected_tcp,
            motion_command=motion_command,
            auto_play_simulation=self.settings.auto_play_simulation,
            velocity=self.settings.velocity,
            acceleration=self.settings.acceleration,
        )

    def _refresh_ghost_objects(self):
        if self._motion_group_prim is None:
            self._ghost_objects = {}
            self._selected_ghost_object_path = None
            return
        self._ghost_objects: dict[str, GhostObject] = {
            ghost_object.prim_path: ghost_object
            for ghost_object in GhostObjectUtils.get_ghost_objects(
                relative_to_prim=self._motion_group_prim.GetPath().pathString
            )
            if ghost_object.robot_prim_path
            == self._motion_group_prim.GetPath().pathString
        }
        carb.log_verbose(
            f"Refresh found ghost objects: {list(self._ghost_objects.keys())} {len(self._ghost_objects)}"
        )

        if self._selected_ghost_object_path not in self._ghost_objects:
            self._selected_ghost_object_path = None

    def _assign_motion_group_prim(self, motion_group_prim: Usd.Prim):
        if self._motion_group_prim == motion_group_prim:
            return
        self._motion_group_prim = motion_group_prim

        motion_group = get_motion_group_configuration_from_prim(self._motion_group_prim)
        carb.log_info(
            f"Get motion group from {self._motion_group_prim} with {motion_group}"
        )
        if motion_group is None:
            nm.post_notification(
                f"Ghost object robot {self._motion_group_prim} has no motion group configured",
                duration=5.0,
                notification_type=nm.NotificationStatus.WARNING,
            )
            return

        if self.visible:
            self._settings_window = GhostTeachingSettingsWindow(
                self._settings_model,
                get_motion_group_configuration_from_prim(
                    self._motion_group_prim
                ).motion_stream_configuration,
            )

        self._refresh_ghost_objects()
        self._deferred_build_ui()

    def _ghost_object_changed_fn(self):
        self._refresh_ghost_objects()
        self._deferred_build_ui()

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type == int(omni.usd.StageEventType.SELECTION_CHANGED):
            self._select_motion_group_from_stage_selection()
            if (
                self._motion_group_prim is None
                and not self._settings_model.stay_open
                and self.visible
            ):
                self.hide()

        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._reset_selection()
        elif event.type == int(omni.usd.StageEventType.OPENED):
            self._reset_selection()

    def _select_motion_group_from_stage_selection(self):
        selection = cast(omni.usd.Selection, omni.usd.get_context().get_selection())
        stage: Usd.Stage = omni.usd.get_context().get_stage()
        selected_prim_paths = selection.get_selected_prim_paths()
        if len(selected_prim_paths) > 1 or len(selected_prim_paths) == 0:
            return
        selected_prim = stage.GetPrimAtPath(selected_prim_paths[0])
        if not GhostObjectUtils.is_ghost_object(selected_prim):
            if not self.settings.stay_open:
                self.hide()
            return

        ghost_object: GhostObject = GhostObjectUtils.get_ghost_object_from_prim(
            selected_prim, None
        )

        if not ghost_object:
            return
        if (
            self._motion_group_prim
            and self._motion_group_prim.GetPath().pathString
            == ghost_object.robot_prim_path
        ):
            return

        self._assign_motion_group_prim(
            stage.GetPrimAtPath(ghost_object.robot_prim_path)
        )

        if not self.visible and self.settings.open_with_ghost_object:
            self.show()

    def _reset_selection(self):
        self._selected_ghost_object_path = None
        self._ghost_objects = {}
        self._motion_group_prim = None

    def show(self):
        if self._tool_bar is None:
            self._tool_bar = ui.ToolBar("Ghost Teaching Actions")
            self._refresh_ghost_objects()
            self._build_ui()
            run_coroutine(self.dock())

        if self._motion_group_prim and not self._settings_window:
            self._settings_window = GhostTeachingSettingsWindow(
                self._settings_model,
                get_motion_group_configuration_from_prim(
                    self._motion_group_prim
                ).motion_stream_configuration,
            )
        self._tool_bar.visible = True
        omni.kit.menu.utils.refresh_menu_items(WINDOW_MENU_ROOT)

    def hide(self):
        carb.log_verbose(
            f"Hiding GhostTeachingToolBar {self._tool_bar} {self._settings_window}"
        )
        self._reset_selection()
        if self._settings_window:
            self._settings_model = self._settings_window.model
            self._settings_window.visible = False
            self._settings_window.frame.clear()
            self._settings_window.destroy()
            self._settings_window = None
        if self._tool_bar is not None:
            self._tool_bar.undock()
            self._tool_bar.visible = False
            self._tool_bar.frame.clear()
            self._tool_bar.destroy()
            self._tool_bar = None
        omni.kit.menu.utils.refresh_menu_items(WINDOW_MENU_ROOT)

    async def dock(self):
        await omni.kit.app.get_app().next_update_async()
        active_viewport: ui.WindowHandle = (
            omni.kit.viewport.utility.get_active_viewport_window()
        )
        if not active_viewport or not self._tool_bar:
            return
        viewport_window = ui.Workspace.get_window(active_viewport.title)
        if not viewport_window:
            return
        self._tool_bar.dock_in(viewport_window, ui.DockPosition.BOTTOM, 0.1)
        viewport_window.dock_tab_bar_visible = False

    def _on_settings_property_changed(self, property_name: str, old: Any, new: Any):
        carb.log_verbose(f"Settings changed: {property_name} from {old} to {new}")
        if "select_ghost_object_in_scene" == property_name:
            if self._ghost_object_selector:
                self._ghost_object_selector.can_select_in_scene = (
                    self.settings.select_ghost_object_in_scene
                )
        save_ghost_teaching_carb_settings(self._settings_model)

    @property
    def visible(self):
        return self._tool_bar.visible if self._tool_bar else False

    @property
    def settings(self) -> SettingsModel:
        # The model gets updated once the settings window is closed until then the window model is the source of truth
        return (
            self._settings_window.model
            if self._settings_window
            else self._settings_model
        )


@dataclass
class ToolBarSubscription:
    toolbar: GhostTeachingToolBar = None
    menu_subscriptions: list = None

    def __del__(self):
        # Need to explicitly hide the toolbar because the docking causes issues on deletion
        if self.toolbar:
            self.toolbar.hide()

        # Dropping the menu items is not enough we need to explicitly remove them
        omni.kit.menu.utils.remove_menu_items(self.menu_subscriptions, WINDOW_MENU_ROOT)


def register_ghost_teaching_tool_bar():
    carb.log_verbose("Registering Ghost Teaching Tool Bar")
    toolbar = GhostTeachingToolBar()

    def toggle_visibility():
        if toolbar.visible:
            toolbar.hide()
        else:
            toolbar.show()

    def _is_visible(
        toolbar: Callable[[], GhostTeachingToolBar | None] = weakref.ref(toolbar),
    ):
        return toolbar().visible if toolbar() else False

    ext_id = "wandelbots.omni"
    name = "Ghost Teaching Tool Bar"
    action_name = "toggle_ghost_teaching_tool_bar"
    action_unique = f"{ext_id}_{name}_{action_name}"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(ext_id, action_unique)
    action_registry.register_action(
        ext_id, action_unique, toggle_visibility, display_name=name, tag="MenuItem"
    )
    return ToolBarSubscription(
        toolbar,
        omni.kit.menu.utils.add_menu_items(
            [
                omni.kit.menu.utils.MenuItemDescription(
                    name="Wandelbots NOVA",
                    sub_menu=[
                        omni.kit.menu.utils.MenuItemDescription(
                            name=name,
                            onclick_action=(ext_id, action_unique),
                            ticked_fn=_is_visible,
                        )
                    ],
                )
            ],
            WINDOW_MENU_ROOT,
        ),
    )
