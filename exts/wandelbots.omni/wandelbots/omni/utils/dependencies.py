import carb
import os
import toml
import omni.kit.pipapi
from importlib.metadata import distributions


def read_dependencies():
    toml_file_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "config",
        "extension.toml",
    )

    with open(toml_file_path, "r") as file:
        data = toml.load(file)

    # Extracting the requirements list from the 'python.pipapi' section
    pipapi = data.get("python", {}).get("pipapi", {})
    requirements = pipapi.get("requirements", [])
    modules = pipapi.get("modules", [])

    dependencies = []
    for requirement, module in zip(requirements, modules):
        package, version = requirement.split("==")
        dependencies.append({"package": package, "version": version, "module": module})

    return dependencies


def check_dependencies():
    dependencies = read_dependencies()

    try:
        for dependency in dependencies:
            if not _is_installed(dependency):
                _install_package(dependency)
    except Exception as e:
        carb.log_error(f"Failed to install dependencies: {e}")


def _install_package(dependency):
    package = dependency["package"]
    version = dependency["version"]
    module = dependency["module"]

    carb.log_warn(f"Installing {package} ({version})...")

    omni.kit.pipapi.install(
        package=package,
        version=version,
        ignore_import_check=False,
        ignore_cache=True,
        use_online_index=True,
        surpress_output=False,
        module=module,
    )
    carb.log_warn(f"{package} ({version}) installed")


def _is_installed(dependency) -> bool:
    for dist in distributions():
        package = dependency["package"]
        version = dependency["version"]
        if dist.metadata["Name"] == dependency["package"]:
            installed_version = dist.version
            if installed_version == dependency["version"]:
                carb.log_info(
                    f"{package} ({version}) is up to date. Nothing to do here."
                )
                return True

    return False
