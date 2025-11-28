import os


WANDELBOTS_NOVA_ICON = "icon.png"


def get_icon_path(icon: str) -> str:
    return f"{os.path.dirname(__file__)}/../../../icons/{icon}"
