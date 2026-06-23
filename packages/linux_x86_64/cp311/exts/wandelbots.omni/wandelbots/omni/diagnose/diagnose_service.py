import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Callable

import carb
import omni.client
import omni.kit.app
import omni.usd

import wandelbots_api_client.v2 as wb_v2
from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVAInstance, NOVACloudInstance
from wandelbots.omni.utils.api import _get_user_agent

# Carb setting holding the path of the current Kit/Isaac Sim session log file.
ISAAC_SIM_LOG_SETTING = "/log/file"

PACKAGE_NAME_SUFFIX = "wb-ov-diagnose.zip"


@dataclass
class DiagnosePackageResult:
    """Outcome of a diagnose package creation run."""

    path: str
    succeeded_instances: list[str] = field(default_factory=list)
    failed_instances: list[str] = field(default_factory=list)
    log_included: bool = False


def get_isaac_sim_log_path() -> str | None:
    """Return the path of the current Isaac Sim session log file, if known."""
    log_path = carb.settings.get_settings().get(ISAAC_SIM_LOG_SETTING)
    if log_path and isinstance(log_path, str) and log_path.strip():
        return log_path
    return None


def get_stage_tree() -> str | None:
    """Return a text representation of the current USD stage hierarchy."""
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return None

    lines: list[str] = []
    for prim in stage.Traverse():
        path_string = prim.GetPath().pathString
        # Depth from the path components; absolute root "/" has no name.
        depth = max(path_string.count("/") - 1, 0)
        indent = "  " * depth
        type_name = prim.GetTypeName() or "—"
        lines.append(f"{indent}- {prim.GetName()}  ({type_name})  [{path_string}]")

    if not lines:
        return "Stage is empty."
    return "\n".join(lines)


def get_motion_groups_json() -> str | None:
    """
    Return the motion groups configured in the scene as a JSON string.

    Mirrors the Omniservice ``GET /manipulators/motion-groups`` endpoint by using
    the MotionGroupService directly. Returns None if the data could not be gathered.
    """
    try:
        from wandelbots.omni.manipulators import get_motion_group_service

        service = get_motion_group_service()
        configurations = {}
        for prim_path in service.get_all_motion_group_prim_paths():
            config = service.get_motion_group_configuration(prim_path)
            configurations[prim_path] = (
                config.model_dump(mode="json") if config is not None else None
            )
        return json.dumps(configurations, indent=2)
    except Exception as exc:
        carb.log_warn(f"Could not gather motion groups for diagnose package: {exc}")
        return None


def _build_readme(
    timestamp: str,
    additional_info: str,
    user_agent: str,
    contents: list[str],
) -> str:
    """Build the README.md content bundled into the diagnose package."""
    info = additional_info.strip() if additional_info else ""
    contents_lines = (
        "\n".join(f"- `{name}`" for name in contents)
        if contents
        else "_No files collected._"
    )
    return (
        "# Wandelbots NOVA & Isaac Sim Diagnose Package\n"
        "\n"
        f"Generated: {timestamp}\n"
        "\n"
        "## Additional Information\n"
        "\n"
        f"{info if info else '_None provided._'}\n"
        "\n"
        "## Environment\n"
        "\n"
        f"- User-Agent: {user_agent}\n"
        "\n"
        "## Contents\n"
        "\n"
        f"{contents_lines}\n"
    )


def _make_api_client(instance: NOVAInstance) -> wb_v2.ApiClient | None:
    """Create an authenticated API client for the given instance."""
    if isinstance(instance, NOVACloudInstance):
        token = get_instances_api().get_auth_token_from_host(instance.host)
        return instance.create_api_client(token=token)
    return instance.create_api_client()


def _sanitize_filename(name: str) -> str:
    """Make a display name safe to use as a file name inside the zip."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return sanitized or "instance"


async def _resolve_scene_dir() -> str | None:
    """
    Resolve the directory of the current scene, prompting the user to save the
    stage first if it has no file path yet. Returns None if no path could be
    determined (caller should notify the user).
    """
    stage_url = omni.usd.get_context().get_stage_url() or ""
    if not stage_url:
        await omni.usd.get_context().save_stage_with_callback_async(lambda r, e: None)
        stage_url = omni.usd.get_context().get_stage_url() or ""
    if not stage_url:
        return None
    if "://" in stage_url:
        return stage_url.rsplit("/", 1)[0] if "/" in stage_url else stage_url
    return os.path.dirname(stage_url)


async def _fetch_instance_package(instance: NOVAInstance) -> bytearray | None:
    """Download the NOVA diagnosis package zip for a single instance."""
    api_client = _make_api_client(instance)
    if api_client is None:
        carb.log_warn(
            f"Could not create API client for instance '{instance.display_name}'"
        )
        return None
    try:
        system_api = wb_v2.SystemApi(api_client)
        return await system_api.get_diagnose_package()
    finally:
        try:
            await api_client.close()
        except Exception as exc:
            carb.log_warn(f"Error closing API client: {exc}")


async def _write_package(path: str, data: bytes) -> None:
    """Write the final zip next to the scene (local filesystem or Nucleus)."""
    if "://" in path:
        result = await omni.client.write_file_async(path, data)
        if result != omni.client.Result.OK:
            raise RuntimeError(f"omni.client.write_file_async failed: {result}")
    else:
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)


async def create_diagnose_package(
    instances: list[NOVAInstance],
    timestamp: str,
    additional_info: str = "",
    include_stage_tree: bool = False,
    include_motion_groups: bool = False,
    progress_cb: Callable[[str, float], None] | None = None,
) -> DiagnosePackageResult:
    """
    Download the NOVA diagnosis package for each given instance, gather the
    current Isaac Sim session log, and bundle everything into a single
    timestamped zip written next to the current scene.

    A README.md (additional info, user agent, contents index) is always added.
    The USD stage tree and the configured motion groups are added as separate
    files when the respective opt-in flag is set.

    Args:
        instances: NOVA instances to include in the package.
        timestamp: Timestamp prefix for the output file name (e.g. "20260608-143000").
        additional_info: Free-form text provided by the user, stored in README.md.
        include_stage_tree: Add the USD stage hierarchy as stage_tree.txt.
        include_motion_groups: Add the scene motion groups as motion_groups.json.
        progress_cb: Optional callback invoked with (message, fraction) where
            fraction is in [0, 1], to report progress to the UI.

    Returns:
        DiagnosePackageResult describing the written file and per-instance outcome.

    Raises:
        RuntimeError: If the scene has no path (after a save prompt) or no
            instance package could be downloaded.
    """
    # One step per instance download, plus log, optional stage tree / motion
    # groups, and the final packaging step.
    total_steps = (
        len(instances)
        + 1
        + (1 if include_stage_tree else 0)
        + (1 if include_motion_groups else 0)
        + 1
    )
    completed = 0

    async def report(message: str):
        """Report progress and yield a frame so the UI can repaint."""
        if progress_cb is not None:
            progress_cb(message, min(completed / total_steps, 1.0))
        # Let omni.ui render the update before the next (possibly blocking) step.
        await omni.kit.app.get_app().next_update_async()

    await report("Resolving scene location…")
    scene_dir = await _resolve_scene_dir()
    if scene_dir is None:
        raise RuntimeError("Please save the scene before creating a diagnose package.")

    is_nucleus = "://" in scene_dir
    file_name = f"{timestamp}-{PACKAGE_NAME_SUFFIX}"
    output_path = (
        f"{scene_dir}/{file_name}" if is_nucleus else os.path.join(scene_dir, file_name)
    )

    succeeded: list[str] = []
    failed: list[str] = []
    packages: dict[str, bytearray] = {}

    for instance in instances:
        await report(f"Downloading diagnosis from {instance.display_name}…")
        try:
            zip_bytes = await _fetch_instance_package(instance)
            if zip_bytes is None:
                failed.append(instance.display_name)
                continue
            packages[instance.display_name] = zip_bytes
            succeeded.append(instance.display_name)
        except Exception as exc:
            carb.log_error(
                f"Failed to fetch diagnose package for '{instance.display_name}': {exc}"
            )
            failed.append(instance.display_name)
        finally:
            completed += 1

    await report("Collecting Isaac Sim session log…")
    log_path = get_isaac_sim_log_path()
    log_bytes: bytes | None = None
    if log_path and os.path.isfile(log_path):
        try:
            with open(log_path, "rb") as f:
                log_bytes = f.read()
        except Exception as exc:
            carb.log_warn(f"Could not read Isaac Sim log '{log_path}': {exc}")
    completed += 1

    if not packages and log_bytes is None:
        raise RuntimeError(
            "No diagnose data could be collected. Check that the selected "
            "instances are reachable."
        )

    stage_tree: str | None = None
    if include_stage_tree:
        await report("Extracting stage tree…")
        stage_tree = get_stage_tree()
        completed += 1

    motion_groups: str | None = None
    if include_motion_groups:
        await report("Extracting motion groups…")
        motion_groups = get_motion_groups_json()
        completed += 1

    await report("Writing package…")
    buffer = io.BytesIO()
    used_names: set[str] = set()
    contents: list[str] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as outer:
        for display_name, zip_bytes in packages.items():
            base = _sanitize_filename(display_name)
            entry_name = f"{base}-nova-diagnose-package.zip"
            # Avoid collisions if two instances share a display name.
            counter = 1
            while entry_name in used_names:
                entry_name = f"{base}-{counter}-nova-diagnose-package.zip"
                counter += 1
            used_names.add(entry_name)
            outer.writestr(entry_name, bytes(zip_bytes))
            contents.append(entry_name)

        if log_bytes is not None:
            log_entry = os.path.basename(log_path) or "isaac-sim.log"
            outer.writestr(log_entry, log_bytes)
            contents.append(log_entry)

        if stage_tree is not None:
            outer.writestr("stage_tree.txt", stage_tree)
            contents.append("stage_tree.txt")

        if motion_groups is not None:
            outer.writestr("motion_groups.json", motion_groups)
            contents.append("motion_groups.json")

        # Build the README last so its contents index reflects what was written.
        readme = _build_readme(
            timestamp=timestamp,
            additional_info=additional_info,
            user_agent=_get_user_agent(),
            contents=contents,
        )
        outer.writestr("README.md", readme)

    await _write_package(output_path, buffer.getvalue())

    return DiagnosePackageResult(
        path=output_path,
        succeeded_instances=succeeded,
        failed_instances=failed,
        log_included=log_bytes is not None,
    )
