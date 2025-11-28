import carb
import omni.ui as ui
from isaacsim.gui.components.ui_utils import get_style
from typing import Optional
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.ui.utils import defer_call, get_icon
from wandelbots.omni.ui.base import BaseUIBuilder
from wandelbots.omni.instances.models import (
    NOVACustomInstance,
    NOVACloudInstance,
    NOVAInstance,
)
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.instances.instance import NOVAInstanceUIBuilder


class NOVAInstanceListUIBuilder(BaseUIBuilder):
    def __init__(self):
        super().__init__(
            title="Wandelbots NOVA | Connected Instances", width=500, height=600
        )
        self._instances_service = NOVAInstancesService()
        self._cloud_instances: list[NOVACloudInstance] = []
        self._custom_instances: list[NOVACustomInstance] = []
        self._cloud_instances_container: Optional[ui.VStack] = None
        self._custom_instances_container: Optional[ui.VStack] = None
        self._host_error_message = ""
        self._current_context = {}
        self._style = get_style()

    def build_ui(self):
        self._style.update({"color": NOVAColor.TEXT_PRIMARY.color})
        self._fetch_instances_data()

        with self._window.frame:
            with ui.ScrollingFrame(
                vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                width=ui.Percent(100),
                height=ui.Percent(100),
                style=self._style,
            ):
                with ui.VStack(spacing=8):
                    self._header_container = ui.VStack(height=0)
                    self._custom_instance_form = ui.VStack(visible=False, height=50)
                    self._custom_instances_container = ui.VStack(spacing=10, height=0)
                    self._cloud_instances_container = ui.VStack(spacing=10, height=0)
                    ui.Spacer()

        self._display_header()
        self._display_instances()

    # Display methods

    def _display_header(self):
        self._header_container.clear()
        with self._header_container:
            with ui.HStack(height=20, spacing=5):
                ui.Spacer(width=5)
                ui.Label("Wandelbots NOVA Instances", style={"font_size": 16})
                ui.Spacer()
                if self._instances_service.is_signed_in:
                    ui.Button(
                        image_url=get_icon("sign_out.svg"),
                        width=20,
                        height=20,
                        style={
                            "color": NOVAColor.ACTION_ACTIVE.color,
                        },
                        tooltip="Click to sign out of your account.",
                        clicked_fn=self._on_sign_out,
                    )
                ui.Button(
                    image_url=get_icon("add.svg"),
                    width=20,
                    height=20,
                    style={
                        "color": NOVAColor.ACTION_ACTIVE.color,
                    },
                    tooltip="Click to add a Wandelbots NOVA instance which is reachable within your network.",
                    clicked_fn=self._on_toggle_add_custom_instance_form,
                )
                ui.Button(
                    image_url=get_icon("refresh.svg"),
                    width=20,
                    height=20,
                    style={
                        "color": NOVAColor.ACTION_ACTIVE.color,
                    },
                    tooltip="Click to refresh instance data",
                    clicked_fn=self._refresh_data,
                )

    def _display_add_custom_instance_form(self):
        def _on_cancel():
            self._custom_instance_form.visible = False
            self._host_input.model.set_value("")
            self._host_error_message = ""

        self._custom_instance_form.clear()
        with self._custom_instance_form:
            with ui.CollapsableFrame(
                title="Add Instance", width=ui.Percent(100), height=0
            ):
                with ui.VStack(spacing=5):
                    with ui.HStack(spacing=5):
                        ui.Spacer(width=5)
                        ui.Label("Host:", width=40)
                        self._host_input = ui.StringField(
                            placeholder="e.g., https://172.31.10.110"
                        )
                        self._host_input.model.add_end_edit_fn(
                            self._on_host_input_end_edit
                        )
                        ui.Button(
                            "Add",
                            width=50,
                            clicked_fn=lambda: self._on_add_custom_instance(),
                        )
                        ui.Button("Cancel", width=50, clicked_fn=lambda: _on_cancel())
                        ui.Spacer(width=5)
                    with ui.HStack():
                        ui.Spacer(width=10)
                        ui.Label(
                            self._host_error_message,
                            style={"color": NOVAColor.ERROR_MAIN.color},
                        )

    def _display_custom_instances(self):
        self._custom_instances_container.clear()
        with self._custom_instances_container:
            self._display_custom_instances_section()

    def _display_cloud_instances(self):
        self._cloud_instances_container.clear()

        with self._cloud_instances_container:
            if not self._instances_service.is_signed_in:
                self._display_sign_in_section()
            elif len(self._cloud_instances) == 0:
                with ui.VStack(spacing=8):
                    ui.Spacer()
                    with ui.HStack():
                        ui.Spacer(width=10)
                        ui.Label("No instances available. Please create one.")
                        ui.Spacer()
            else:
                self._display_cloud_instances_section()

    def _display_instances(self):
        """Display all loaded instances in the UI."""
        try:
            defer_call(self._display_cloud_instances)
            defer_call(self._display_custom_instances)
        except Exception as e:
            carb.log_error(f"Error displaying instances: {e}")

    def _display_sign_in_section(self):
        """Show sign-in prompt for cloud instances."""
        sign_in_container = ui.CollapsableFrame("Cloud Instances", height=0, spacing=5)
        with sign_in_container:
            with ui.VStack(spacing=5):
                with ui.HStack(spacing=5):
                    ui.Spacer(width=10)
                    ui.Label(
                        "Sign in to Wandelbots NOVA:",
                        width=ui.Percent(50),
                    )
                    ui.Spacer()
                    ui.Button(
                        "Sign in",
                        height=25,
                        width=ui.Percent(20),
                        style={"background_color": NOVAColor.PRIMARY_MAIN.color},
                        clicked_fn=lambda: self._on_sign_in(sign_in_container),
                    )
                    ui.Spacer(width=5)
                ui.Spacer(height=5)

    def _display_cloud_instances_section(self):
        """Display cloud instances or sign-in prompt."""
        if not self._instances_service.is_signed_in:
            return

        if len(self._cloud_instances) == 0:
            carb.log_verbose("No cloud instances available")
            return

        with ui.CollapsableFrame(title="Cloud Instances", height=0):
            with ui.VStack(spacing=5, height=0):
                if len(self._cloud_instances) == 0:
                    with ui.HStack(spacing=5):
                        ui.Spacer(width=10)
                        ui.Label("No instances available. Create one.")
                for instance in self._cloud_instances:
                    self._display_instance(instance)

                    if instance != self._cloud_instances[-1]:
                        self._display_separator()

                ui.Spacer(height=5)

    def _display_custom_instances_section(self):
        """Display custom instances section."""
        if not self._custom_instances:
            carb.log_verbose("No custom instances available")
            return

        with ui.CollapsableFrame(title="Custom Instances", height=0):
            with ui.VStack(spacing=5, height=0):
                for instance in self._custom_instances:
                    self._display_instance(instance)

                    if instance != self._custom_instances[-1]:
                        self._display_separator()
                ui.Spacer(height=10)

    def _display_separator(self):
        """Display a separator line in the UI."""
        with ui.HStack(height=20):
            ui.Spacer(width=ui.Percent(2))
            ui.Line(width=ui.Percent(96), style={"color": NOVAColor.DIVIDER.color})
            ui.Spacer(width=ui.Percent(2))

    def _display_instance(self, instance: NOVAInstance):
        return NOVAInstanceUIBuilder(
            instance=instance,
            instances_service=self._instances_service,
            on_remove=self._on_remove_custom_instance,
            on_toggle_status=self._refresh_data,
        ).build_ui()

    # Data management methods

    def _refresh_data(self):
        self._fetch_instances_data()
        self._display_instances()

    def _fetch_instances_data(self):
        try:
            # Clear previous lists to avoid duplications on refresh
            self._cloud_instances.clear()
            self._custom_instances.clear()

            cloud_instances = (
                self._instances_service._instances_api.get_cloud_instances()
            )
            for instance in cloud_instances:
                self._cloud_instances.append(instance)

            custom_instances = (
                self._instances_service._instances_api.get_custom_instances()
            )
            for instance in custom_instances:
                self._custom_instances.append(instance)

            carb.log_info(
                f"Loaded {len(self._cloud_instances)} cloud instances and {len(self._custom_instances)} custom instances"
            )

        except Exception as e:
            carb.log_error(f"Failed to load instance data: {e}")

    # Handlers for UI events
    def _on_host_input_end_edit(self, model):
        """Handle ENTER key press in the host input field."""
        self._on_add_custom_instance()

    def _on_add_custom_instance(self):
        text_input = self._host_input.model.get_value_as_string().strip()

        try:
            host_address = self._instances_service.validate_host(text_input)
            host_address = f"{host_address}".replace("https://", "").replace(
                "http://", ""
            )
        except ValueError as e:
            carb.log_verbose(f"Invalid host address: {e}")
            self._host_error_message = "Invalid host address. Please enter a valid URL."
            defer_call(self._display_add_custom_instance_form)
            return

        try:
            custom_instance = NOVACustomInstance(host=host_address, name=host_address)
            self._instances_service.add_custom_instance(custom_instance)

            # Clear the form and hide it
            self._host_input.model.set_value("")
            self._custom_instance_form.visible = False
            self._host_error_message = ""

            # Clear handler cache and refresh data
            defer_call(self._refresh_data)

        except Exception as e:
            carb.log_verbose(f"Failed to add custom instance: {e}")
            self._host_error_message = f"Failed to add custom instance: {e}"
            self._display_add_custom_instance_form()

    def _on_remove_custom_instance(self, instance: NOVACustomInstance):
        carb.log_info(f"Removing instance: {instance.display_name}")

        def on_complete():
            carb.log_info(f"Instance {instance.display_name} removed successfully")
            self._refresh_data()

        self._instances_service.remove_instance(instance, callback=on_complete)

    def _on_toggle_add_custom_instance_form(self):
        if self._custom_instance_form.visible:
            self._custom_instance_form.visible = False
        else:
            # Show the form and build it if needed
            self._custom_instance_form.visible = True
            self._display_add_custom_instance_form()

    def _on_sign_in(self, container: ui.Widget):
        def on_complete(_):
            defer_call(self._refresh_data)
            defer_call(self._display_header)

        self._instances_service.sign_in(container, callback=on_complete)

    def _on_sign_out(self):
        def on_complete():
            defer_call(self._display_header)
            defer_call(self._refresh_data)

        self._instances_service.sign_out(callback=on_complete)
