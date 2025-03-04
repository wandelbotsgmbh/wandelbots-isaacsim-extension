import asyncio
import omni.ui as ui
import webbrowser
import carb
from nova.auth.authorization import Auth0DeviceAuthorization
from wandelbots.omni.utils.auth import Auth0Model
from wandelbots.omni.environment import load_env

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

    def _on_dismissed_window(self, is_visible):
        self._dismissed = not is_visible

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
                        lambda: webbrowser.open(open_verification_url)
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

    def create_auth_ui(self, device_code_info, auth0_device_auth):
        self._auth_window = ui.Window(
            self._window_title, width=400, height=240, visibility_changed_fn=self._on_dismissed_window
        )
        url = f"{device_code_info.verification_uri}?user_code={device_code_info.user_code}"
        self.create_initial_view(device_code_info, url)

        async def check_auth_status():
            try:
                token = await auth0_device_auth.poll_token_endpoint()
                Auth0Model.store_token(token)
                self._auth_window.frame.clear()
                if self._dismissed: 
                    return
                close_button = self.create_success_view()
                close_button.set_clicked_fn(
                    lambda: setattr(self._auth_window, "visible", False)
                )
            except Exception:
                Auth0Model.store_token(None)
                self._auth_window.frame.clear()
                if self._dismissed: 
                    return
                close_button = self.create_error_view()
                close_button.set_clicked_fn(
                    lambda: setattr(self._auth_window, "visible", False)
                )

        # Schedule the check_auth_status coroutine
        asyncio.create_task(check_auth_status())

    def display_auth_window(self):
        config = load_env()
        auth0_domain = config("AUTH0_DOMAIN")
        auth0_client_id = config("AUTH0_CLIENT_ID")
        auth0_audience = config("AUTH0_AUDIENCE")

        auth0_device_auth = Auth0DeviceAuthorization(
            auth0_domain, auth0_client_id, auth0_audience
        )

        try:
            # Request a device code
            device_code_info = auth0_device_auth.request_device_code()
            self.create_auth_ui(device_code_info, auth0_device_auth)

        except Exception as e:
            carb.log_error(f"An error occurred: {e}")



