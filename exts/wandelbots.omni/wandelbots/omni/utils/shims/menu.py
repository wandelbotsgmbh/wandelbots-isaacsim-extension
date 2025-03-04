# This is a copy of omni.isaac.ui.menu for backwards compatability to Isaac Sim 4.2.0 and to support 4.5.0.
# Will be removed when we drop Isaac Sim 4.2.0 support.

import omni.ext
from omni.kit.menu.utils import MenuItemDescription


def make_menu_item_description(
    ext_id: str, name: str, onclick_fun, action_name: str = ""
) -> None:
    """Easily replace the onclick_fn with onclick_action when creating a menu description

    Args:
        ext_id (str): The extension you are adding the menu item to.
        name (str): Name of the menu item displayed in UI.
        onclick_fun (Function): The function to run when clicking the menu item.
        action_name (str): name for the action, in case ext_id+name don't make a unique string

    Note:
        ext_id + name + action_name must concatenate to a unique identifier.

    """
    action_unique = f"{ext_id.replace(' ', '_')}{name.replace(' ', '_')}{action_name.replace(' ', '_')}"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(ext_id, action_unique)
    action_registry.register_action(ext_id, action_unique, onclick_fun)
    return MenuItemDescription(name=name, onclick_action=(ext_id, action_unique))
