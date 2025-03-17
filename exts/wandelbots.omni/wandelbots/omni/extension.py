import json
import os
import omni.usd
import carb.settings
import weakref
import webbrowser
import omni.ui as ui
import omni.kit.pipapi

import omni.ext
import omni.kit.commands
import omni.usd
from wandelbots.omni.base import omniservice_app
from wandelbots.omni.environment import host_database
from fastapi.openapi.utils import get_openapi
from wandelbots.omni.utils.shims.menu import make_menu_item_description
from omni.kit.menu.utils import add_menu_items, remove_menu_items
from omni.services.core import main
import carb
from wandelbots.omni.utils.base import get_current_version
from wandelbots.omni.utils.dependencies import check_dependencies
from wandelbots.omni.router.v1.utils import StreamManager, get_stream_manager
import omni.timeline

kit_app = main.get_app()


class OmniService(omni.ext.IExt):
    def on_startup(self, ext_id) -> None:
        carb.log_info("Mounting /omniservice")
        self.stream_manager = StreamManager()
        check_dependencies()

        omniservice_app.dependency_overrides[get_stream_manager] = lambda: self.stream_manager
        main.register_mount("/omniservice", omniservice_app)
        self.menu_item_name = "Wandelbots NOVA"

        self._sub_stage_event = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(self._on_stage_event)
        )
        
        self._load_carb_settings()
        self._generate_schema()
        self._create_menu(ext_id=ext_id)

        

    def _on_stage_event(self, event):
        if event.type == int(omni.usd.StageEventType.OPENED) or event.type == int(
            omni.usd.StageEventType.CLOSED
        ):
            host_database.clear_all()

    def on_shutdown(self) -> None:
        carb.log_info("Unmount /omniservice")
        main.deregister_mount("/omniservice")
        omniservice_app.openapi_schema = None
        

        remove_menu_items(self._menu_items, self.menu_item_name)
        omniservice_app.dependency_overrides[get_stream_manager] = None
        
        self.stream_manager.close()
        self.stream_manager = None
        host_database.clear_all()

        timeline = omni.timeline.get_timeline_interface()
        if timeline.is_playing():
            carb.log_info("Stopping timeline")
            timeline.stop()

    def _create_menu(self, ext_id):
        self._menu_items = [
            make_menu_item_description(
                ext_id=ext_id,
                name="Open Omniservice API",
                onclick_fun=lambda a=weakref.proxy(self): a._open_omniservice_api(),
            ),
            make_menu_item_description(
                ext_id=ext_id,
                name="Open Documentation",
                onclick_fun=lambda a=weakref.proxy(self): a._open_documentation(),
            ),
            make_menu_item_description(
                ext_id=ext_id,
                name="Open Developer Portal",
                onclick_fun=lambda a=weakref.proxy(self): a._open_developer_portal(),
            ),
            make_menu_item_description(
                ext_id=ext_id,
                name="Authenticate",
                onclick_fun=lambda a=weakref.proxy(self): a._authorize(),
            ),
        ]
        add_menu_items(self._menu_items, self.menu_item_name)

    @staticmethod
    def _open_omniservice_api():
        port = OmniService._get_port()
        webbrowser.open(f"http://127.0.0.1:{port}/omniservice/api")

    @staticmethod
    def _open_documentation():
        webbrowser.open("https://docs.wandelbots.io/latest/intro-simulating/")

    @staticmethod
    def _open_developer_portal():
        webbrowser.open("https://portal.wandelbots.io")

    @staticmethod
    def _authorize():
        # Lazy loading of dependency to prevent imports missing while nova-sdk is being installed
        from wandelbots.omni.gui.auth import Auth0UIBuilder
        ui_builder = Auth0UIBuilder()
        ui_builder.display_auth_window()

    @staticmethod
    def _open_about():
        OmniService._version = get_current_version()
        OmniService._about_window = ui.Window("About Wandelbots", width=250, height=100)
        with OmniService._about_window.frame:
            with ui.VStack(spacing=10, alignment=ui.Alignment.CENTER):
                ui.Label(
                    "Wandelbots NOVA Extension",
                    alignment=ui.Alignment.CENTER,
                    style={"font_size": 20, "font_weight": "bold"},
                )
                ui.Label(f"Version {OmniService._version}", alignment=ui.Alignment.CENTER)

    @staticmethod
    def _generate_schema():
        dir_path = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(dir_path, "openapi.json"), "w") as json_file:
            json.dump(
                get_openapi(
                    title=omniservice_app.title,
                    version=omniservice_app.version,
                    openapi_version=omniservice_app.openapi_version,
                    description=omniservice_app.description,
                    routes=omniservice_app.routes,
                ),
                json_file,
            )

    @staticmethod
    def _load_carb_settings():
        settings = carb.settings.acquire_settings_interface()
        base_path = "/exts/omni.services.transport.server.http"
        https_enabled = settings.get(f"{base_path}/https/enabled")

        port = settings.get_as_int(f"{base_path}/port")
        settings.set_int(f"{base_path}/port", port)

        if not https_enabled:
            return

        settings.set_bool(f"{base_path}/https/enabled", https_enabled)

        # Serves app on port 8433
        ssl_settings = {
            "keyfile": "ssl/keyfile",
            "certfile": "ssl/certfile",
            "ssl_cert_reqs": "ssl/ssl_cert_reqs",
            "ssl_ciphers": "ssl/ssl_ciphers",
        }
        try:
            for key, path in ssl_settings.items():
                full_path = f"{base_path}/{path}"
                settings.set(full_path, settings.get(full_path))

        except Exception as e:
            raise ValueError(f"Invalid SSL settings for a secure connection: {str(e)}")

    @staticmethod
    def _get_port():
        settings = carb.settings.acquire_settings_interface()
        return settings.get_as_int("/exts/omni.services.transport.server.http/port")
