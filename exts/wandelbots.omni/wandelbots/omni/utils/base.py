import omni


def get_versions_of_enabled_extensions():
    ext_manager = omni.kit.app.get_app().get_extension_manager()
    extensions = ext_manager.get_extensions()

    versions = {}
    for ext in extensions:
        id = ext["id"]
        version = ext["version"]
        version_string = ".".join(map(str, version[:3]))
        is_enabled = ext["enabled"]
        if is_enabled:
            versions[id] = {
                "name": ext["name"],
                "version": version_string,
                "enabled": is_enabled,
            }
    return versions


def get_current_version():
    versions = get_versions_of_enabled_extensions()
    for ext_id, ext_info in versions.items():
        if ext_id.startswith("wandelbots.omni"):
            return ext_info["version"]
    return "Unknown"
