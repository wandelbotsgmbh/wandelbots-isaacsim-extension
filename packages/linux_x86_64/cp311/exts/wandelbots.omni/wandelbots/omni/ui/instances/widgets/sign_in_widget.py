import weakref
import carb
import omni.ui as ui
from typing import Callable
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.utils.auth import get_auth_configs
from wandelbots.omni.ui.auth import Auth0UIBuilder
from wandelbots.omni.ui.utils import defer_call


class SignInWidget:
    def __init__(
        self,
        instances_service: NOVAInstancesService,
        on_signed_in_fn: Callable[[str], None],
    ):
        self._instances_service = instances_service
        self._on_signed_in_fn = on_signed_in_fn
        self._selected_auth_name_idx = 0
        self._sign_in_container: ui.Frame = None
        self._build_ui()

    def _build_ui(self):
        """Show sign-in prompt for cloud instances."""

        auth_configs = get_auth_configs()
        # Map identifiers to names for display
        auth_config_names = {
            identifier: config.name for identifier, config in auth_configs.items()
        }
        not_signed_in_auth_config_ids = [
            identifier
            for identifier in auth_configs.keys()
            if not self._instances_service.is_signed_in(identifier)
        ]

        with ui.ZStack():
            ui.Rectangle(
                style={
                    "background_color": NOVAColor.BACKGROUND_PAPER.color,
                    "border_radius": 2,
                },
            )
            if self._sign_in_container is None:
                self._sign_in_container = ui.Frame(
                    name="sign_in_frame",
                    height=0,
                    spacing=5,
                    style={
                        "Frame::sign_in_frame": {
                            "margin": 4,
                            "padding": 4,
                        }
                    },
                )
            with self._sign_in_container:
                with ui.VStack(spacing=5):
                    with ui.HStack(spacing=5):
                        ui.Spacer(width=10)
                        ui.Label(
                            "Sign in to Wandelbots NOVA",
                            width=ui.Percent(40),
                        )
                        selection_subscription_model: ui.AbstractItemModel = None
                        if len(not_signed_in_auth_config_ids) > 1:
                            # Display names in combo box but track identifiers
                            display_names = [
                                auth_config_names[id]
                                for id in not_signed_in_auth_config_ids
                            ]
                            selection_subscription_model = ui.ComboBox(
                                self._selected_auth_name_idx,
                                *display_names,
                            ).model
                        else:
                            ui.Spacer()

                        def auth_config_id():
                            if selection_subscription_model:
                                return not_signed_in_auth_config_ids[
                                    selection_subscription_model.get_item_value_model().as_int
                                ]
                            return not_signed_in_auth_config_ids[
                                self._selected_auth_name_idx
                            ]

                        ui.Button(
                            "Sign in",
                            height=20,
                            width=ui.Percent(20),
                            style={"background_color": NOVAColor.PRIMARY_MAIN.color},
                            clicked_fn=lambda weak_self=weakref.proxy(self): (
                                weak_self._on_sign_in(auth_config_id())
                            ),
                        )
                        ui.Spacer(width=5)

    def _on_sign_in(self, auth_config_id: str):
        if self._instances_service.is_signed_in(auth_config_id):
            carb.log_info(f"Already signed in for config: {auth_config_id}")
            return

        def sign_in_callback(success: bool):
            defer_call(self._build_ui)
            if success:
                carb.log_info(f"Successfully signed in for config: {auth_config_id}")
                self._on_signed_in_fn(auth_config_id)

        Auth0UIBuilder(auth_config_id).show(
            self._sign_in_container, callback=sign_in_callback
        )
