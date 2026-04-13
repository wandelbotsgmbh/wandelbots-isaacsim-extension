from __future__ import annotations

import os
import re
import weakref
import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine
from omni.kit.window.filepicker import FilePickerDialog
from pxr import Sdf, UsdGeom
import omni.client

from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import (
    NOVAInstance,
    NOVACloudInstance,
    NOVAMotionGroupData,
)
import wandelbots_api_client.v2 as wb_v2

from wandelbots.omni.ui.colors import NOVAColor

MANUFACTURER_PREFIXES: dict[str, str] = {
    "ABB": "abb",
    "FANUC": "fanuc",
    "KUKA": "kuka",
    "Universal Robots": "universalrobots",
    "Yaskawa": "yaskawa",
}

MANUFACTURERS: list[str] = list(MANUFACTURER_PREFIXES)

_LABEL_WIDTH = 140
_WARNING_LABEL_STYLE = {"color": NOVAColor.WARNING_DARK.color}


def _normalize_model_name(name: str) -> str:
    """Strip separators and lowercase for fuzzy model-name comparison."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


class RobotSpawnWindow:
    """
    Floating dialog for selecting a NOVA instance, robot manufacturer and model,
    then downloading and referencing the robot USD into the active stage.
    """

    def __init__(self) -> None:
        self._instances: list[NOVAInstance] = []
        self._models: list[str] = []

        self._selected_instance_idx: int = 0
        self._selected_manufacturer_idx: int = 0
        self._selected_model_idx: int = 0

        self._instance_combo_sub = None
        self._motion_group_combo_sub = None
        self._manufacturer_combo_sub = None
        self._model_combo_sub = None
        self._models_task = None
        self._motion_groups_task = None

        self._motion_groups: list[NOVAMotionGroupData] = []
        self._selected_motion_group_idx: int = 0
        # Set when a motion group is chosen; restricts manufacturer/model to one entry.
        self._locked_manufacturer: str | None = None
        self._pending_model_name: str | None = None
        self._fetch_error: str | None = None  # displayed in the model row on API errors

        self._instance_frame: ui.Frame | None = None
        self._motion_group_frame: ui.Frame | None = None
        self._manufacturer_frame: ui.Frame | None = None
        self._model_frame: ui.Frame | None = None
        self._file_picker: FilePickerDialog | None = None

        self._window = ui.Window(
            "Download Robot from NOVA",
            width=460,
            height=232,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )
        self._window.visible = False
        self._build_ui()

    def __del__(self) -> None:
        if self._window:
            self._window.visible = False
        self._window = None

    def open(self, payload=None) -> None:
        """Show the window and refresh the instance list."""
        self._selected_instance_idx = 0
        self._selected_motion_group_idx = 0
        self._selected_manufacturer_idx = 0
        self._selected_model_idx = 0
        self._motion_groups = []
        self._models = []
        self._locked_manufacturer = None
        self._pending_model_name = None
        self._fetch_error = None
        if self._motion_groups_task is not None:
            self._motion_groups_task.cancel()
            self._motion_groups_task = None
        self._refresh_instances()

        if not self._instances:
            nm.post_notification(
                "No NOVA instances found. Please connect to a NOVA instance first.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        self._rebuild_instance_row()
        self._rebuild_manufacturer_row()
        self._window.visible = True
        self._window.focus()
        self._motion_groups_task = run_coroutine(self._fetch_motion_groups())

    def _build_ui(self) -> None:
        with self._window.frame:
            with ui.VStack(spacing=8):
                ui.Spacer(height=4)

                # Row: Instance
                with ui.HStack(height=24):
                    ui.Label("Instance", width=_LABEL_WIDTH)
                    self._instance_frame = ui.Frame()

                # Row: Motion Group
                with ui.HStack(height=24):
                    ui.Label("Motion Group", width=_LABEL_WIDTH)
                    self._motion_group_frame = ui.Frame()

                # Row: Manufacturer
                with ui.HStack(height=24):
                    ui.Label("Manufacturer", width=_LABEL_WIDTH)
                    self._manufacturer_frame = ui.Frame()

                # Row: Model
                with ui.HStack(height=24):
                    ui.Label("Model", width=_LABEL_WIDTH)
                    self._model_frame = ui.Frame()

                ui.Spacer(height=4)

                # Confirm / Cancel buttons
                with ui.HStack(height=28, spacing=8):
                    ui.Spacer()
                    ui.Button(
                        "Cancel",
                        width=100,
                        clicked_fn=lambda ws=weakref.proxy(self): (
                            ws._on_cancel_creation()
                        ),
                    )
                    ui.Button(
                        "Confirm",
                        width=100,
                        clicked_fn=lambda ws=weakref.proxy(self): (
                            ws._on_confirm_creation()
                        ),
                    )
                    ui.Spacer(width=4)

    def _refresh_instances(self) -> None:
        """Collect the current instance list into ``self._instances``."""
        api = get_instances_api()
        self._instances = [
            inst
            for instances in api.get_cloud_instances().values()
            for inst in instances
        ] + api.get_custom_instances()

    def _rebuild_instance_row(self) -> None:
        if self._instance_frame is None:
            return

        self._instance_combo_sub = None
        self._instance_frame.clear()

        with self._instance_frame:
            names = [inst.display_name for inst in self._instances]
            idx = min(self._selected_instance_idx, len(names) - 1)
            combo = ui.ComboBox(idx, *names)

            def _on_instance_changed(
                model: ui.AbstractItemModel, _, ws=weakref.proxy(self)
            ) -> None:
                new_idx = model.get_item_value_model().as_int
                if new_idx == ws._selected_instance_idx:  # spurious event on creation
                    return
                ws._selected_instance_idx = new_idx
                ws._selected_motion_group_idx = 0
                ws._selected_manufacturer_idx = 0
                ws._selected_model_idx = 0
                ws._motion_groups = []
                ws._models = []
                ws._locked_manufacturer = None
                ws._pending_model_name = None
                ws._fetch_error = None
                if ws._motion_groups_task is not None:
                    ws._motion_groups_task.cancel()
                ws._rebuild_motion_group_row()
                ws._rebuild_manufacturer_row()
                ws._motion_groups_task = run_coroutine(ws._fetch_motion_groups())

            self._instance_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_instance_changed
            )

    def _rebuild_motion_group_row(self) -> None:
        if self._motion_group_frame is None:
            return

        self._motion_group_combo_sub = None
        self._motion_group_frame.clear()

        with self._motion_group_frame:
            items = ["None"] + [mg.name for mg in self._motion_groups]
            idx = min(self._selected_motion_group_idx, len(items) - 1)
            combo = ui.ComboBox(idx, *items)
            combo.enabled = bool(self._motion_groups)

            def _on_motion_group_changed(
                model: ui.AbstractItemModel, _, ws=weakref.proxy(self)
            ) -> None:
                new_idx = model.get_item_value_model().as_int
                if new_idx == ws._selected_motion_group_idx:
                    return
                ws._selected_motion_group_idx = new_idx

                if new_idx == 0:
                    # "None" — restore full manufacturer/model browsing
                    ws._locked_manufacturer = None
                    ws._pending_model_name = None
                    ws._selected_manufacturer_idx = 0
                    ws._rebuild_manufacturer_row()
                    return

                mg = ws._motion_groups[new_idx - 1]  # offset by 1 for "None"

                # Resolve the matching manufacturer name
                norm = _normalize_model_name(mg.motion_group_model_name)
                matched = next(
                    (
                        manufacturer
                        for manufacturer, prefix in MANUFACTURER_PREFIXES.items()
                        if norm.startswith(prefix)
                    ),
                    None,
                )
                ws._locked_manufacturer = matched
                ws._pending_model_name = mg.motion_group_model_name
                # Rebuild triggers _fetch_models which will consume _pending_model_name
                ws._rebuild_manufacturer_row()

            self._motion_group_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_motion_group_changed
            )

    def _rebuild_manufacturer_row(self) -> None:
        if self._manufacturer_frame is None:
            return

        self._manufacturer_combo_sub = None
        self._manufacturer_frame.clear()

        has_instance = bool(self._instances)

        with self._manufacturer_frame:
            if self._locked_manufacturer is not None:
                # Motion group active — show only its manufacturer and kick off model fetch
                ui.ComboBox(0, self._locked_manufacturer)
                if has_instance:
                    self._selected_model_idx = 0
                    self._models = []
                    if self._models_task is not None:
                        self._models_task.cancel()
                    self._rebuild_model_row(loading=True)
                    self._models_task = run_coroutine(self._fetch_models())
                return

            idx = min(self._selected_manufacturer_idx, len(MANUFACTURERS) - 1)
            combo = ui.ComboBox(idx, *MANUFACTURERS)
            combo.enabled = has_instance

            def _on_manufacturer_changed(
                model: ui.AbstractItemModel, _, ws=weakref.proxy(self)
            ) -> None:
                ws._selected_manufacturer_idx = model.get_item_value_model().as_int
                ws._selected_model_idx = 0
                ws._models = []
                if ws._models_task is not None:
                    ws._models_task.cancel()
                ws._rebuild_model_row(loading=True)
                ws._models_task = run_coroutine(ws._fetch_models())

            self._manufacturer_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_manufacturer_changed
            )
            # Manually fire to ensure models are fetched for the initially selected manufacturer.
            if has_instance:
                _on_manufacturer_changed(combo.model, None)

    def _rebuild_model_row(self, loading: bool = False) -> None:
        if self._model_frame is None:
            return

        self._model_combo_sub = None
        self._model_frame.clear()

        with self._model_frame:
            if loading:
                ui.Label("Loading...")
                return

            if self._fetch_error:
                ui.Label(self._fetch_error, style=_WARNING_LABEL_STYLE)
                return

            if not self._models:
                ui.Label(
                    "No models found for this manufacturer", style=_WARNING_LABEL_STYLE
                )
                return

            idx = min(self._selected_model_idx, len(self._models) - 1)
            combo = ui.ComboBox(idx, *self._models)

            def _on_model_changed(
                model: ui.AbstractItemModel, _, ws=weakref.proxy(self)
            ) -> None:
                ws._selected_model_idx = model.get_item_value_model().as_int

            self._model_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_model_changed
            )

    @staticmethod
    def _extract_motion_groups(cells) -> list[NOVAMotionGroupData]:
        """Return a flat list of all motion groups across all cells and controllers."""
        return [
            mg
            for cell in cells
            for controller in cell.controllers
            for mg in controller.motion_groups
        ]

    async def _fetch_motion_groups(self) -> None:
        """Fetch motion groups for the selected instance and populate the Motion Group dropdown."""
        if not self._instances:
            return
        instance = self._instances[
            min(self._selected_instance_idx, len(self._instances) - 1)
        ]
        try:
            cells = await get_instances_api().fetch_cells_for_instance(instance)
            self._motion_groups = self._extract_motion_groups(cells or [])
        except Exception as exc:
            carb.log_warn(f"Could not fetch motion groups: {exc}")
            self._motion_groups = []
        self._rebuild_motion_group_row()

    def _make_api_client(self, instance: NOVAInstance):
        """Return an authenticated API client for *instance*."""
        if isinstance(instance, NOVACloudInstance):
            token = get_instances_api().get_auth_token_from_host(instance.host)
            return instance.create_api_client(token=token)
        return instance.create_api_client()

    async def _fetch_models(self) -> None:
        if not self._instances:
            self._rebuild_model_row()
            return

        instance = self._instances[
            min(self._selected_instance_idx, len(self._instances) - 1)
        ]
        manufacturer = (
            self._locked_manufacturer
            if self._locked_manufacturer is not None
            else MANUFACTURERS[
                min(self._selected_manufacturer_idx, len(MANUFACTURERS) - 1)
            ]
        )
        prefix = MANUFACTURER_PREFIXES.get(manufacturer, manufacturer.lower())

        # Create an API client appropriate for the instance type
        api_client = self._make_api_client(instance)

        if api_client is None:
            self._fetch_error = f"Cannot connect to '{instance.display_name}'"
            carb.log_warn(
                f"Could not create API client for instance '{instance.display_name}'"
            )
            self._models = []
            self._rebuild_model_row()
            return

        self._fetch_error = None
        try:
            all_models: list[str] = await wb_v2.MotionGroupModelsApi(
                api_client
            ).get_motion_group_models()
            filtered = [m for m in all_models if m.lower().startswith(prefix)]
            self._models = sorted(filtered)
            # If a motion group is selected, keep only the matching model
            if self._pending_model_name:
                norm_pending = _normalize_model_name(self._pending_model_name)
                match = next(
                    (
                        m
                        for m in self._models
                        if _normalize_model_name(m) == norm_pending
                    ),
                    self._pending_model_name,  # fall back to the raw name if not found
                )
                self._models = [match]
                self._selected_model_idx = 0
                self._pending_model_name = None
            else:
                self._selected_model_idx = 0
            carb.log_verbose(
                f"Found {len(self._models)} model(s) for manufacturer '{manufacturer}' (prefix '{prefix}')"
            )
        except Exception as exc:
            carb.log_warn(f"Failed to fetch motion group models: {exc}")
            self._fetch_error = f"Instance not reachable: {instance.display_name}"
            self._models = []
        finally:
            try:
                await api_client.close()
            except Exception:
                pass

        self._rebuild_model_row()

    def _on_cancel_creation(self) -> None:
        self._window.visible = False

    def _on_confirm_creation(self) -> None:
        if self._models_task is not None:
            self._models_task.cancel()
        if self._motion_groups_task is not None:
            self._motion_groups_task.cancel()
        self._window.visible = False
        self._open_folder_picker()

    def _open_folder_picker(self) -> None:
        def _on_apply(filename: str, path: str, ws=weakref.proxy(self)) -> None:
            ws._file_picker.hide()
            if filename and "://" in path:
                download_path = path.rstrip("/") + "/" + filename
            elif filename:
                download_path = os.path.join(path, filename)
            else:
                download_path = path
            run_coroutine(ws._spawn_robot(download_path))

        def _on_picker_cancel(filename: str, path: str, ws=weakref.proxy(self)) -> None:
            ws._file_picker.hide()
            ws._window.visible = True
            ws._window.focus()

        stage_url = omni.usd.get_context().get_stage_url() or ""
        if stage_url and "://" in stage_url:
            default_dir = stage_url.rsplit("/", 1)[0] if "/" in stage_url else stage_url
        elif stage_url:
            default_dir = os.path.dirname(stage_url)
        else:
            default_dir = ""

        self._file_picker = FilePickerDialog(
            "Download Robot Model To...",
            apply_button_label="Download Here",
            click_apply_handler=_on_apply,
            click_cancel_handler=_on_picker_cancel,
        )
        self._file_picker.show(default_dir)

    async def _spawn_robot(self, download_path: str) -> None:
        if not self._instances or not self._models:
            return

        instance = self._instances[
            min(self._selected_instance_idx, len(self._instances) - 1)
        ]
        manufacturer = (
            self._locked_manufacturer
            if self._locked_manufacturer is not None
            else MANUFACTURERS[
                min(self._selected_manufacturer_idx, len(MANUFACTURERS) - 1)
            ]
        )
        model = self._models[min(self._selected_model_idx, len(self._models) - 1)]

        carb.log_info(
            f"Spawning robot - "
            f"instance: '{instance.display_name}', "
            f"manufacturer: '{manufacturer}', "
            f"model: '{model}', "
            f"download path: '{download_path}'"
        )

        # Create API client to fetch the kinematic model
        api_client = self._make_api_client(instance)

        if api_client is None:
            carb.log_error("Could not create API client for spawning robot")
            return

        try:
            carb.log_info(f"Downloading USD for '{model}'...")
            usd_bytes: bytearray = await wb_v2.MotionGroupModelsApi(
                api_client
            ).get_motion_group_usd_model(motion_group_model=model)

            is_nucleus = "://" in download_path

            if is_nucleus:
                usd_file_path = (
                    download_path
                    if download_path.lower().endswith(".usd")
                    else download_path.rstrip("/") + "/" + model + ".usd"
                )
            elif os.path.isdir(download_path):
                usd_file_path = os.path.join(download_path, f"{model}.usd")
            elif not download_path.lower().endswith(".usd"):
                usd_file_path = download_path + ".usd"
            else:
                usd_file_path = download_path

            if is_nucleus:
                write_result = await omni.client.write_file_async(
                    usd_file_path, bytes(usd_bytes)
                )
                if write_result != omni.client.Result.OK:
                    raise RuntimeError(
                        f"omni.client.write_file_async failed with: {write_result}"
                    )
            else:
                parent_dir = os.path.dirname(usd_file_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with open(usd_file_path, "wb") as f:
                    f.write(usd_bytes)

            carb.log_info(f"Saved USD to '{usd_file_path}'")

            # Add as a reference on the active stage under the selected prim
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                carb.log_error("No active stage found")
                return

            selected = omni.usd.get_context().get_selection().get_selected_prim_paths()
            parent_path = selected[0] if selected else "/World"

            # USD prim names must be valid identifiers; replace illegal chars
            safe_name = (
                model
                if Sdf.Path.IsValidIdentifier(model)
                else model.replace("-", "_").replace(" ", "_")
            )
            robot_prim_path = Sdf.Path(parent_path).AppendChild(safe_name)

            xform = UsdGeom.Xform.Define(stage, robot_prim_path)
            xform.GetPrim().GetReferences().AddReference(usd_file_path)
            carb.log_info(
                f"Added reference at '{robot_prim_path}' -> '{usd_file_path}'"
            )

        except Exception as exc:
            carb.log_error(f"Failed to download / import model '{model}': {exc}")
        finally:
            try:
                await api_client.close()
            except Exception:
                pass
