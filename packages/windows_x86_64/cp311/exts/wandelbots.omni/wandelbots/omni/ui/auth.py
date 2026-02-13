from typing import Callable, Optional
import omni.ui as ui
import webbrowser
import carb
from wandelbots.omni.ui.utils import defer_call
import omni.kit.clipboard as clipboard
from wandelbots.omni.utils.auth import (
    store_auth_tokens,
    poll_token_endpoint,
    get_device_code_info,
    get_auth_config,
    create_auth_controller,
)
from wandelbots.omni.ui.colors import NOVAColor
from omni.kit.async_engine import run_coroutine


class Auth0UIBuilder:
    def __init__(self, auth_config_id: str):
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
        self._auth_config_id = auth_config_id
        self._auth_config = get_auth_config(auth_config_id)
        self._polling = False
        self._device_code = None
        self._auth_controller = create_auth_controller(self._auth_config)

    def build_ui(self, device_code_info: dict):
        verification_url = device_code_info.get(
            "verification_uri"
        ) or device_code_info.get("verification_uri_complete")
        user_code = device_code_info.get("user_code", "")

        # Store device code and interval for polling
        self._device_code = device_code_info.get("device_code")
        self._interval = device_code_info.get("interval", 5)
        self._expires_in = device_code_info.get("expires_in", 900)

        # Build complete verification URL
        if user_code and "?" not in verification_url:
            verification_url = f"{verification_url}?user_code={user_code}"

        with self._container:
            with ui.VStack(spacing=10, alignment=ui.Alignment.CENTER):
                ui.Label(
                    "Authenticate by following these steps:",
                    style={"font_size": 16},
                    alignment=ui.Alignment.CENTER,
                )
                ui.Label(
                    "1. Click 'Open in browser' below (or 'Copy URL' to paste it in your browser)",
                    word_wrap=True,
                    alignment=ui.Alignment.CENTER,
                )
                ui.Label(
                    "2. If asked, enter this code on the website:",
                    word_wrap=True,
                    alignment=ui.Alignment.CENTER,
                )

                with ui.HStack():
                    ui.Spacer()
                    ui.StringField(
                        ui.SimpleStringModel(user_code),
                        read_only=True,
                        height=60,
                        style={
                            "font_size": 24,
                            "alignment": ui.Alignment.CENTER,
                            "padding": 20,
                            "color": 0xFFFFFFFF,
                            "background_color": 0xFF3C3C3C,
                        },
                    )
                    ui.Spacer()

                ui.Label(
                    "3. Complete the authentication in your browser",
                    word_wrap=True,
                    alignment=ui.Alignment.CENTER,
                )

                ui.Spacer(height=5)

                with ui.HStack(spacing=5):
                    ui.Spacer(width=5)
                    ui.Button(
                        "Cancel", height=30, clicked_fn=lambda: self._on_dismissed()
                    )
                    ui.Spacer()
                    ui.Button(
                        "Copy URL",
                        height=30,
                        tooltip=f"Copies the verification URL to your clipboard: {verification_url}",
                        clicked_fn=lambda: self._on_copy_to_clipboard(verification_url),
                    )
                    ui.Button(
                        "Open in browser",
                        height=30,
                        style={
                            "background_color": NOVAColor.PRIMARY_MAIN.color,
                            "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                        },
                        tooltip=f"Opens the verification URL in your default web browser: {verification_url}",
                        clicked_fn=lambda: self._on_open_in_browser(verification_url),
                    )
                    ui.Spacer(width=5)

    async def _check_auth_status(self):
        """Async function to check authentication status and update UI"""
        try:
            carb.log_info("Starting token polling...")
            token_response = await poll_token_endpoint(
                self._auth_controller,
                self._device_code,
                self._interval,
                self._expires_in,
            )
            store_auth_tokens(token_response, self._auth_config_id)
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

        carb.log_info("Copying URL to clipboard. Waiting for callback...")
        # Create task in the event loop
        run_coroutine(self._check_auth_status())

    def _show_device_code_ui(self):
        carb.log_verbose("Fetching device code")
        run_coroutine(get_device_code_info(self._auth_controller)).add_done_callback(
            lambda device_code: defer_call(lambda: self.build_ui(device_code.result()))
        )

    def show(
        self, container: ui.Widget, callback: Optional[Callable[[bool], None]] = None
    ):
        self._container = container
        self._callback = callback
        self._show_device_code_ui()
