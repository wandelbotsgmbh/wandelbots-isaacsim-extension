import weakref
import carb
import omni.ui as ui
from typing import Callable, Optional
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.ui.utils import get_icon
from wandelbots.omni.instances.models import (
    NOVACloudInstance,
)
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.instances.instance import NOVAInstanceUIBuilder
from omni.kit.window.property.templates.header_context_menu import (
    GroupHeaderContextMenu,
    GroupHeaderContextMenuEvent,
)


def _build_frame_header(
    collapsed,
    text: str,
    group_id: str = None,
    on_sign_out_fn: Optional[Callable] = None,
):
    group_id = group_id if group_id else text

    if collapsed:
        alignment = ui.Alignment.RIGHT_CENTER
        width = 5
        height = 7
    else:
        alignment = ui.Alignment.CENTER_BOTTOM
        width = 7
        height = 5

    header_stack = ui.HStack(
        name="header_stack",
        spacing=8,
    )
    with header_stack:
        ui.Spacer(width=1)
        with ui.VStack(width=0):
            ui.Spacer()
            ui.Triangle(
                style_type_name_override="CollapsableFrame.Header",
                width=width,
                height=height,
                alignment=alignment,
            )
            ui.Spacer()
        ui.Label(text, style_type_name_override="CollapsableFrame.Header")
        ui.Spacer()
        with ui.HStack(content_clipping=True, width=0):
            ui.Spacer(width=8)
            ui.Button(
                width=20,
                height=20,
                clicked_fn=on_sign_out_fn,
                style={
                    "image_url": get_icon("sign_out.svg"),
                    "color": NOVAColor.ACTION_ACTIVE.color,
                },
                tooltip="Click to sign out of your account.",
                identifier="sign_out_button",
            )
            ui.Spacer(width=10)

    def show_context_menu(b):
        if b != 1:
            return

        event = GroupHeaderContextMenuEvent(group_id=group_id, payload=[])
        GroupHeaderContextMenu.on_mouse_event(event)

    header_stack.set_mouse_pressed_fn(lambda x, y, b, _: show_context_menu(b))


class NOVACloudInstancesContainer:
    def __init__(
        self,
        auth_config_name: str,
        instances_service: NOVAInstancesService,
        on_sign_out_fn: Optional[Callable],
    ):
        self.container: ui.VStack = None
        self._auth_config_name = auth_config_name
        self._instances_service = instances_service
        self._cloud_instances: list[NOVACloudInstance] = (
            self._instances_service.instances_api.get_cloud_instances_by_auth(
                auth_config_name
            )
        )
        self._one_of_many_providers = (
            len(self._instances_service.instances_api.get_cloud_instances().keys()) > 1
        )
        self._on_sign_out_fn = on_sign_out_fn

    def build_ui(self):
        if not self._instances_service.is_signed_in(self._auth_config_name):
            return

        self.container = ui.VStack()
        with self.container:
            if len(self._cloud_instances) > 0:
                self._display_cloud_instances_section()
                return
            with ui.ZStack():
                ui.Rectangle(
                    style={
                        "background_color": NOVAColor.BACKGROUND_PAPER.color,
                        "border_radius": 2,
                    }
                )
                with ui.Frame(
                    height=0,
                    style={
                        "Frame": {"margin": 10},
                    },
                ):
                    with ui.VStack(spacing=8):
                        with ui.HStack():
                            ui.Spacer(width=10)

                            title = "No instances available. Please create one."
                            if self._one_of_many_providers:
                                title = f"({self._auth_config_name}) {title}"
                            ui.Label(
                                title,
                                width=ui.Fraction(1),
                            )
                            ui.Button(
                                image_url=get_icon("sign_out.svg"),
                                width=20,
                                height=20,
                                style={
                                    "color": NOVAColor.ACTION_ACTIVE.color,
                                },
                                tooltip="Click to sign out of your account.",
                                clicked_fn=self._on_sign_out_fn,
                            )
                            ui.Spacer(width=6)

    def refresh_cloud_instances(self):
        self._cloud_instances = (
            self._instances_service.instances_api.get_cloud_instances_by_auth(
                self._auth_config_name
            )
        )

    def _display_cloud_instances_section(self):
        """Display cloud instances or sign-in prompt."""
        if not self._instances_service.is_signed_in(self._auth_config_name):
            return

        if len(self._cloud_instances) == 0:
            carb.log_verbose("No cloud instances available")
            return

        with ui.CollapsableFrame(
            title=f"({self._auth_config_name}) Cloud Instances"
            if self._one_of_many_providers
            else "Cloud Instances",
            height=0,
            build_header_fn=lambda collapsed,
            text,
            weak_self=weakref.proxy(self): _build_frame_header(
                collapsed, text, on_sign_out_fn=weak_self._on_sign_out_fn
            ),
        ):
            with ui.VStack(spacing=5, height=0):
                if len(self._cloud_instances) == 0:
                    with ui.HStack(spacing=5):
                        ui.Spacer(width=10)
                        ui.Label("No instances available. Create one.")
                for instance in self._cloud_instances:
                    NOVAInstanceUIBuilder(
                        instance=instance,
                        instances_service=self._instances_service,
                        on_remove=None,
                        on_toggle_status=None,
                    ).build_ui()

                    if instance != self._cloud_instances[-1]:
                        self._display_separator()

                ui.Spacer(height=5)

    def _display_separator(self):
        """Display a separator line in the UI."""
        with ui.HStack(height=20):
            ui.Spacer(width=ui.Percent(2))
            ui.Line(width=ui.Percent(96), style={"color": NOVAColor.DIVIDER.color})
            ui.Spacer(width=ui.Percent(2))
