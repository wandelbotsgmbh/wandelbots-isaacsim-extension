import carb
import os
import omni.kit.pipapi
import sys


def _get_pip_prebundle_dir():
    python_dir = f"{sys.version_info.major}.{sys.version_info.minor}"
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), f"../../../pip_prebundle/{python_dir}")
    )


def install_required_packages():
    pip_prebundle_dir = _get_pip_prebundle_dir()
    if pip_prebundle_dir not in sys.path:
        sys.path.insert(0, pip_prebundle_dir)

    requirements_file = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../../../config/requirements.txt")
    )
    if os.path.exists(pip_prebundle_dir):
        carb.log_verbose(f"{pip_prebundle_dir} exists, skipping dependency check.")
        return

    if not os.path.exists(requirements_file):
        carb.log_verbose(
            f"{requirements_file} does not exist, skipping dependency check."
        )
        return

    try:
        install_status_code = omni.kit.pipapi.call_pip(
            args=[
                "install",
                f"--target={pip_prebundle_dir}",
                "-r",
                f"{requirements_file}",
            ],
            surpress_output=False,
        )
        carb.log_info(f"Dependencies installed. status_code: {install_status_code}")

    except Exception as e:
        carb.log_error(f"Failed to install dependencies: {e}")


install_required_packages()


def remove_extension_packages():
    packaged_dir = _get_pip_prebundle_dir()
    if packaged_dir in sys.path:
        sys.path.remove(packaged_dir)

    if packaged_dir in sys.path:
        carb.log_error(f"Failed to remove {packaged_dir} from sys.path")
