import sys
from pathlib import Path

import carb.tokens
import omni


def get_extension_root() -> Path:
    """Root folder of the wandelbots.omni extension.

    Resolved via the extension token Kit registers per enabled extension,
    with the extension manager as fallback. Only meaningful inside a
    running Kit process.
    """
    resolved = carb.tokens.get_tokens_interface().resolve("${wandelbots.omni}")
    if resolved and Path(resolved).is_dir():
        return Path(resolved)

    manager = omni.kit.app.get_app().get_extension_manager()
    path = manager.get_extension_path_by_module("wandelbots.omni")
    if path and Path(path).is_dir():
        return Path(path)
    raise RuntimeError("could not resolve the wandelbots.omni extension root")


def get_kit_python_executable() -> str:
    """Path of a plain Python interpreter matching the Kit runtime.

    Prefer the interpreter bundled with Kit: sys.executable is not
    guaranteed to be a Python binary of the right version in every launch
    configuration, so binaries it starts may not load Kit-matched native
    modules.
    """
    kit_root = Path(carb.tokens.get_tokens_interface().resolve("${kit}"))
    for candidate in ("python/bin/python3", "python/python.exe"):
        python_path = kit_root / candidate
        if python_path.exists():
            return str(python_path)
    if Path(sys.executable).name.lower().startswith("python"):
        return sys.executable
    raise RuntimeError("no Python interpreter matching the Kit runtime found")


def get_versions_of_enabled_extensions() -> dict[str, dict[str, str | bool]]:
    """
    Retrieves versions of all enabled extensions.

    Returns:
        A dictionary mapping extension IDs to a dictionary of name, version, and enabled status.
    """
    app = omni.kit.app.get_app()
    ext_manager = app.get_extension_manager()
    extensions = ext_manager.get_extensions()

    versions = {}
    for ext in extensions:
        if not ext.get("enabled"):
            continue

        ext_id = ext.get("id")
        ext_name = ext.get("name")
        ext_version_tuple = ext.get("version", [])
        ext_version = ".".join(map(str, ext_version_tuple[:3]))

        versions[ext_id] = {"name": ext_name, "version": ext_version, "enabled": True}

    return versions


def get_current_version() -> str:
    """
    Returns the version of the Wandelbots extension currently enabled.

    Returns:
        The version string if found, otherwise "Unknown".
    """
    versions = get_versions_of_enabled_extensions()

    for ext_id, ext_info in versions.items():
        if ext_id.startswith("wandelbots.omni"):
            return ext_info["version"]

    return "Unknown"
