import carb
import omni.ui as ui
from typing import Callable, Optional
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.ui.utils import get_icon
from wandelbots.omni.instances.models import (
    NOVACloudInstance,
)
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.instances.instance import InstanceWidget
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection


class NOVACloudInstancesContainer:
    def __init__(
        self,
        auth_config_id: str,
        auth_config_name: str,
        instances_service: NOVAInstancesService,
        on_sign_out_fn: Optional[Callable],
    ):
        self.container: ui.VStack = None
        self._auth_config_id = auth_config_id
        self._auth_config_name = auth_config_name
        self._instances_service = instances_service
        self._cloud_instances: list[NOVACloudInstance] = (
            self._instances_service.instances_api.get_cloud_instances_by_auth(
                auth_config_id
            )
        )
        self._one_of_many_providers = (
            len(self._instances_service.instances_api.get_cloud_instances().keys()) > 1
        )
        self._on_sign_out_fn = on_sign_out_fn
        self._instance_widgets: list[InstanceWidget] = []

    def build_ui(self):
        if not self._instances_service.is_signed_in(self._auth_config_id):
            return

        self._instance_widgets.clear()
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
                                title = f"[{self._auth_config_name}] {title}"
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
                self._auth_config_id
            )
        )

    def _display_cloud_instances_section(self):
        """Display cloud instances or sign-in prompt."""
        if not self._instances_service.is_signed_in(self._auth_config_id):
            return

        if len(self._cloud_instances) == 0:
            carb.log_verbose("No cloud instances available")
            return

        title = (
            f"[{self._auth_config_name}] Cloud Instances"
            if self._one_of_many_providers
            else "Cloud Instances"
        )

        def _build_sign_out_button(_section):
            ui.Button(
                width=20,
                height=20,
                clicked_fn=self._on_sign_out_fn,
                style={
                    "image_url": get_icon("sign_out.svg"),
                    "color": NOVAColor.ACTION_ACTIVE.color,
                },
                tooltip="Click to sign out of your account.",
                identifier="sign_out_button",
            )

        self._section = CollapsibleSection(
            title=title,
            collapsed=False,
            build_header_fn=_build_sign_out_button,
        )
        with self._section.body:
            with ui.VStack(spacing=5, height=0):
                for instance in self._cloud_instances:
                    widget = InstanceWidget(
                        instance=instance,
                        instances_service=self._instances_service,
                    )
                    self._instance_widgets.append(widget)

                    if instance != self._cloud_instances[-1]:
                        self._display_separator()

                ui.Spacer(height=5)

    def _display_separator(self):
        """Display a separator line in the UI."""
        with ui.HStack(height=20):
            ui.Spacer(width=ui.Percent(2))
            ui.Line(width=ui.Percent(96), style={"color": NOVAColor.DIVIDER.color})
            ui.Spacer(width=ui.Percent(2))
