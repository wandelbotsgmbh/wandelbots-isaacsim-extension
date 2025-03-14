import omni.ui as ui
import webbrowser
import asyncio
import carb
from wandelbots.omni.utils.auth import store_auth_token, poll_token_endpoint, get_device_code_info, get_auth_config
from nova.auth.authorization import Auth0DeviceAuthorization

class Auth0UIBuilder:
    def __init__(self):
        self._auth_window = None
        self._dismissed = False 
        self._window_title = "Wandelbots NOVA | Authentication"
        self._close_button_lbl = "Close"
        self._auth_button_lbl = "Open instance confirmation"
        self._auth_subline_lbl = "This code must be identical to the code displayed during the instance confirmation:"
        self._success_headline_lbl = "Authentication successful. Happy simulating!"
        self._error_headline_lbl = "Authentication not possible. Please try again later."
        self._auth_config = get_auth_config()
        self._auth_controller = Auth0DeviceAuthorization(self._auth_config)

    def _on_dismissed_window(self, is_visible):
        self._dismissed = not is_visible

    def _handle_button_click(self, url):
        """Helper function to handle button click"""
       
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # Create a new event loop if one doesn't exist
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        webbrowser.open(url)
        # Create task in the event loop
        loop.create_task(self.check_auth_status())

    def create_initial_view(self, device_code_info, open_verification_url):
        with self._auth_window.frame:
            with ui.VStack(spacing=14, alignment=ui.Alignment.CENTER):
                with ui.HStack(spacing=50):
                    ui.Spacer()
                    ui.Label(
                        self._auth_subline_lbl,
                        alignment=ui.Alignment.CENTER,
                        width=300,
                        style={"font_size": 16},
                        word_wrap=True,
                    )
                    ui.Spacer()
                ui.Label(
                    device_code_info.user_code,
                    alignment=ui.Alignment.CENTER,
                    style={"font_size": 24},
                )
                with ui.HStack(spacing=10, alignment=ui.Alignment.CENTER):
                    ui.Spacer()
                    open_url_button = ui.Button(
                        self._auth_button_lbl, width=300, height=50
                    )
                    open_url_button.set_clicked_fn(
                        lambda: self._handle_button_click(open_verification_url)
                    )
                    ui.Spacer()
                ui.Label(
                    open_verification_url,
                    alignment=ui.Alignment.CENTER,
                    style={"font_size": 12},
                )

    def create_success_view(self):
        self._auth_window = ui.Window(
            self._window_title, width=350, height=180
        )
        with self._auth_window.frame:
            with ui.VStack(spacing=14, alignment=ui.Alignment.CENTER):
                ui.Label(
                    self._success_headline_lbl,
                    alignment=ui.Alignment.CENTER,
                    style={"font_size": 16},
                    word_wrap=True,
                )
                with ui.HStack(spacing=10, alignment=ui.Alignment.CENTER):
                    ui.Spacer()
                    close_button = ui.Button(self._close_button_lbl, width=300, height=50)
                    ui.Spacer()

                return close_button

    def create_error_view(self):
        if self._dismissed:
            return

        self._auth_window = ui.Window(
            self._window_title, width=350, height=180
        )
        with self._auth_window.frame:
            with ui.VStack(spacing=14, alignment=ui.Alignment.CENTER):
                ui.Label(
                    self._error_headline_lbl,
                    alignment=ui.Alignment.CENTER,
                    style={"font_size": 16},
                    word_wrap=True,
                )
                with ui.HStack(spacing=10, alignment=ui.Alignment.CENTER):
                    ui.Spacer()
                    close_button = ui.Button(self._close_button_lbl, width=300, height=50)
                    ui.Spacer()

                return close_button

    def create_auth_ui(self):
        self._auth_window = ui.Window(
            self._window_title, width=400, height=240, visibility_changed_fn=self._on_dismissed_window
        )
        device_code_info = get_device_code_info(self._auth_controller)
        url = f"{device_code_info.verification_uri}?user_code={device_code_info.user_code}"
        self.create_initial_view(device_code_info, url)

    async def check_auth_status(self):
        """Async function to check authentication status and update UI"""
        try:
            carb.log_info("Starting token polling...")
            token = await poll_token_endpoint(self._auth_controller)
            store_auth_token(token)
            self._auth_window.frame.clear()
            if self._dismissed: 
                return
            close_button = self.create_success_view()
            close_button.set_clicked_fn(
                lambda: setattr(self._auth_window, "visible", False)
            )
        except Exception as e:
            carb.log_error(f"Error during authentication: {e}")
            self._auth_window.frame.clear()
            if self._dismissed: 
                return
            close_button = self.create_error_view()
            close_button.set_clicked_fn(
                lambda: setattr(self._auth_window, "visible", False)
            )

    def display_auth_window(self):
        self.create_auth_ui()



                            