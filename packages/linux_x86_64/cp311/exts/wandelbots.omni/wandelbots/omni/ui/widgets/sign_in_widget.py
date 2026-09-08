import carb
import omni.ui as ui
from typing import Callable
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.ui.wb_theme import (
    BUTTON_HEIGHT,
    BUTTON_PRIMARY_STYLE,
    COMBOBOX_STYLE,
    FORM_FIELD_WIDTH,
    FORM_SIDE_MARGIN,
    TOOLTIP_RESET,
    build_tooltip,
)
from wandelbots.omni.utils.auth import get_auth_configs
from wandelbots.omni.ui.auth import Auth0UIBuilder
from wandelbots.omni.ui.utils import defer_call


class SignInWidget:
    def __init__(
        self,
        instances_service: NOVAInstancesService,
        on_sign_in_fn: Callable[[str], None],
    ):
        self._instances_service = instances_service
        self._on_sign_in_fn = on_sign_in_fn
        self._selected_auth_name_idx = 0
        self._sign_in_container: ui.Frame = None
        self._build_ui()

    def _build_ui(self):
        """Show the sign-in body for cloud instances.

        The enclosing collapsible section provides the card and the
        "Sign in to Wandelbots NOVA" title, so this only renders the body:
        an "Environment" provider picker and a "Sign In" button below it.
        """

        auth_configs = get_auth_configs()
        auth_config_names = {
            identifier: config.name for identifier, config in auth_configs.items()
        }
        unsigned_auth_ids = [
            identifier
            for identifier in auth_configs.keys()
            if not self._instances_service.is_signed_in(identifier)
        ]

        # A Frame so the Auth0 flow can replace this body in place during sign-in.
        if self._sign_in_container is None:
            self._sign_in_container = ui.Frame(height=0)
        with self._sign_in_container:
            with ui.VStack(spacing=8, height=0):
                display_names = [auth_config_names[id] for id in unsigned_auth_ids]

                def auth_config_id():
                    return unsigned_auth_ids[
                        auth_combo_model.get_item_value_model().as_int
                    ]

                with ui.HStack(height=BUTTON_HEIGHT, spacing=0):
                    ui.Spacer(width=FORM_SIDE_MARGIN)
                    ui.Label(
                        "Environment",
                        width=ui.Fraction(1),
                        alignment=ui.Alignment.LEFT_CENTER,
                    )
                    # Combo shows names but tracks identifiers; wrap in a spacer
                    # VStack to center it vertically. The label stretches so the
                    # combo's right border sits at the shared right margin.
                    with ui.VStack(width=FORM_FIELD_WIDTH):
                        ui.Spacer()
                        auth_combo_model: ui.AbstractItemModel = ui.ComboBox(
                            self._selected_auth_name_idx,
                            *display_names,
                            height=20,
                            style=COMBOBOX_STYLE,
                            tooltip_fn=lambda: build_tooltip(
                                "Select the account provider to sign in with."
                            ),
                        ).model
                        ui.Spacer()
                    ui.Spacer(width=FORM_SIDE_MARGIN)
                # Action button on its own row beneath the combo, right-aligned to
                # the same margin as the combo's right border.
                with ui.HStack(height=BUTTON_HEIGHT):
                    ui.Spacer()
                    ui.Button(
                        "Sign in to NOVA",
                        height=BUTTON_HEIGHT,
                        width=0,
                        alignment=ui.Alignment.CENTER,
                        style={**BUTTON_PRIMARY_STYLE, **TOOLTIP_RESET},
                        clicked_fn=lambda _self=self: _self._on_sign_in(
                            auth_config_id()
                        ),
                        tooltip_fn=lambda: build_tooltip(
                            "Sign in to your Wandelbots NOVA account."
                        ),
                    )
                    ui.Spacer(width=FORM_SIDE_MARGIN)

    def _on_sign_in(self, auth_config_id: str):
        if self._instances_service.is_signed_in(auth_config_id):
            carb.log_info(f"Already signed in for config: {auth_config_id}")
            return

        def sign_in_callback(success: bool):
            defer_call(self._build_ui)
            if success:
                carb.log_info(f"Successfully signed in for config: {auth_config_id}")
                self._on_sign_in_fn(auth_config_id)

        Auth0UIBuilder(auth_config_id).show(
            self._sign_in_container, callback=sign_in_callback
        )
