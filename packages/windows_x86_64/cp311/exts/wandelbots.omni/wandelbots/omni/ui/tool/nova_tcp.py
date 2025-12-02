import omni.kit.context_menu
from ..schema.schema_components import (
    GhostObjectApiSchema,
)
from wandelbots.omni.ui.utils import get_icon
import asyncio


def register_nova_tcp_menu():
    """Creates a custom context menu in the Wandelbots NOVA menu for creating NOVA TCPs."""

    # Define the custom menu structure
    create_menu_dict = {
        "name": {
            "Wandelbots NOVA": [
                {
                    "name": "TCP in NOVA",
                    "show_fn": lambda payload: GhostObjectApiSchema.can_create_nova_tcp_object(  # Check if TCP can be created
                        payload
                    ),
                    "onclick_fn": lambda payload: asyncio.run(
                        GhostObjectApiSchema.create_nova_tcp_from_payload(
                            payload
                        )  # Create the TCP object
                    ),
                },
            ]
        },
        "glyph": get_icon("wandelbots.png"),
    }

    stage_create_menu_subscription = (
        omni.kit.context_menu.add_menu(  # Add the custom context menu
            create_menu_dict, "CREATE", "omni.kit.widget.stage"
        )
    )

    return stage_create_menu_subscription
