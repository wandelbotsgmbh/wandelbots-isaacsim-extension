import asyncio
import json
import os
from pathlib import Path
import webbrowser
import carb
import carb.settings
import omni.ext
import omni.timeline
import omni.ui as ui
import omni.usd
from fastapi.openapi.utils import get_openapi
from omni.kit.menu.utils import add_menu_items, remove_menu_items
from omni.services.core import main
from wandelbots.omni.base import omniservice_base_app
from wandelbots.omni.environment import host_database
from wandelbots.omni.ui.schema.schema_extension_ui import SchemaExtensionUI
from wandelbots.omni.io import (
    get_io_stream_service,
    IOStreamService,
    get_bus_io_stream_service,
    BusIOStreamService,
)
from wandelbots.omni.manipulators import get_motion_group_service, MotionGroupService
from wandelbots.omni.utils.base import get_current_version
from wandelbots.omni.ui.utils import make_menu_item_description
import wandelbots.omni.router.v2.base as v2
import omni.kit.app
from wandelbots.omni.environment import credential_store
from wandelbots.omni.ui.instances.instances_list import NOVAInstanceListUIBuilder
import wandelbots.omni.ui.tool
import weakref
from wandelbots.omni.utils.nucleus import NucleusUtils
from wandelbots.omni.ui.asset_browser.browser import WandelbotsAssetBrowserManager

kit_app = main.get_app()


class OmniService(omni.ext.IExt):
    def on_startup(self, ext_id) -> None:
        self.schema_extension = SchemaExtensionUI()
        carb.log_info("Mounting /omniservice")

        # Collect services to bind them to the timeline state
        self.io_stream_service = get_io_stream_service()
        self.bus_io_stream_service = get_bus_io_stream_service()
        self.motion_group_service = get_motion_group_service()
        self.instance_list_window: NOVAInstanceListUIBuilder = None

        self.timeline = omni.timeline.get_timeline_interface()
        carb.log_verbose(f"{self} listening to timeline events")
        self.timeline_sub = (
            self.timeline.get_timeline_event_stream().create_subscription_to_pop(
                lambda event: self._on_timeline_events(
                    async_loop=asyncio.get_event_loop(), event=event
                )
            )
        )

        main.register_mount("/omniservice", omniservice_base_app)
        self.menu_item_name = "Wandelbots NOVA"

        self._sub_stage_event = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(self._on_stage_event)
        )

        self._load_carb_settings()

        self._create_menu(ext_id=ext_id)
        self._load_data()

        self.register_snippets(ext_id)
        self._tools_subscription = wandelbots.omni.ui.tool.register_tools()

        self._asset_browser_manager = WandelbotsAssetBrowserManager()
        self._generate_schema()

    def register_snippets(self, ext_id: str):
        carb.log_verbose(f"Registering {ext_id} snippets")
        self._settings = carb.settings.get_settings()
        manager = omni.kit.app.get_app().get_extension_manager()
        ext_path = manager.get_extension_path(ext_id)
        rep_snippets_folder = str(Path(ext_path).joinpath("snippets").as_posix())

        snippets_folders = (
            self._settings.get("/exts/omni.kit.window.script_editor/snippetFolders")
            or []
        )
        if rep_snippets_folder not in snippets_folders:
            snippets_folders.append(rep_snippets_folder)
        self._settings.set_string_array(
            "/exts/omni.kit.window.script_editor/snippetFolders", snippets_folders
        )

    async def start_all_io_streams(self):
        await self.io_stream_service.start_all_streams()
        await self.bus_io_stream_service.start_all_streams()

    def _on_timeline_events(self, async_loop: asyncio.AbstractEventLoop, event):
        if event.type == omni.timeline.TimelineEventType.PLAY.value:
            async_loop.create_task(self.start_all_io_streams())
            async_loop.create_task(self.motion_group_service.start_streams())

        if event.type in {
            omni.timeline.TimelineEventType.STOP.value,
            omni.timeline.TimelineEventType.PAUSE.value,
        }:
            async_loop.create_task(self.io_stream_service.stop_all_streams())
            async_loop.create_task(self.bus_io_stream_service.stop_all_streams())
            async_loop.create_task(self.motion_group_service.stop_streams())

    def _on_stage_event(self, event):
        if event.type in {
            int(omni.usd.StageEventType.OPENED),
            int(omni.usd.StageEventType.CLOSED),
        }:
            host_database.clear_all()
            if self.instance_list_window:
                self.instance_list_window.setup()
                self.instance_list_window.build_ui()

    async def _async_shutdown(
        motion_group_service: MotionGroupService,
        io_stream_service: IOStreamService,
        bus_io_stream_service: BusIOStreamService,
    ) -> None:
        # We cannot directly call those function due a "cannot enter thread" exception when asyncio.run_until is used.
        # Therefore we collect these tasks in a single async function to  synchronize dependent calls in this function
        await motion_group_service.stop_streams()
        await io_stream_service.clear()
        await bus_io_stream_service.clear()
        host_database.clear_all()

    def on_shutdown(self) -> None:
        self._save_data()
        carb.log_verbose("Unmount Omniservice")
        main.deregister_mount("/omniservice")
        omniservice_base_app.openapi_schema = None

        remove_menu_items(self._menu_items, self.menu_item_name)

        if self.instance_list_window:
            self.instance_list_window.close()

        asyncio.get_event_loop().create_task(
            OmniService._async_shutdown(
                self.motion_group_service,
                self.io_stream_service,
                self.bus_io_stream_service,
            )
        )

        self.timeline_sub.unsubscribe()
        self.timeline_sub = None

        if self.timeline.is_playing():
            carb.log_verbose("Stopping timeline")
            self.timeline.stop()

        self.schema_extension = None
        self._tools_subscription = None
        self._asset_browser_manager = None

    def _create_menu(self, ext_id):
        self._menu_items = [
            make_menu_item_description(
                ext_id=ext_id,
                name="Connected Instances",
                onclick_fun=lambda ext=weakref.proxy(self): ext._open_connect_to_nova(),
                on_ticked_fn=lambda ext=weakref.proxy(self): ext.instance_list_window
                is not None
                and ext.instance_list_window.is_visible,
            ),
            make_menu_item_description(
                ext_id=ext_id,
                header="",
                name="Omniservice API ...",
                onclick_fun=lambda ext=weakref.proxy(self): ext._open_omniservice_api(),
            ),
            make_menu_item_description(
                ext_id=ext_id,
                name="Documentation ...",
                onclick_fun=lambda ext=weakref.proxy(self): ext._open_documentation(),
            ),
            make_menu_item_description(
                ext_id=ext_id,
                name="Developer Portal ...",
                onclick_fun=lambda ext=weakref.proxy(
                    self
                ): ext._open_developer_portal(),
            ),
            make_menu_item_description(
                ext_id=ext_id,
                header="",
                name="About",
                onclick_fun=lambda ext=weakref.proxy(self): ext._open_about(),
            ),
        ]
        add_menu_items(self._menu_items, self.menu_item_name)

    def _load_data(self):
        credential_store.load_data()

    def _save_data(self):
        credential_store.save_data()

    def _open_omniservice_api(self):
        port = self._get_port()
        webbrowser.open(f"http://127.0.0.1:{port}/omniservice/api/v2/ui")

    def _open_documentation(self):
        webbrowser.open("https://docs.wandelbots.io/latest/intro-simulating/")

    def _open_developer_portal(self):
        webbrowser.open("https://portal.wandelbots.io")

    def _open_connect_to_nova(self):
        if self.instance_list_window and self.instance_list_window.is_visible:
            self.instance_list_window.close()
            self.instance_list_window = None
        else:
            self.instance_list_window = NOVAInstanceListUIBuilder()
            self.instance_list_window.setup()
            self.instance_list_window.build_ui()

    def _open_about(self):
        self._version = get_current_version()
        self._about_window = ui.Window("About Wandelbots", width=250, height=100)
        with self._about_window.frame:
            with ui.VStack(spacing=10, alignment=ui.Alignment.CENTER):
                ui.Label(
                    "Wandelbots NOVA Extension",
                    alignment=ui.Alignment.CENTER,
                    style={"font_size": 20, "font_weight": "bold"},
                )
                ui.Label(f"Version {self._version}", alignment=ui.Alignment.CENTER)

    @staticmethod
    def _generate_schema():
        dir_path = os.path.dirname(os.path.abspath(__file__))
        try:
            with open(os.path.join(dir_path, "openapi.json"), "w") as json_file:
                json.dump(
                    get_openapi(
                        title=v2.omniservice_app.title,
                        version=v2.omniservice_app.version,
                        openapi_version=v2.omniservice_app.openapi_version,
                        description=v2.omniservice_app.description,
                        routes=v2.omniservice_app.routes,
                    ),
                    json_file,
                    indent=4,
                )
        except (IOError, OSError) as e:
            carb.log_error(f"Failed to write OpenAPI schema: {str(e)}")
        except Exception as e:
            carb.log_error(f"Unexpected error generating OpenAPI schema: {str(e)}")

    @staticmethod
    def _load_carb_settings():
        NucleusUtils.set_omni_api_token_environment_from_carb_settings()
        settings = carb.settings.acquire_settings_interface()
        base_path = "/exts/omni.services.transport.server.http"
        https_enabled = settings.get(f"{base_path}/https/enabled")

        port = settings.get_as_int(f"{base_path}/port")
        settings.set_int(f"{base_path}/port", port)

        if not https_enabled:
            return

        settings.set_bool(f"{base_path}/https/enabled", https_enabled)

        ssl_settings = {
            "keyfile": "ssl/keyfile",
            "certfile": "ssl/certfile",
            "ssl_cert_reqs": "ssl/ssl_cert_reqs",
            "ssl_ciphers": "ssl/ssl_ciphers",
        }

        try:
            for _, path in ssl_settings.items():
                full_path = f"{base_path}/{path}"
                settings.set(full_path, settings.get(full_path))
        except Exception as e:
            raise ValueError(f"Invalid SSL settings for a secure connection: {str(e)}")

    @staticmethod
    def _get_port():
        settings = carb.settings.acquire_settings_interface()
        return settings.get_as_int("/exts/omni.services.transport.server.http/port")
