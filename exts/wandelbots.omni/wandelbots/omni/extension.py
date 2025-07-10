import asyncio
import json
import os
import weakref
import webbrowser

import carb
import carb.settings
import omni.ext
import omni.kit.commands
import omni.timeline
import omni.ui as ui
import omni.usd
from fastapi.openapi.utils import get_openapi
from omni.kit.menu.utils import add_menu_items, remove_menu_items
from omni.services.core import main
from wandelbots.omni.base import omniservice_base_app
from wandelbots.omni.environment import host_database
from wandelbots.omni.io import get_io_stream_service, IOStreamService
from wandelbots.omni.manipulators import get_motion_group_service, MotionGroupService
from wandelbots.omni.utils.base import get_current_version
from wandelbots.omni.utils.dependencies import check_dependencies
from wandelbots.omni.utils.shims.menu import make_menu_item_description
import wandelbots.omni.router.v2.base as v2

kit_app = main.get_app()


class OmniService(omni.ext.IExt):
    def on_startup(self, ext_id) -> None:
        check_dependencies()
        carb.log_info("Mounting /omniservice")

        # Collect services to bind them to the timeline state
        self.io_stream_service = get_io_stream_service()
        self.motion_group_service = get_motion_group_service()

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
        self._generate_schema()
        self._create_menu(ext_id=ext_id)

    async def start_all_io_streams(self):
        await self.io_stream_service.start_all_streams()

    def _on_timeline_events(self, async_loop: asyncio.AbstractEventLoop, event):
        if event.type == omni.timeline.TimelineEventType.PLAY.value:
            async_loop.create_task(self.start_all_io_streams())
            async_loop.create_task(self.motion_group_service.start_streams())

        if event.type in {
            omni.timeline.TimelineEventType.STOP.value,
            omni.timeline.TimelineEventType.PAUSE.value,
        }:
            async_loop.create_task(self.io_stream_service.stop_all_streams())
            async_loop.create_task(self.motion_group_service.stop_streams())

    def _on_stage_event(self, event):
        if event.type in {
            int(omni.usd.StageEventType.OPENED),
            int(omni.usd.StageEventType.CLOSED),
        }:
            host_database.clear_all()

    async def _async_shutdown(
        motion_group_service: MotionGroupService, io_stream_service: IOStreamService
    ) -> None:
        # We cannot directly call those function due a "cannot enter thread" exception when asyncio.run_until is used.
        # Therefore we collect these tasks in a single async function to  synchronize dependent calls in this function
        await motion_group_service.stop_streams()
        await io_stream_service.clear()
        host_database.clear_all()

    def on_shutdown(self) -> None:
        carb.log_verbose("Unmount Omniservice")
        main.deregister_mount("/omniservice")
        omniservice_base_app.openapi_schema = None

        remove_menu_items(self._menu_items, self.menu_item_name)

        asyncio.get_event_loop().create_task(
            OmniService._async_shutdown(
                self.motion_group_service, self.io_stream_service
            )
        )

        self.timeline_sub.unsubscribe()
        self.timeline_sub = None

        if self.timeline.is_playing():
            carb.log_verbose("Stopping timeline")
            self.timeline.stop()

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
        webbrowser.open(f"http://127.0.0.1:{port}/omniservice/api/v2/ui")

    @staticmethod
    def _open_documentation():
        webbrowser.open("https://docs.wandelbots.io/latest/intro-simulating/")

    @staticmethod
    def _open_developer_portal():
        webbrowser.open("https://portal.wandelbots.io")

    @staticmethod
    def _authorize():
        from wandelbots.omni.ui.auth import Auth0UIBuilder

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
                ui.Label(
                    f"Version {OmniService._version}", alignment=ui.Alignment.CENTER
                )

    @staticmethod
    def _generate_schema():
        dir_path = os.path.dirname(os.path.abspath(__file__))

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
