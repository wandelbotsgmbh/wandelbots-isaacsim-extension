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


# Prepend pip_prebundle to sys.path at import time. This runs automatically because
# importing the `wandelbots.omni` extension module imports this parent package first,
# so the bundled dependencies (e.g. wandelbots_api_client) are on sys.path before any
# `wandelbots.omni` submodule imports them. Do NOT rely on a separate
# `[[python.module]] name = "wandelbots"` entry in extension.toml for this: that entry
# causes OmniGraph to scan wandelbots/omni/ogn twice and emit a duplicate node-type
# registration warning.
register_bundled_packages()
