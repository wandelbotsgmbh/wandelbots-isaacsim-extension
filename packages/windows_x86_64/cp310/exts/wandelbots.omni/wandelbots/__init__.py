import sys
from pathlib import Path
import carb


def register_bundled_packages() -> None:
    try:
        pre_bundle_path = Path(__file__).absolute().parents[1].joinpath("pip_prebundle")
        if not pre_bundle_path.exists():
            raise FileNotFoundError(
                f"Pre-bundle path does not exist: {pre_bundle_path}"
            )
        sys.path = [str(pre_bundle_path)] + [
            p for p in sys.path if p != str(pre_bundle_path)
        ]
        carb.log_verbose(f"Registered bundled packages from {str(pre_bundle_path)}")
    except FileNotFoundError as e:
        carb.log_error(f"{str(e)} Skipping registration of bundled packages.")


register_bundled_packages()
