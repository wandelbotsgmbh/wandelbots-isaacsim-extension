import omni


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
