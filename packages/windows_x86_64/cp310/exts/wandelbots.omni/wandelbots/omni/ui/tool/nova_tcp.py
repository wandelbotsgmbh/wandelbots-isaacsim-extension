import asyncio

import omni.kit.context_menu

from wandelbots.omni.ui.utils import get_icon

from ..schema.schema_components import (
    GhostObjectApiSchema,
)


def register_tcp_from_isaac_to_nova_menu():
    """Creates a custom context menu in the Wandelbots NOVA menu for creating NOVA TCPs based on Isaac Sim TCP prims."""

    # Define the custom menu structure
    create_menu_dict = {
        "name": {
            "Wandelbots NOVA": [
                {
                    "name": "TCP in NOVA",
                    "show_fn": lambda payload: (
                        GhostObjectApiSchema.can_create_nova_tcp_object(  # Check if TCP can be created
                            payload
                        )
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
            create_menu_dict, "CREATE"
        )
    )
    return stage_create_menu_subscription


def register_tcp_from_nova_to_isaac_menu():
    """Creates a custom context menu in the Wandelbots NOVA menu for creating Isaac Sim TCP prims based on NOVA TCPs."""

    # Define the custom menu structure
    create_menu_dict = {
        "name": {
            "Wandelbots NOVA": [
                {
                    "name": "TCP from NOVA",
                    "show_fn": lambda payload: (
                        GhostObjectApiSchema.can_create_tcp_prim_from_nova(  # Check if TCP can be created
                            payload
                        )
                    ),
                    "onclick_fn": lambda payload: asyncio.run(
                        GhostObjectApiSchema.create_tcp_prim_from_nova(
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
            create_menu_dict, "CREATE"
        )
    )
    return stage_create_menu_subscription
