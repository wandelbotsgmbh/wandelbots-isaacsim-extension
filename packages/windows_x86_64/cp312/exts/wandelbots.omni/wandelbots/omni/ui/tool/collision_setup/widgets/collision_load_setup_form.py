from typing import cast
import omni.kit.notification_manager as nm
from pxr import Usd
import omni
import weakref
import carb
import omni.ui as ui
from wandelbots.omni.manipulators import (
    is_prim_motion_group,
    get_scene_motion_group_prim_paths,
)
import omni.usd
from wandelbots.omni.ui.overlay.collision_world.utils import (
    CARB_OVERLAY_RENDER_MODE,
    get_overlay_color,
    set_overlay_color,
)
from wandelbots.omni.ui.tool.collision_setup.widgets import settings_string_values_model
from wandelbots.omni.ui.widgets import (
    CollisionSetupSelector,
    PrimPicker,
    PrimPickerDialogProperties,
)
import carb.events
from omni.kit.async_engine import run_coroutine
import wandelbots.omni.ui.colors as color_utils
from wandelbots.omni.manipulators import get_motion_group_configuration_from_prim
import wandelbots.omni.ui.overlay.collision_world.collision_world_overlay as overlay
from wandelbots.omni.ui.overlay.overlay_registry import (
    get_overlay_registry,
)
import wandelbots_api_client.v2 as wb
from wandelbots.omni.ui.colors import NOVAColor

WINDOW_MENU_ROOT = "Tools"


class SphereRadiusModel(ui.SimpleFloatModel):
    def min(self):
        return 0


class CollisionLoadSetupForm:
    def __init__(self):
        self._stage = omni.usd.get_context().get_stage()
        self.frame = ui.Frame(height=0)

        # model data
        self._motion_group_prim: Usd.Prim | None = None
        self._base_prim: Usd.Prim | None = None
        self._collision_setup_name: str | None = None

        # model inputs
        self._motion_group_prim_picker: PrimPicker | None = None
        self._base_prim_picker: PrimPicker | None = None

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
                name="collision_export_window_stage_event",
            )
        )

        self._build_ui()
        run_coroutine(self._prefetch_collision_setup())

    def _build_ui(self):
        self.frame.clear()
        if self._stage is None:
            with self.frame:
                ui.Label("No stage loaded.", height=30)
            return

        with self.frame:
            with ui.VStack(spacing=4):
                with ui.VGrid(column_count=2, row_height=ui.Pixel(30)):
                    ui.Label(
                        "Motion group prim",
                        tooltip="Prim representing the robot motion group for which to export collisions",
                    )
                    with ui.HStack(height=20):

                        def assign_prim(
                            prim: Usd.Prim,
                            weak_self: CollisionLoadSetupForm = weakref.proxy(self),
                        ):
                            weak_self._motion_group_prim = prim
                            weak_self._deferred_build_ui()

                        self._motion_group_prim_picker = PrimPicker(
                            stage=self._stage,
                            prim_picked_fn=assign_prim,
                            prim=self._motion_group_prim,
                            dialog_properties=PrimPickerDialogProperties(
                                filter_fn=is_prim_motion_group,
                                title="Select Motion Group",
                            ),
                        )

                    if not self._motion_group_prim:
                        ui.Label(
                            "Please select a motion group prim to load a collision setup.",
                            word_wrap=True,
                            height=40,
                        )
                        return

                    motion_group_configuration = (
                        get_motion_group_configuration_from_prim(
                            self._motion_group_prim
                        )
                    )

                    if not motion_group_configuration:
                        ui.Label(
                            "The selected prim does not have a valid motion group configuration.",
                            word_wrap=True,
                            height=40,
                        )
                        return

                    ui.Label(
                        "Collision setup", tooltip="Collision setup to use for planning"
                    )
                    with ui.HStack(height=20):
                        stream_config = (
                            motion_group_configuration.motion_stream_configuration
                        )

                        def assign_collision_setup(
                            collision_setup: str,
                            weak_self: CollisionLoadSetupForm = weakref.proxy(self),
                        ):
                            weak_self._collision_setup_name = collision_setup
                            weak_self._deferred_build_ui()

                        CollisionSetupSelector(
                            api_configuration=stream_config.get_api_configuration(),
                            cell=stream_config.cell,
                            collision_setup_changed_fn=assign_collision_setup,
                            selected_collision_setup=self._collision_setup_name,
                        )

                    ui.Label(
                        "Coordinate system base",
                        tooltip="Prim representing the base of the coordinate system for which to load the collision setup",
                    )
                    with ui.HStack(height=20):

                        def assign_prim(
                            prim: Usd.Prim,
                            weak_self: CollisionLoadSetupForm = weakref.proxy(self),
                        ):
                            weak_self._base_prim = prim
                            weak_self._deferred_build_ui()

                        self._base_prim_picker = PrimPicker(
                            stage=self._stage,
                            prim_picked_fn=assign_prim,
                            prim=self._base_prim,
                            dialog_properties=PrimPickerDialogProperties(
                                filter_fn=is_prim_motion_group,
                                title="Select prim",
                            ),
                        )

                    ui.Spacer()
                    ui.Button(
                        "Load Collision Setup",
                        height=30,
                        width=150,
                        style={
                            "background_color": NOVAColor.PRIMARY_MAIN.color,
                            "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                            ":hovered": {
                                "background_color": NOVAColor.PRIMARY_DARK.color
                            },
                            ":disabled": {"background_color": NOVAColor.DIVIDER.color},
                        },
                        enabled=bool(
                            self._motion_group_prim
                            and self._base_prim
                            and self._collision_setup_name
                        ),
                        clicked_fn=lambda weak_self=weakref.ref(self): (
                            weak_self()._request_load_collision_setup()
                            if weak_self()
                            else None
                        ),
                    )

                    ui.Label("Collision Overlay")
                    ui.Spacer()

                    ui.Label("Mesh color", width=ui.Fraction(1))

                    def _overlay_color_changed(
                        model: ui.AbstractItemModel,
                        item: ui.AbstractItem,
                        weak_self=weakref.ref(self),
                    ):
                        self_instance = weak_self()

                        if not self_instance:
                            return

                        color = []
                        for item in model.get_item_children():
                            val = model.get_item_value_model(item).get_value_as_float()
                            color.append(val)

                        set_overlay_color(color_utils.float_array_to_hex(color))

                    stored_color_value = get_overlay_color()

                    with ui.HStack():
                        ui.Spacer(width=ui.Fraction(1), height=40)
                        color_picker = ui.ColorWidget(
                            *color_utils.hex_to_float_array(stored_color_value),
                            width=20,
                            height=20,
                            tooltip="Color of the ghost object overlay",
                        )
                        color_picker.model.add_end_edit_fn(_overlay_color_changed)
                        ui.Spacer(width=ui.Fraction(1), height=40)

                    ui.Label("Display")
                    with ui.VStack(height=20):
                        ui.Spacer(height=4)
                        ui.ComboBox(
                            settings_string_values_model.SettingsStringValuesModel(
                                CARB_OVERLAY_RENDER_MODE,
                                [
                                    ("None", "None"),
                                    ("Selected", "Selected"),
                                    ("All", "All"),
                                ],
                            )
                        )

    def _deferred_build_ui(self):
        async def wait_one_frame_and_build():
            await omni.kit.app.get_app().next_update_async()
            self._build_ui()

        run_coroutine(wait_one_frame_and_build())

    def reset(self):
        pass
        self._base_prim = None
        self._motion_group_prim = None
        self._collision_setup_name = None
        run_coroutine(self._prefetch_collision_setup()).add_done_callback(
            lambda _: self._deferred_build_ui()
        )

    def refresh(self):
        run_coroutine(self._prefetch_collision_setup()).add_done_callback(
            lambda _: self._deferred_build_ui()
        )

    async def _prefetch_collision_setup(self):
        if self._stage is None:
            return

        raw_motion_group_prims: list[Usd.Prim] = [
            self._stage.GetPrimAtPath(prim_path)
            for prim_path in get_scene_motion_group_prim_paths(self._stage)
        ]

        motion_group_prims = [
            (prim, get_motion_group_configuration_from_prim(prim))
            for prim in raw_motion_group_prims
            if prim and get_motion_group_configuration_from_prim(prim)
        ]

        if len(motion_group_prims) == 0 or len(motion_group_prims) > 1:
            return

        motion_group_prim, motion_group_config = motion_group_prims[0]

        self._motion_group_prim = motion_group_prim
        self._base_prim = self._motion_group_prim

        try:
            motion_stream_config = motion_group_config.motion_stream_configuration
            async with motion_stream_config.get_api_client() as api:
                collision_setups = await wb.StoreCollisionSetupsApi(
                    api
                ).list_stored_collision_setups_keys(cell=motion_stream_config.cell)
                if len(collision_setups) > 0:
                    self._collision_setup_name = collision_setups[0]
        except Exception as e:
            carb.log_warn(
                f"Failed to fetch collision setups for motion group prim {self._motion_group_prim.GetPath().pathString}: {e}"
            )

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type == int(omni.usd.StageEventType.OPENED):
            self._stage = omni.usd.get_context().get_stage()
            self.reset()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._stage = None
            self.reset()

    def _request_load_collision_setup(self):
        collision_world_overlay: overlay.CollisionWorldOverlay = (
            get_overlay_registry().get_overlay(overlay.COLLISION_WORLD_OVERLAY_NAME)
        )

        if not collision_world_overlay:
            nm.post_notification(
                text="Collision World Overlay is not registered.",
                status=nm.NotificationStatus.WARNING,
            )
            return
        collision_world_overlay.selection = overlay.CollisionSetupSelection(
            motion_group_prim=self._motion_group_prim,
            base_prim=self._base_prim,
            collision_setup_name=self._collision_setup_name,
        )
