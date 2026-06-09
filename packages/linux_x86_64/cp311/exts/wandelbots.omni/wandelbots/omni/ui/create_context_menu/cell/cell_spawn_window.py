from __future__ import annotations

import asyncio
import os
import re
import weakref

import carb
import omni.client
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine
from omni.kit.window.filepicker import FilePickerDialog
from omni.usd import get_watcher
from pxr import Gf, Sdf, Usd, UsdGeom

import wandelbots_api_client.v2 as wb_v2

from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import (
    NOVACellData,
    NOVACloudInstance,
    NOVAInstance,
    NOVAMotionGroupData,
)
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.widgets import PrimPicker, PrimPickerDialogProperties
from wandelbots.omni.utils.math import rotvec_to_quat
from wandelbots.omni.utils.prims import PrimUtils
import wandelbots.usd as wb_schema  # type: ignore
from .cell_preview import CellPreview, _DEFAULT_PREVIEW_COLOR

_LABEL_WIDTH = 140
_WARNING_LABEL_STYLE = {"color": NOVAColor.WARNING_DARK.color}


class CellSpawnWindow:
    def __init__(self) -> None:
        self._instances: list[NOVAInstance] = []
        self._cells: list[NOVACellData] = []

        self._selected_instance_idx: int = 0
        self._selected_cell_idx: int = 0

        self._instance_combo_sub = None
        self._cell_combo_sub = None
        self._cells_task = None
        self._color_picker_sub = None

        self._instance_frame: ui.Frame | None = None
        self._cell_frame: ui.Frame | None = None
        self._location_frame: ui.Frame | None = None
        self._location_prim: Usd.Prim | None = None
        self._location_picker: PrimPicker | None = None
        self._location_watcher = None
        self._preview_update_task: asyncio.Task | None = None
        self._file_picker: FilePickerDialog | None = None
        self._preview = CellPreview()

        self.window = ui.Window(
            "Insert all robot models matching a NOVA cell",
            width=460,
            height=275,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR,
        )
        self.window.visible = False
        self.window.set_visibility_changed_fn(
            lambda visible, ws=weakref.ref(self): (
                ws()._preview.destroy() if ws() and not visible else None
            )
        )
        self._build_ui()

    def open(self, payload: dict | None = None) -> None:
        self._selected_instance_idx = 0
        self._selected_cell_idx = 0
        self._cells = []

        if self._cells_task is not None:
            self._cells_task.cancel()
            self._cells_task = None

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
        self._rebuild_cell_row(loading=True)
        self._rebuild_location_row()
        self._subscribe_location_watcher()
        self.window.visible = True
        self.window.focus()
        self._cells_task = run_coroutine(self._fetch_cells())

    def _build_ui(self) -> None:
        with self.window.frame:
            with ui.VStack(spacing=8):
                with ui.HStack():
                    ui.Spacer(width=10)
                    with ui.VStack(spacing=8):
                        ui.Spacer(height=4)
                        ui.Label(
                            "Insert all robots including mounting and tcp configurations from your selected NOVA OS cell.",
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
                                "Cell",
                                width=_LABEL_WIDTH,
                                tooltip="The cell whose robots will be imported",
                            )
                            self._cell_frame = ui.Frame(
                                tooltip="The cell whose robots will be imported"
                            )

                        with ui.HStack(height=24):
                            ui.Label(
                                "Location",
                                width=_LABEL_WIDTH,
                                tooltip="Stage prim path where robots will be placed",
                            )
                            self._location_frame = ui.Frame(
                                tooltip="Stage prim path where robots will be placed"
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
                                tooltip="Choose a download folder and import all robots from the selected cell",
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

    def _refresh_instances(self) -> None:
        api = get_instances_api()
        self._instances = [
            inst
            for instances in api.get_cloud_instances().values()
            for inst in instances
        ] + api.get_custom_instances()

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
                if new_idx == ws._selected_instance_idx:
                    return
                ws._selected_instance_idx = new_idx
                ws._selected_cell_idx = 0
                ws._cells = []
                if ws._cells_task is not None:
                    ws._cells_task.cancel()
                ws._rebuild_cell_row(loading=True)
                ws._cells_task = run_coroutine(ws._fetch_cells())

            self._instance_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_instance_changed
            )

    def _rebuild_cell_row(self, loading: bool = False) -> None:
        if self._cell_frame is None:
            return

        self._cell_combo_sub = None
        self._cell_frame.clear()

        with self._cell_frame:
            if loading:
                ui.Label("Loading...")
                return

            if not self._cells:
                ui.Label("No cells found for this instance", style=_WARNING_LABEL_STYLE)
                return

            idx = min(self._selected_cell_idx, len(self._cells) - 1)
            combo = ui.ComboBox(
                idx,
                *[c.name for c in self._cells],
                tooltip="The cell whose robots will be imported",
            )

            def _on_cell_changed(
                model: ui.AbstractItemModel, _, ws=weakref.proxy(self)
            ) -> None:
                ws._selected_cell_idx = model.get_item_value_model().as_int
                ws._update_preview()

            self._cell_combo_sub = combo.model.subscribe_item_changed_fn(
                _on_cell_changed
            )
            self._update_preview()

    async def _fetch_cells(self) -> None:
        if not self._instances:
            self._rebuild_cell_row()
            return
        instance = self._instances[
            min(self._selected_instance_idx, len(self._instances) - 1)
        ]
        try:
            cells = await get_instances_api().fetch_cells_for_instance(instance)
            self._cells = cells or []
        except Exception as exc:
            carb.log_warn(f"Could not fetch cells: {exc}")
            self._cells = []
        self._cells_task = None
        self._rebuild_cell_row()

    def _make_api_client(self, instance: NOVAInstance):
        if isinstance(instance, NOVACloudInstance):
            token = get_instances_api().get_auth_token_from_host(instance.host)
            return instance.create_api_client(token=token)
        return instance.create_api_client()

    def _update_preview(self) -> None:
        if not self._instances or not self._cells:
            self._preview.clear()
            return
        instance = self._instances[
            min(self._selected_instance_idx, len(self._instances) - 1)
        ]
        cell = self._cells[min(self._selected_cell_idx, len(self._cells) - 1)]
        prim_path = (
            self._location_prim.GetPath().pathString if self._location_prim else None
        )
        self._preview.request_preview(cell, instance, prim_path)

    def _on_cancel_creation(self) -> None:
        if self._cells_task is not None:
            self._cells_task.cancel()
            self._cells_task = None
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
        if not self._instances or not self._cells:
            return
        if self._cells_task is not None:
            self._cells_task.cancel()
            self._cells_task = None
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
        cell_name = self._cells[min(self._selected_cell_idx, len(self._cells) - 1)].name

        def _on_apply(filename: str, path: str, ws=weakref.proxy(self)) -> None:
            ws._file_picker.hide()
            if filename and "://" in path:
                download_path = path.rstrip("/") + "/" + filename
            elif filename:
                download_path = os.path.join(path, filename)
            else:
                download_path = path
            run_coroutine(ws._spawn_cell(download_path))

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
        self._file_picker.set_filename(cell_name)
        self._file_picker.show(default_dir)

    async def _spawn_cell(self, download_path: str) -> None:
        if not self._instances or not self._cells:
            return

        instance = self._instances[
            min(self._selected_instance_idx, len(self._instances) - 1)
        ]
        cell = self._cells[min(self._selected_cell_idx, len(self._cells) - 1)]

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            carb.log_error("CellSpawnWindow: no active stage")
            return

        parent_path = (
            self._location_prim.GetPath().pathString
            if self._location_prim
            else "/World"
        )
        stage_units = UsdGeom.GetStageMetersPerUnit(stage)
        is_nucleus = "://" in download_path

        safe_cell_name = (
            cell.name
            if Sdf.Path.IsValidIdentifier(cell.name)
            else cell.name.replace("-", "_").replace(" ", "_")
        )
        cell_prim_path = Sdf.Path(parent_path).AppendChild(safe_cell_name)
        UsdGeom.Xform.Define(stage, cell_prim_path)
        parent_path = str(cell_prim_path)

        api_client = self._make_api_client(instance)
        if api_client is None:
            carb.log_error(f"Could not create API client for '{instance.display_name}'")
            return

        try:
            motion_group_api = wb_v2.MotionGroupApi(api_client)
            models_api = wb_v2.MotionGroupModelsApi(api_client)

            for controller in cell.controllers:
                for mg in controller.motion_groups:
                    await self._download_and_place_robot(
                        motion_group_api=motion_group_api,
                        models_api=models_api,
                        cell_name=cell.name,
                        controller_name=controller.name,
                        mg=mg,
                        download_path=download_path,
                        parent_path=parent_path,
                        stage=stage,
                        stage_units=stage_units,
                        is_nucleus=is_nucleus,
                    )
        except Exception as exc:
            carb.log_error(f"Error spawning cell '{cell.name}': {exc}")
        finally:
            try:
                await api_client.close()
            except Exception:
                pass

    async def _download_and_place_robot(
        self,
        motion_group_api: wb_v2.MotionGroupApi,
        models_api: wb_v2.MotionGroupModelsApi,
        cell_name: str,
        controller_name: str,
        mg: NOVAMotionGroupData,
        download_path: str,
        parent_path: str,
        stage: Usd.Stage,
        stage_units: float,
        is_nucleus: bool,
    ) -> None:
        model_name = mg.motion_group_model_name.replace(" ", "_")
        safe_name = (
            model_name
            if Sdf.Path.IsValidIdentifier(model_name)
            else model_name.replace("-", "_")
        )

        mounting: wb_v2.Pose | None = None
        tcps: dict[str, wb_v2.TcpOffset] = {}
        try:
            mg_desc: wb_v2.MotionGroupDescription = (
                await motion_group_api.get_motion_group_description(
                    cell=cell_name,
                    controller=controller_name,
                    motion_group=mg.name,
                )
            )
            mounting = mg_desc.mounting
            tcps = mg_desc.tcps or {}
        except Exception as exc:
            carb.log_warn(f"Could not fetch description for '{mg.name}': {exc}")

        try:
            usd_bytes: bytearray = await models_api.get_motion_group_usd_model(
                motion_group_model=model_name
            )
        except Exception as exc:
            carb.log_error(f"Failed to download USD for '{model_name}': {exc}")
            return

        final_name = safe_name
        if stage.GetPrimAtPath(Sdf.Path(parent_path).AppendChild(safe_name)).IsValid():
            counter = 1
            while True:
                candidate = f"{safe_name}_{counter:02d}"
                if not stage.GetPrimAtPath(
                    Sdf.Path(parent_path).AppendChild(candidate)
                ).IsValid():
                    final_name = candidate
                    break
                counter += 1

        usd_file_path = (
            download_path.rstrip("/") + "/" + final_name + ".usd"
            if is_nucleus
            else os.path.join(download_path, f"{final_name}.usd")
        )
        try:
            if is_nucleus:
                result = await omni.client.write_file_async(
                    usd_file_path, bytes(usd_bytes)
                )
                if result != omni.client.Result.OK:
                    raise RuntimeError(
                        f"omni.client.write_file_async returned {result}"
                    )
            else:
                parent_dir = os.path.dirname(usd_file_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with open(usd_file_path, "wb") as fh:
                    fh.write(usd_bytes)
            carb.log_info(f"Saved '{usd_file_path}'")
        except Exception as exc:
            carb.log_error(f"Failed to write '{usd_file_path}': {exc}")
            return

        robot_prim_path = Sdf.Path(parent_path).AppendChild(final_name)
        xform = UsdGeom.Xform.Define(stage, robot_prim_path)
        xform.GetPrim().GetPayloads().AddPayload(usd_file_path)

        if mounting is not None:
            self._apply_mounting(xform, mounting, stage_units)

        carb.log_info(f"Placed '{robot_prim_path}' (model '{model_name}')")

        if tcps:
            self._place_tcps(
                stage=stage,
                robot_prim_path=str(robot_prim_path),
                tcps=tcps,
                stage_units=stage_units,
            )

    def _apply_mounting(
        self,
        xform: UsdGeom.Xform,
        mounting: wb_v2.Pose,
        stage_units: float,
    ) -> None:
        unit_factor = 0.001 / stage_units

        ordered_ops = xform.GetOrderedXformOps()
        translate_op = next(
            (
                op
                for op in ordered_ops
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate
            ),
            None,
        )
        orient_op = next(
            (op for op in ordered_ops if op.GetOpType() == UsdGeom.XformOp.TypeOrient),
            None,
        )
        if translate_op is None:
            translate_op = xform.AddTranslateOp(
                precision=UsdGeom.XformOp.PrecisionDouble
            )
        if orient_op is None:
            orient_op = xform.AddOrientOp(precision=UsdGeom.XformOp.PrecisionDouble)

        translate_op.Set(
            Gf.Vec3d(
                mounting.position[0] * unit_factor,
                mounting.position[1] * unit_factor,
                mounting.position[2] * unit_factor,
            )
        )
        quat_xyzw = rotvec_to_quat(
            mounting.orientation[0], mounting.orientation[1], mounting.orientation[2]
        )
        orient_op.Set(Gf.Quatd(quat_xyzw[3], quat_xyzw[0], quat_xyzw[1], quat_xyzw[2]))

    def _find_descendant_by_name(
        self, stage: Usd.Stage, root_path: str, name: str
    ) -> str | None:
        root_prim = stage.GetPrimAtPath(root_path)
        if not root_prim.IsValid():
            return None
        for prim in Usd.PrimRange(root_prim):
            if prim.GetName() == name:
                return str(prim.GetPath())
        return None

    def _place_tcps(
        self,
        stage: Usd.Stage,
        robot_prim_path: str,
        tcps: dict[str, wb_v2.TcpOffset],
        stage_units: float,
    ) -> None:
        unit_factor = 0.001 / stage_units

        link_6_path = self._find_descendant_by_name(stage, robot_prim_path, "link_6")
        if link_6_path is None:
            carb.log_warn(
                f"'link_6' not found under '{robot_prim_path}'; skipping TCP placement"
            )
            return

        tcp_flange_path = self._find_descendant_by_name(
            stage, link_6_path, "tcp_flange"
        )
        if tcp_flange_path is None:
            carb.log_warn(
                f"'tcp_flange' not found under '{link_6_path}'; skipping TCP placement"
            )
            return

        try:
            flange_pose = PrimUtils.get_relative_prim_pose(
                robot_prim_path, tcp_flange_path
            )
        except Exception as exc:
            carb.log_warn(
                f"Could not compute tcp_flange pose: {exc}; skipping TCP placement"
            )
            return

        f_pos = flange_pose.pose[:3]
        f_ori = flange_pose.pose[3:]

        tool_path = Sdf.Path(robot_prim_path).AppendChild("tool")
        tool_xform = UsdGeom.Xform.Define(stage, tool_path)
        tool_xform.AddTranslateOp(precision=UsdGeom.XformOp.PrecisionDouble).Set(
            Gf.Vec3d(
                f_pos[0] * unit_factor,
                f_pos[1] * unit_factor,
                f_pos[2] * unit_factor,
            )
        )
        f_quat = rotvec_to_quat(f_ori[0], f_ori[1], f_ori[2])
        tool_xform.AddOrientOp(precision=UsdGeom.XformOp.PrecisionDouble).Set(
            Gf.Quatd(f_quat[3], f_quat[0], f_quat[1], f_quat[2])
        )

        for tcp_id, tcp_offset in tcps.items():
            if tcp_id.lower() == "flange":
                continue
            safe_name = re.sub(r"[^0-9A-Za-z]+", "_", tcp_id)
            tcp_prim_path = Sdf.Path(str(tool_path)).AppendChild(safe_name)
            tcp_xform = wb_schema.ToolCenterPoint.Define(stage, tcp_prim_path)

            tcp_pos = tcp_offset.pose.position
            tcp_ori = tcp_offset.pose.orientation

            tcp_xform.AddTranslateOp(precision=UsdGeom.XformOp.PrecisionDouble).Set(
                Gf.Vec3d(
                    tcp_pos[0] * unit_factor,
                    tcp_pos[1] * unit_factor,
                    tcp_pos[2] * unit_factor,
                )
            )
            tcp_quat = rotvec_to_quat(tcp_ori[0], tcp_ori[1], tcp_ori[2])
            tcp_xform.AddOrientOp(precision=UsdGeom.XformOp.PrecisionDouble).Set(
                Gf.Quatd(tcp_quat[3], tcp_quat[0], tcp_quat[1], tcp_quat[2])
            )
            carb.log_info(f"Placed TCP '{tcp_id}' at '{tcp_prim_path}'")

    def __del__(self) -> None:
        self._preview.destroy()
        self.window.visible = False
