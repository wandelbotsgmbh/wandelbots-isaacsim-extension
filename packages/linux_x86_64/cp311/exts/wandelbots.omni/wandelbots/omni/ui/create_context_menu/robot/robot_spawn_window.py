from __future__ import annotations

import asyncio
import os
import re
import weakref

import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine
from omni.kit.window.filepicker import FilePickerDialog
from omni.usd import get_watcher
from pxr import Usd
import wandelbots_api_client.v2 as wb_v2

from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import (
    NOVAInstance,
    NOVAMotionGroupData,
)
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.manufacturers import (
    MANUFACTURER_PREFIXES,
    manufacturers_from_model_names,
)
from wandelbots.omni.ui.widgets import PrimPicker, PrimPickerDialogProperties
from .robot_download import download_and_add_robot, make_robot_api_client
from .robot_preview import RobotPreview, _DEFAULT_PREVIEW_COLOR

_LABEL_WIDTH = 140
_WARNING_LABEL_STYLE = {"color": NOVAColor.WARNING_DARK.color}


def _normalize_model_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


class RobotSpawnWindow:
    def __init__(self) -> None:
        self._instances: list[NOVAInstance] = []
        self._models: list[str] = []
        # Display label -> model-name prefix. The static fallback until the
        # instance's model catalog is known, so a manufacturer NOVA adds later
        # shows up without a code change.
        self._manufacturers: dict[str, str] = dict(MANUFACTURER_PREFIXES)
        # Model catalog cache: one getMotionGroupModels call per instance,
        # not one per manufacturer switch.
        self._all_models: list[str] = []
        self._all_models_host: str | None = None

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
        self._locked_manufacturer: str | None = None
        self._locked_prefix: str | None = None
        self._pending_model_name: str | None = None
        self._fetch_error: str | None = None

        self._instance_frame: ui.Frame | None = None
        self._motion_group_frame: ui.Frame | None = None
        self._manufacturer_frame: ui.Frame | None = None
        self._model_frame: ui.Frame | None = None
        self._location_frame: ui.Frame | None = None
        self._location_prim: Usd.Prim | None = None
        self._location_picker: PrimPicker | None = None
        self._location_watcher = None
        self._preview_update_task: asyncio.Task | None = None
        self._color_picker_sub = None
        self._file_picker: FilePickerDialog | None = None
        self._preview = RobotPreview()

        self.window = ui.Window(
            "Select and insert robot model",
            width=460,
            height=328,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )
        self.window.visible = False
        self.window.set_visibility_changed_fn(
            lambda visible, ws=weakref.ref(self): (
                ws()._preview.destroy() if ws() and not visible else None
            )
        )
        self._build_ui()

    def open(self, payload=None) -> None:
        self._selected_instance_idx = 0
        self._selected_motion_group_idx = 0
        self._selected_manufacturer_idx = 0
        self._selected_model_idx = 0
        self._motion_groups = []
        self._models = []
        self._locked_manufacturer = None
        self._locked_prefix = None
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

        selected = omni.usd.get_context().get_selection().get_selected_prim_paths()
        default_path = selected[0] if selected else "/World"
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(default_path) if stage else None
        self._location_prim = prim if (prim and prim.IsValid()) else None

        self._rebuild_instance_row()
        self._rebuild_manufacturer_row()
        self._rebuild_location_row()
        self._subscribe_location_watcher()
        self.window.visible = True
        self.window.focus()
        self._motion_groups_task = run_coroutine(self._fetch_motion_groups())

    def _build_ui(self) -> None:
        with self.window.frame:
            with ui.VStack(spacing=8):
                with ui.HStack():
                    ui.Spacer(width=10)
                    with ui.VStack(spacing=8):
                        ui.Spacer(height=4)
                        ui.Label(
                            "Insert a single robot model from NOVA OS.",
                            word_wrap=True,
                            alignment=ui.Alignment.LEFT,
                            style={"color": NOVAColor.TEXT_SECONDARY.color},
                            height=0,
                        )
                        ui.Spacer(height=4)
                        with ui.HStack(height=24):
                            ui.Label(
                                "Instance",
                                width=_LABEL_WIDTH,
                                tooltip="The connected NOVA OS instance to import from",
                            )
                            self._instance_frame = ui.Frame(
                                tooltip="The connected NOVA OS instance to import from"
                            )

                        with ui.HStack(height=24):
                            ui.Label(
                                "Motion Group",
                                width=_LABEL_WIDTH,
                                tooltip="Optionally select a motion group to pre-fill manufacturer and model",
                            )
                            self._motion_group_frame = ui.Frame(
                                tooltip="Optionally select a motion group to pre-fill manufacturer and model"
                            )

                        with ui.HStack(height=24):
                            ui.Label(
                                "Manufacturer",
                                width=_LABEL_WIDTH,
                                tooltip="The robot manufacturer",
                            )
                            self._manufacturer_frame = ui.Frame(
                                tooltip="The robot manufacturer"
                            )

                        with ui.HStack(height=24):
                            ui.Label(
                                "Model",
                                width=_LABEL_WIDTH,
                                tooltip="The robot model to download and place",
                            )
                            self._model_frame = ui.Frame(
                                tooltip="The robot model to download and place"
                            )

                        with ui.HStack(height=24):
                            ui.Label(
                                "Location",
                                width=_LABEL_WIDTH,
                                tooltip="Stage prim path where the robot will be placed",
                            )
                            self._location_frame = ui.Frame(
                                tooltip="Stage prim path where the robot will be placed"
                            )

                        with ui.HStack(height=24, spacing=8):
                            ui.Label(
                                "Preview color",
                                width=_LABEL_WIDTH,
                                tooltip="Color of the translucent robot preview overlay",
                            )
                            color_picker = ui.ColorWidget(
                                *_DEFAULT_PREVIEW_COLOR[:3],
                                width=24,
                                height=24,
                                style={"border_radius": 4},
                                tooltip="Color of the translucent robot preview overlay",
                            )

                            def _on_preview_color_changed(
                                model: ui.AbstractItemModel,
                                item: ui.AbstractItem,
                                weak_self=weakref.ref(self),
                            ) -> None:
                                ws = weak_self()
                                if not ws:
                                    return
                                rgb = [
                                    model.get_item_value_model(c).get_value_as_float()
                                    for c in model.get_item_children()
                                ]
                                ws._preview.color = rgb[:3] + [
                                    _DEFAULT_PREVIEW_COLOR[3]
                                ]

                            self._color_picker_sub = color_picker.model.add_end_edit_fn(
                                _on_preview_color_changed
                            )

                        ui.Spacer(height=4)

                        with ui.HStack(height=28, spacing=8):
                            ui.Spacer()
                            ui.Button(
                                "Cancel",
                                width=100,
                                tooltip="Close this dialog without importing",
                                clicked_fn=lambda ws=weakref.proxy(self): (
                                    ws._on_cancel_creation()
                                ),
                            )
                            ui.Button(
                                "Confirm",
                                width=100,
                                tooltip="Choose a download folder and import the selected robot model",
                                clicked_fn=lambda ws=weakref.proxy(self): (
                                    ws._on_confirm_creation()
                                ),
                                style={
                                    "Button": {
                                        "background_color": NOVAColor.PRIMARY_MAIN.color,
                                    },
                                    "Button:hovered": {
                                        "background_color": NOVAColor.PRIMARY_LIGHT.color,
                                    },
                                },
                            )
                            ui.Spacer()
                    ui.Spacer(width=10)

    def _rebuild_location_row(self) -> None:
        if self._location_frame is None:
            return
        self._location_picker = None
        self._location_frame.clear()
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return

        def _on_location_picked(prim, ws=weakref.ref(self)) -> None:
            obj = ws()
            if obj is None:
                return
            obj._location_prim = prim
            obj._subscribe_location_watcher()
            obj._update_preview()

        with self._location_frame:
            self._location_picker = PrimPicker(
                stage=stage,
                prim_picked_fn=_on_location_picked,
                prim=self._location_prim,
                dialog_properties=PrimPickerDialogProperties(
                    title="Select spawn location prim",
                ),
            )

    def _subscribe_location_watcher(self) -> None:
        if self._location_watcher is not None:
            try:
                self._location_watcher.unsubscribe()
            except Exception:
                pass
            self._location_watcher = None

        if self._location_prim is None:
            return

        prim_path = self._location_prim.GetPath().pathString

        def _on_location_changed(path=None, ws=weakref.ref(self)) -> None:
            obj = ws()
            if obj is None:
                return
            obj._schedule_preview_update()

        self._location_watcher = get_watcher().subscribe_to_change_info_path(
            prim_path, _on_location_changed
        )

    def _schedule_preview_update(self) -> None:
        if self._preview_update_task is not None:
            self._preview_update_task.cancel()
        self._preview_update_task = run_coroutine(self._delayed_preview_update())

    async def _delayed_preview_update(self) -> None:
        await asyncio.sleep(0.4)
        self._preview.clear()
        self._update_preview()
        self._preview_update_task = None

    def _refresh_instances(self) -> None:
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
            combo = ui.ComboBox(
                idx, *names, tooltip="The connected NOVA OS instance to import from"
            )

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
                # Back to the static fallback until the new instance's model
                # catalog has been fetched (its manufacturers may differ).
                ws._manufacturers = dict(MANUFACTURER_PREFIXES)
                ws._locked_manufacturer = None
                ws._locked_prefix = None
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
            combo = ui.ComboBox(
                idx,
                *items,
                tooltip="Optionally select a motion group to pre-fill manufacturer and model",
            )
            combo.enabled = bool(self._motion_groups)

            def _on_motion_group_changed(
                model: ui.AbstractItemModel, _, ws=weakref.proxy(self)
            ) -> None:
                new_idx = model.get_item_value_model().as_int
                if new_idx == ws._selected_motion_group_idx:
                    return
                ws._selected_motion_group_idx = new_idx

                if new_idx == 0:
                    # "None" restores full manufacturer and model browsing.
                    ws._locked_manufacturer = None
                    ws._locked_prefix = None
                    ws._pending_model_name = None
                    ws._selected_manufacturer_idx = 0
                    ws._rebuild_manufacturer_row()
                    return

                motion_group = ws._motion_groups[new_idx - 1]  # 0 is "None"
                normalized_model_name = _normalize_model_name(
                    motion_group.motion_group_model_name
                )
                match = next(
                    (
                        (manufacturer, prefix)
                        for manufacturer, prefix in ws._manufacturers.items()
                        if normalized_model_name.startswith(prefix)
                    ),
                    None,
                )
                ws._locked_manufacturer = match[0] if match else None
                ws._locked_prefix = match[1] if match else None
                ws._pending_model_name = motion_group.motion_group_model_name
                # The rebuild starts the model fetch, which consumes the
                # pending model name.
                ws._rebuild_manufacturer_row()

            self._motion_group_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_motion_group_changed
            )

    def _start_model_fetch(self) -> None:
        """Show the model row as loading and (re)start the model fetch."""
        self._selected_model_idx = 0
        self._models = []
        if self._models_task is not None:
            self._models_task.cancel()
        self._rebuild_model_row(loading=True)
        self._models_task = run_coroutine(self._fetch_models())

    def _rebuild_manufacturer_row(self, start_model_fetch: bool = True) -> None:
        """Build the manufacturer combo. ``start_model_fetch`` is False when
        _fetch_models rebuilds the row itself, so the row cannot re-enter it.
        """
        if self._manufacturer_frame is None:
            return

        self._manufacturer_combo_sub = None
        self._manufacturer_frame.clear()

        has_instance = bool(self._instances)

        with self._manufacturer_frame:
            if self._locked_manufacturer is not None:
                ui.ComboBox(
                    0,
                    self._locked_manufacturer,
                    tooltip="The robot manufacturer (locked by selected motion group)",
                )
            else:
                labels = list(self._manufacturers)
                selected_index = min(self._selected_manufacturer_idx, len(labels) - 1)
                combo = ui.ComboBox(
                    selected_index, *labels, tooltip="The robot manufacturer"
                )
                combo.enabled = has_instance

                def _on_manufacturer_changed(
                    model: ui.AbstractItemModel, _, ws=weakref.proxy(self)
                ) -> None:
                    ws._selected_manufacturer_idx = model.get_item_value_model().as_int
                    ws._start_model_fetch()

                self._manufacturer_combo_sub = combo.model.subscribe_item_changed_fn(
                    _on_manufacturer_changed
                )

        if has_instance and start_model_fetch:
            self._start_model_fetch()

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
            combo = ui.ComboBox(
                idx, *self._models, tooltip="The robot model to download and place"
            )

            def _on_model_changed(
                model: ui.AbstractItemModel, _, ws=weakref.proxy(self)
            ) -> None:
                ws._selected_model_idx = model.get_item_value_model().as_int
                ws._update_preview()

            self._model_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_model_changed
            )
            self._update_preview()

    @staticmethod
    def _extract_motion_groups(cells) -> list[NOVAMotionGroupData]:
        return [
            mg
            for cell in cells
            for controller in cell.controllers
            for mg in controller.motion_groups
        ]

    async def _fetch_motion_groups(self) -> None:
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

    def _make_api_client(self, instance: NOVAInstance) -> wb_v2.ApiClient | None:
        return make_robot_api_client(instance)

    async def _get_all_models(self, instance: NOVAInstance) -> list[str] | None:
        """The instance's full model catalog, cached per host. Returns None
        (with _fetch_error set) when the instance can't be reached."""
        if self._all_models_host == instance.host and self._all_models:
            return self._all_models

        api_client = self._make_api_client(instance)
        if api_client is None:
            self._fetch_error = f"Cannot connect to '{instance.display_name}'"
            carb.log_warn(
                f"Could not create API client for instance '{instance.display_name}'"
            )
            return None

        self._fetch_error = None
        try:
            models: list[str] = await wb_v2.MotionGroupModelsApi(
                api_client
            ).get_motion_group_models()
        except Exception as exc:
            carb.log_warn(f"Failed to fetch motion group models: {exc}")
            self._fetch_error = f"Instance not reachable: {instance.display_name}"
            return None
        finally:
            try:
                await api_client.close()
            except Exception:
                pass

        self._all_models = models
        self._all_models_host = instance.host
        return models

    def _adopt_catalog_manufacturers(self, all_models: list[str]) -> bool:
        """Replace the manufacturer list with the ones the catalog offers,
        keeping the selection on the same label. True when it changed."""
        manufacturers = manufacturers_from_model_names(all_models)
        if not manufacturers or manufacturers == self._manufacturers:
            return False

        previous_labels = list(self._manufacturers)
        selected_label = (
            previous_labels[
                min(self._selected_manufacturer_idx, len(previous_labels) - 1)
            ]
            if previous_labels
            else None
        )
        self._manufacturers = manufacturers
        labels = list(manufacturers)
        self._selected_manufacturer_idx = (
            labels.index(selected_label) if selected_label in labels else 0
        )
        return True

    def _selected_manufacturer(self) -> tuple[str, str]:
        """Label and model-name prefix of the manufacturer to show models for."""
        if self._locked_manufacturer is not None:
            prefix = self._locked_prefix or self._locked_manufacturer.lower()
            return self._locked_manufacturer, prefix
        labels = list(self._manufacturers)
        label = labels[min(self._selected_manufacturer_idx, len(labels) - 1)]
        return label, self._manufacturers.get(label, label.lower())

    def _resolve_pending_model_name(self) -> str:
        """The catalog entry matching the pending model name, or the raw name
        when the catalog has none."""
        normalized = _normalize_model_name(self._pending_model_name)
        return next(
            (
                model_name
                for model_name in self._models
                if _normalize_model_name(model_name) == normalized
            ),
            self._pending_model_name,
        )

    async def _fetch_models(self) -> None:
        if not self._instances:
            self._rebuild_model_row()
            return

        instance = self._instances[
            min(self._selected_instance_idx, len(self._instances) - 1)
        ]

        all_models = await self._get_all_models(instance)
        if all_models is None:
            self._models = []
            self._rebuild_model_row()
            return

        # The combo must offer what this instance can actually deliver, so it
        # is rebuilt from the catalog. The rebuild must not start another
        # fetch, which would re-enter this method through the UI.
        if self._adopt_catalog_manufacturers(all_models):
            self._rebuild_manufacturer_row(start_model_fetch=False)

        manufacturer, prefix = self._selected_manufacturer()
        self._models = sorted(
            model_name
            for model_name in all_models
            if model_name.lower().startswith(prefix)
        )
        self._selected_model_idx = 0
        if self._pending_model_name:
            self._models = [self._resolve_pending_model_name()]
            self._pending_model_name = None
        carb.log_verbose(
            f"Found {len(self._models)} model(s) for manufacturer "
            f"'{manufacturer}' (prefix '{prefix}')"
        )

        self._rebuild_model_row()

    def _on_cancel_creation(self) -> None:
        if self._preview_update_task is not None:
            self._preview_update_task.cancel()
            self._preview_update_task = None
        if self._location_watcher is not None:
            try:
                self._location_watcher.unsubscribe()
            except Exception:
                pass
            self._location_watcher = None
        self._preview.destroy()
        self.window.visible = False

    def _on_confirm_creation(self) -> None:
        if self._models_task is not None:
            self._models_task.cancel()
        if self._motion_groups_task is not None:
            self._motion_groups_task.cancel()
        if self._preview_update_task is not None:
            self._preview_update_task.cancel()
            self._preview_update_task = None
        if self._location_watcher is not None:
            try:
                self._location_watcher.unsubscribe()
            except Exception:
                pass
            self._location_watcher = None
        self._preview.destroy()
        self.window.visible = False
        self._open_folder_picker()

    def _open_folder_picker(self) -> None:
        model = self._models[min(self._selected_model_idx, len(self._models) - 1)]

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
            ws.window.visible = True
            ws.window.focus()

        stage_url = omni.usd.get_context().get_stage_url() or ""
        if stage_url and "://" in stage_url:
            default_dir = stage_url.rsplit("/", 1)[0] if "/" in stage_url else stage_url
        elif stage_url:
            default_dir = os.path.dirname(stage_url)
        else:
            default_dir = ""

        self._file_picker = FilePickerDialog(
            "Select location for robot download...",
            apply_button_label="Download Here",
            click_apply_handler=_on_apply,
            click_cancel_handler=_on_picker_cancel,
        )
        self._file_picker.set_filename(f"{model}.usd")
        self._file_picker.show(default_dir)

    async def _spawn_robot(self, download_path: str) -> None:
        if not self._instances or not self._models:
            return

        instance = self._instances[
            min(self._selected_instance_idx, len(self._instances) - 1)
        ]
        model = self._models[min(self._selected_model_idx, len(self._models) - 1)]

        await download_and_add_robot(
            instance, model, download_path, location_prim=self._location_prim
        )

    def _get_selected_instance(self) -> NOVAInstance | None:
        if not self._instances:
            return None
        return self._instances[
            min(self._selected_instance_idx, len(self._instances) - 1)
        ]

    def _get_selected_model_name(self) -> str | None:
        if not self._models:
            return None
        return self._models[min(self._selected_model_idx, len(self._models) - 1)]

    def _update_preview(self) -> None:
        prim_path = (
            self._location_prim.GetPath().pathString if self._location_prim else None
        )
        self._preview.request_preview(
            self._get_selected_model_name(),
            self._get_selected_instance(),
            prim_path=prim_path,
        )

    def __del__(self) -> None:
        self._preview.destroy()
        self.window.visible = False
