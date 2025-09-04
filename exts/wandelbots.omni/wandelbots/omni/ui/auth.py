from typing import Optional
import omni.ui as ui
import webbrowser
import asyncio
import carb
from wandelbots.omni.ui.utils import defer_call
import omni.kit.clipboard as clipboard
from wandelbots.omni.utils.auth import (
    store_auth_token,
    poll_token_endpoint,
    get_device_code_info,
    get_auth_config,
)
from nova.auth.authorization import Auth0DeviceAuthorization
from wandelbots.omni.ui.colors import NOVAColor


class Auth0UIBuilder:
    def __init__(self):
        self._container = None
        self._callback = None
        self._dismissed = False
        self._close_button_lbl = "Close"
        self._auth_button_lbl = "Open instance confirmation"
        self._auth_subline_lbl = "This code must be identical to the code displayed during the instance confirmation:"
        self._success_headline_lbl = "Authentication successful. Happy simulating!"
        self._error_headline_lbl = (
            "Authentication not possible. Please try again later."
        )
        self._auth_config = get_auth_config()
        self._polling = False
        self._auth_controller = Auth0DeviceAuthorization(self._auth_config)

    def build_ui(self):
        device_code_info = get_device_code_info(self._auth_controller)
        verification_url = f"{device_code_info.verification_uri}?user_code={device_code_info.user_code}"
        with self._container:
            with ui.VStack(spacing=5):
                with ui.HStack():
                    ui.Spacer(width=15)
                    ui.Label(
                        "Copy URL or click open in browser to proceed:",
                    )
                    ui.Spacer(width=10)
                with ui.HStack():
                    ui.Spacer(width=10)
                    ui.StringField(
                        ui.SimpleStringModel(verification_url), read_only=True
                    )
                    ui.Spacer(width=10)
                with ui.HStack(spacing=5):
                    ui.Spacer(width=5)
                    ui.Button(
                        "Cancel", height=30, clicked_fn=lambda: self._on_dismissed()
                    )
                    ui.Spacer()
                    ui.Button(
                        "Copy URL",
                        height=30,
                        clicked_fn=lambda: self._on_copy_to_clipboard(verification_url),
                    )
                    ui.Button(
                        "Open in Browser",
                        height=30,
                        style={
                            "background_color": NOVAColor.PRIMARY_MAIN.color,
                            "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                        },
                        clicked_fn=lambda: self._on_open_in_browser(verification_url),
                    )
                    ui.Spacer(width=5)
                ui.Spacer(height=10)

    async def _check_auth_status(self):
        """Async function to check authentication status and update UI"""
        try:
            carb.log_info("Starting token polling...")
            token = await poll_token_endpoint(self._auth_controller)
            store_auth_token(token)
            self._polling = False
            self._callback(True)

        except Exception as e:
            carb.log_error(f"Error during authentication: {e}")
            if self._dismissed:
                self._polling = False
                self._callback(False)
                return

            self._polling = False
            self._callback(False)

    def _on_dismissed(self):
        self._polling = False
        self._callback(False)

    def _on_copy_to_clipboard(self, url):
        """Helper function to handle button click"""
        if not self._polling:
            self._waiting_for_auth()
        clipboard.copy(url)
        carb.log_info("Copying URL to clipboard. Waiting for callback...")

    def _on_open_in_browser(self, url):
        """Helper function to handle button click"""
        if not self._polling:
            self._waiting_for_auth()
        webbrowser.open(url)
        carb.log_info("Open in browser. Waiting for callback...")

    def _waiting_for_auth(self):
        self._polling = True
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # Create a new event loop if one doesn't exist
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        carb.log_info("Copying URL to clipboard. Waiting for callback...")
        # Create task in the event loop
        loop.create_task(self._check_auth_status())

    def show(self, container: ui.Widget, callback: Optional[callable] = None):
        self._container = container
        self._callback = callback
        defer_call(self.build_ui)
