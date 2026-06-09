import weakref
import carb
import omni.ui as ui
from isaacsim.gui.components.ui_utils import get_style
from typing import Optional
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.ui.utils import defer_call, get_icon
from wandelbots.omni.ui.base import BaseUIBuilder
from wandelbots.omni.instances.models import (
    NOVACustomInstance,
    NOVAInstance,
)
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.instances.instance import InstanceWidget
from wandelbots.omni.ui.instances.widgets.cloud_instances_container import (
    NOVACloudInstancesContainer,
)
from wandelbots.omni.ui.instances.widgets.sign_in_widget import SignInWidget
from wandelbots.omni.utils.auth import get_auth_configs


class NOVAInstanceListUIBuilder(BaseUIBuilder):
    def __init__(self):
        super().__init__(
            title="Wandelbots NOVA | Connected Instances",
            width=600,
            height=600,
        )
        self._instances_service = NOVAInstancesService()
        self._custom_instances: list[NOVACustomInstance] = []
        self._orphan_hosts: set[str] = set()
        self._cloud_instances: dict[str, NOVACloudInstancesContainer] = {}
        self._cloud_instances_container: Optional[ui.VStack] = None
        self._custom_instances_container: Optional[ui.VStack] = None
        self._host_error_message = ""
        self._current_context = {}
        self._instance_widgets: list[InstanceWidget] = []
        self._style = get_style()

    def build_ui(self):
        self._style.update(
            {
                "color": NOVAColor.TEXT_PRIMARY.color,
                "Tooltip": {
                    "background_color": NOVAColor.TOOLTIP_BACKGROUND.color,
                    "color": NOVAColor.TOOLTIP_TEXT.color,
                    "padding": 2,
                    "margin_width": 0,
                    "margin_height": 0,
                    "border_width": 1,
                    "border_radius": 1.5,
                    "border_color": NOVAColor.TOOLTIP_BORDER.color,
                },
            }
        )
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

                ui.Button(
                    image_url=get_icon("add.svg"),
                    width=20,
                    height=20,
                    style={"color": NOVAColor.ACTION_ACTIVE.color},
                    tooltip="Click to add a Wandelbots NOVA instance which is reachable within your network.",
                    clicked_fn=lambda weak_self=weakref.ref(self): (
                        weak_self()._on_toggle_add_custom_instance_form()
                        if weak_self()
                        else None
                    ),
                )
                ui.Button(
                    image_url=get_icon("refresh.svg"),
                    width=20,
                    height=20,
                    style={
                        "color": NOVAColor.ACTION_ACTIVE.color,
                    },
                    tooltip="Click to refresh instance data",
                    clicked_fn=lambda weak_self=weakref.ref(self): (
                        weak_self()._refresh_data() if weak_self() else None
                    ),
                )

    def _display_add_custom_instance_form(self):
        def _on_cancel(weak_self=weakref.ref(self)):
            self_instance = weak_self()
            if not self_instance:
                return
            self_instance._custom_instance_form.visible = False
            self_instance._host_input.model.set_value("")
            self_instance._host_error_message = ""

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
                            lambda model, weak_self=weakref.ref(self): (
                                weak_self()._on_host_input_end_edit(model)
                                if weak_self()
                                else None
                            )
                        )
                        ui.Button(
                            "Add",
                            width=50,
                            clicked_fn=lambda weak_self=weakref.ref(self): (
                                weak_self()._on_add_custom_instance()
                                if weak_self()
                                else None
                            ),
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
            for _, container in self._cloud_instances.items():
                container.build_ui()
            any_not_signed_in = any(
                not self._instances_service.is_signed_in(auth_config_id)
                for auth_config_id in self._cloud_instances.keys()
            )
            if any_not_signed_in:

                def _on_sign_in_callback(auth_config_id, weak_ref=weakref.ref(self)):
                    ref_instance = weak_ref()
                    if ref_instance:
                        ref_instance._on_sign_in(auth_config_id)

                SignInWidget(
                    self._instances_service,
                    _on_sign_in_callback,
                )

    def _on_sign_in(self, auth_config_id: str):
        if auth_config_id not in self._cloud_instances:
            carb.log_error(
                f"Auth config name {auth_config_id} not found in cloud instances containers."
            )
            return
        self._cloud_instances[auth_config_id].refresh_cloud_instances()
        defer_call(self._display_cloud_instances)

    def _display_instances(self):
        """Display all loaded instances in the UI."""
        self._instance_widgets.clear()
        try:
            defer_call(self._display_cloud_instances)
            defer_call(self._display_custom_instances)
        except Exception as e:
            carb.log_error(f"Error displaying instances: {e}")

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
        is_orphan = instance.host in self._orphan_hosts
        widget = InstanceWidget(
            instance=instance,
            instances_service=self._instances_service,
            on_remove=None
            if is_orphan
            else (
                lambda inst, weak_self=weakref.ref(self): (
                    weak_self()._on_remove_custom_instance(inst)
                    if weak_self()
                    else None
                )
            ),
            on_toggle_status=lambda weak_self=weakref.ref(self): (
                weak_self()._refresh_data() if weak_self() else None
            ),
        )
        self._instance_widgets.append(widget)

    def _refresh_data(self):
        self._fetch_instances_data()
        self._display_instances()

    def _get_auth_config_name(self, auth_config_id: str) -> str:
        auth_configs = get_auth_configs()
        return auth_configs.get(auth_config_id).name

    def _fetch_instances_data(self):
        try:
            # Clear previous lists to avoid duplications on refresh
            self._cloud_instances.clear()
            self._custom_instances.clear()

            self._cloud_instances = {
                auth_config_id: NOVACloudInstancesContainer(
                    auth_config_id=auth_config_id,
                    auth_config_name=self._get_auth_config_name(auth_config_id),
                    instances_service=self._instances_service,
                    on_sign_out_fn=lambda auth_config_id=auth_config_id, weak_ref=weakref.ref(self): (
                        weak_ref()._on_sign_out(auth_config_id) if weak_ref() else None
                    ),
                )
                for auth_config_id in self._instances_service.instances_api.get_cloud_instances().keys()
            }

            custom_instances = (
                self._instances_service.instances_api.get_custom_instances()
            )
            for instance in custom_instances:
                self._custom_instances.append(instance)

            # Discover instances referenced on stage prims but not yet in any
            # instance list (e.g. from a saved USD whose NOVA instance was
            # removed or is unreachable).
            known_hosts: set[str] = set()
            for instance in self._custom_instances:
                known_hosts.add(instance.host)
            for container in self._cloud_instances.values():
                for cloud_inst in getattr(container, "_cloud_instances", []):
                    known_hosts.add(cloud_inst.host)

            orphan_instances = self._instances_service.list_stage_instances(known_hosts)
            self._orphan_hosts = {inst.host for inst in orphan_instances}
            for instance in orphan_instances:
                self._custom_instances.append(instance)

            carb.log_info(
                f"Loaded {[(auth, len(self._cloud_instances[auth]._cloud_instances)) for auth in self._cloud_instances.keys()]} cloud instances and {len(self._custom_instances)} custom instances"
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

        self._instances_service.remove_instance(instance, on_complete_fn=on_complete)

    def _on_toggle_add_custom_instance_form(self):
        if self._custom_instance_form.visible:
            self._custom_instance_form.visible = False
        else:
            # Show the form and build it if needed
            self._custom_instance_form.visible = True
            self._display_add_custom_instance_form()

    def _on_sign_out(self, auth_config_id: str = None):
        def on_complete():
            defer_call(self._display_header)
            defer_call(self._refresh_data)

        self._instances_service.sign_out(auth_config_id, callback=on_complete)
