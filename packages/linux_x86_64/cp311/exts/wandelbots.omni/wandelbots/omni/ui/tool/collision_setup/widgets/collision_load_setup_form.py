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
    get_overlay_color,
    set_overlay_color,
)
from wandelbots.omni.ui.widgets import (
    CollisionSetupSelector,
    PrimPicker,
    PrimPickerDialogProperties,
)
from wandelbots.omni.ui.utils import defer_call, weak_cb
from wandelbots.omni.ui.widgets.form_row import form_row
from wandelbots.omni.ui.widgets.instance_picker import InstancePicker
from wandelbots.omni.ui.wb_theme import (
    TOOLTIP_RESET,
    BUTTON_HEIGHT,
    BUTTON_PRIMARY_STYLE,
    BUTTON_STYLE,
    FORM_HEADER_GAP,
    FORM_SIDE_MARGIN,
    SPACING_MD,
    SPACING_SM,
    build_tooltip,
)
from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVAInstance
import carb.events
from omni.kit.async_engine import run_coroutine
import wandelbots.omni.ui.colors as color_utils
from wandelbots.omni.manipulators import get_motion_group_configuration_from_prim
import wandelbots.omni.ui.overlay.collision_world.collision_world_overlay as overlay
from wandelbots.omni.ui.overlay.overlay_registry import (
    get_overlay_registry,
)
from wandelbots.omni.ui.colors import NOVAColor


class CollisionLoadSetupForm:
    def __init__(self):
        self._stage = omni.usd.get_context().get_stage()
        self.frame = ui.Frame(height=0)

        # model data
        self._motion_group_prim: Usd.Prim | None = None
        self._collision_setup_name: str | None = None
        self._cell_id: str | None = None

        self._instance_picker = InstancePicker(
            tooltip="NOVA instance to load a stored collision setup from.",
            instance_changed_fn=weak_cb(self, "_on_instance_changed"),
            instances_refreshed_fn=weak_cb(self, "_on_instances_refreshed"),
        )

        # model inputs
        self._motion_group_prim_picker: PrimPicker | None = None

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
        self._instance_picker.refresh()

    def _build_ui(self):
        self.frame.clear()
        if self._stage is None:
            with self.frame:
                ui.Label("No stage loaded.", height=30)
            return

        with self.frame:
            with ui.VStack(spacing=SPACING_SM):
                ui.Spacer(height=FORM_HEADER_GAP)
                self._instance_picker.build_row()

                instance = self._instance_picker.instance
                if instance is None:
                    with ui.HStack(height=0):
                        ui.Spacer(width=FORM_SIDE_MARGIN)
                        ui.Label(
                            "Select a NOVA instance to load a collision setup.",
                            word_wrap=True,
                            height=40,
                            style={"color": NOVAColor.TEXT_SECONDARY.color},
                        )
                        ui.Spacer(width=FORM_SIDE_MARGIN)
                    return

                if self._cell_id is None:
                    with ui.HStack(height=0):
                        ui.Spacer(width=FORM_SIDE_MARGIN)
                        ui.Label(
                            f"Instance '{instance.display_name}' has no cells available.",
                            word_wrap=True,
                            height=40,
                            style={"color": NOVAColor.TEXT_SECONDARY.color},
                        )
                        ui.Spacer(width=FORM_SIDE_MARGIN)
                    return

                with form_row(
                    "Collision setup",
                    tooltip="Stored collision setup to load into the viewport",
                ):

                    def assign_collision_setup(
                        collision_setup: str,
                        weak_self: CollisionLoadSetupForm = weakref.proxy(self),
                    ):
                        weak_self._collision_setup_name = collision_setup
                        weak_self._deferred_build_ui()

                    def on_setups_loaded(
                        setups: list[str],
                        weak_self: CollisionLoadSetupForm = weakref.proxy(self),
                    ):
                        # The remembered setup may be gone from the instance,
                        # and the Load button must not stay enabled for it.
                        if (
                            weak_self._collision_setup_name is not None
                            and weak_self._collision_setup_name not in setups
                        ):
                            weak_self._collision_setup_name = None
                            weak_self._deferred_build_ui()

                    CollisionSetupSelector(
                        api_configuration=get_instances_api().get_api_configuration_for_instance(
                            instance
                        ),
                        cell=self._cell_id,
                        collision_setup_changed_fn=assign_collision_setup,
                        selected_collision_setup=self._collision_setup_name,
                        collision_setups_loaded_fn=on_setups_loaded,
                    )

                with form_row(
                    "Motion group prim",
                    tooltip=(
                        "Optional. With a motion group selected, the overlay "
                        "renders the full assembled view a consumer checks: "
                        "static colliders plus the robot's live link chain, "
                        "stored link equipment and tool, tracked on that robot. "
                        "Without one, only the static colliders are rendered. "
                        "Auto-detected when exactly one motion group in the "
                        "scene is connected to the selected instance."
                    ),
                ):

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

                with form_row(
                    "Mesh color", tooltip="Color of the collision overlay meshes"
                ):
                    with ui.HStack(height=20):
                        color_picker = ui.ColorWidget(
                            *color_utils.hex_to_float_array(get_overlay_color()),
                            width=20,
                            height=20,
                            style=TOOLTIP_RESET,
                            tooltip_fn=lambda: build_tooltip(
                                "Color of the collision overlay meshes"
                            ),
                        )
                        color_picker.model.add_end_edit_fn(_overlay_color_changed)
                        ui.Spacer()

                ui.Spacer(height=SPACING_SM)
                with ui.HStack(height=BUTTON_HEIGHT, spacing=SPACING_MD):
                    ui.Spacer(width=ui.Fraction(1))
                    ui.Button(
                        "Clear",
                        width=0,
                        height=BUTTON_HEIGHT,
                        style=BUTTON_STYLE,
                        tooltip="Remove the loaded collision setup from the viewport",
                        clicked_fn=lambda weak_self=weakref.ref(self): (
                            weak_self()._request_clear_collision_setup()
                            if weak_self()
                            else None
                        ),
                    )
                    ui.Button(
                        "Load Collision Setup",
                        width=0,
                        height=BUTTON_HEIGHT,
                        style={
                            **BUTTON_PRIMARY_STYLE,
                            "Button:disabled": {
                                "background_color": (
                                    NOVAColor.ACTION_DISABLED_BACKGROUND.color
                                )
                            },
                        },
                        enabled=bool(self._collision_setup_name),
                        clicked_fn=lambda weak_self=weakref.ref(self): (
                            weak_self()._request_load_collision_setup()
                            if weak_self()
                            else None
                        ),
                    )
                    ui.Spacer(width=FORM_SIDE_MARGIN)
                ui.Spacer(height=SPACING_SM)

    def _deferred_build_ui(self):
        defer_call(self._build_ui)

    def refresh_instances(self) -> None:
        """Refetch the instance list in the background. The owning window calls
        this whenever it becomes visible, so instances added or removed via
        Connect-to-NOVA since the constructor's fetch show up."""
        self._instance_picker.refresh()

    def _on_instances_refreshed(self) -> None:
        run_coroutine(self._resolve_selected_instance())

    def _on_instance_changed(self) -> None:
        # Another instance serves other setups, and the detected motion group
        # was picked for the previous one.
        self._motion_group_prim = None
        self._collision_setup_name = None
        self._on_instances_refreshed()

    async def _resolve_selected_instance(self) -> None:
        """Resolve the selected instance's cell and auto-detect a motion group
        prim in the scene connected to it, then rebuild the dependent UI."""
        instance = self._instance_picker.instance
        self._cell_id = None
        if instance is not None:
            self._cell_id = await get_instances_api().fetch_primary_cell_id(instance)
            self._auto_detect_motion_group_prim(instance)

        self._deferred_build_ui()

    def _auto_detect_motion_group_prim(self, instance: NOVAInstance) -> None:
        """Auto-select the scene's motion group prim connected to `instance`,
        when exactly one such prim exists. Left unset (for manual override)
        when there's none or more than one."""
        if self._stage is None or self._motion_group_prim is not None:
            return

        candidates: list[Usd.Prim] = []
        for prim_path in get_scene_motion_group_prim_paths(
            include_prims_without_api=False
        ):
            prim = self._stage.GetPrimAtPath(prim_path)
            if not prim or not prim.IsValid():
                continue
            config = get_motion_group_configuration_from_prim(prim)
            if config is None:
                continue
            stream = config.motion_stream_configuration
            if (
                stream.host == instance.host
                and stream.secure_connection == instance.is_secure_connection
            ):
                candidates.append(prim)

        if len(candidates) == 1:
            self._motion_group_prim = candidates[0]

    def reset(self):
        self._motion_group_prim = None
        self._collision_setup_name = None
        self._cell_id = None
        self._instance_picker.reset_selection()
        self._instance_picker.refresh()

    def _on_stage_event(self, event: carb.events.IEvent):
        if event.type == int(omni.usd.StageEventType.OPENED):
            self._stage = omni.usd.get_context().get_stage()
            self.reset()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._stage = None
            self.reset()

    def _request_load_collision_setup(self):
        instance = self._instance_picker.instance
        if instance is None or self._cell_id is None:
            nm.post_notification(
                text="Select a NOVA instance with an available cell before loading.",
                status=nm.NotificationStatus.WARNING,
            )
            return

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
            # Static colliders live in the stage world frame, which is where the
            # overlay's None fallback anchors them.
            base_prim=None,
            collision_setup_name=self._collision_setup_name,
            api_configuration=get_instances_api().get_api_configuration_for_instance(
                instance
            ),
            cell=self._cell_id,
            motion_group_prim=self._motion_group_prim,
        )

    def _request_clear_collision_setup(self):
        collision_world_overlay: overlay.CollisionWorldOverlay = (
            get_overlay_registry().get_overlay(overlay.COLLISION_WORLD_OVERLAY_NAME)
        )
        if not collision_world_overlay:
            return
        collision_world_overlay.selection = None
