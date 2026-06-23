"""Modal dialog to import a stored trajectory plan onto a scene motion group.

Opens unconditionally with a fixed layout: the instance, cell, stored-plan and
scene-motion-group comboboxes are always present (each with a "Select …"
placeholder, disabled when there is nothing to choose) and are filled in place as
data loads, so the window does not jump around. The user picks the NOVA instance
and cell to import from (no motion group needs to be connected first), then a
stored plan (``trajectory-plan/*``), maps the plan's motion group onto one in the
current scene, and — once the model / joint count verify — imports it, relinking
the plan's poses to the existing prims under the chosen motion group.

When some of the plan's pose prims are not present in the scene, they cannot be
relinked; all poses are listed up front in a "Poses to import" section.
"""

from __future__ import annotations

import weakref
from typing import Callable

import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine

import wandelbots_api_client.v2 as wb_v2

from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVACloudInstance, NOVAInstance
from wandelbots.omni.manipulators import (
    get_motion_group_configuration_from_prim,
    get_scene_motion_group_prim_paths,
)
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.utils import defer_call
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.utils.api import get_api_client_from_config
from wandelbots.omni.ui.tool.trajectory_planner.nova_skill_store import (
    config_from_exported_skill,
    exported_skill_joint_count,
    exported_skill_model,
    extract_pose_rows,
    list_nova_plan_names,
    load_nova_plan,
)
from wandelbots.omni.ui.tool.trajectory_planner.trajectory_planner_store import (
    TrajectoryPlannerConfig,
)

_LABEL_WIDTH = 150
# The window auto-resizes its height to the content; a fixed content width keeps
# it from stretching to fit the (word-wrapped) description on one line.
_CONTENT_WIDTH = 430
_WINDOW_WIDTH = _CONTENT_WIDTH + 30


def _spawn(coro):
    """Run a coroutine and log any exception (otherwise it is swallowed)."""
    task = run_coroutine(coro)

    def _done(fut):
        try:
            exc = fut.exception()
        except Exception:
            return
        if exc is not None:
            import traceback

            carb.log_error(
                "Import dialog task failed: " + "".join(traceback.format_exception(exc))
            )

    task.add_done_callback(_done)
    return task


async def _close_client(api_client) -> None:
    if api_client is None:
        return
    try:
        await api_client.close()
    except Exception:
        pass


def _tcp_text(pose: list[float] | None) -> str:
    if not pose or len(pose) < 6:
        return "-"
    return (
        f"({pose[0]:.1f}, {pose[1]:.1f}, {pose[2]:.1f}, "
        f"{pose[3]:.3f}, {pose[4]:.3f}, {pose[5]:.3f})"
    )


def _joints_text(joint: list[float] | None) -> str:
    if not joint:
        return "-"
    return "[" + ", ".join(f"{v:.2f}" for v in joint) + "]"


class ImportSkillDialog:
    def __init__(
        self,
        on_import: Callable[[TrajectoryPlannerConfig], None],
    ) -> None:
        self._on_import = on_import

        self._window: ui.Window | None = None

        self._instances: list[NOVAInstance] = []
        self._cells: list[str] = []
        self._plan_names: list[str] = []
        self._scene_mg_paths: list[str] = []

        self._selected_instance_idx: int | None = None
        self._selected_cell_idx: int | None = None
        self._selected_plan: str | None = None
        self._selected_scene_mg: str | None = None

        self._skill_dict: dict | None = None
        self._config: TrajectoryPlannerConfig | None = None
        self._stored_model: str | None = None
        self._stored_joints: int | None = None
        self._stored_mg_path: str | None = None
        self._stored_scene_path: str | None = None
        self._verify_state = "pending"

        self._hint_host: str | None = None
        self._hint_cell: str | None = None

        self._instance_frame: ui.Frame | None = None
        self._cell_frame: ui.Frame | None = None
        self._plan_frame: ui.Frame | None = None
        self._scene_frame: ui.Frame | None = None
        self._stored_model_label: ui.Label | None = None
        self._stored_scene_label: ui.Label | None = None
        self._verify_label: ui.Label | None = None
        self._warning_frame: ui.Frame | None = None
        self._warning_overlay: ui.Frame | None = None
        self._import_button: ui.Button | None = None
        self._combo_subs: list = []
        self._cells_task = None
        self._plans_task = None

    # -- lifecycle ---------------------------------------------------------

    def show(self) -> None:
        defer_call(self._build_window)

    def _close(self) -> None:
        self._combo_subs.clear()
        if self._window is not None:
            self._window.visible = False
            self._window = None

    # -- selection helpers -------------------------------------------------

    def _selected_instance(self) -> NOVAInstance | None:
        if self._selected_instance_idx is None:
            return None
        if 0 <= self._selected_instance_idx < len(self._instances):
            return self._instances[self._selected_instance_idx]
        return None

    def _selected_cell(self) -> str | None:
        if self._selected_cell_idx is None:
            return None
        if 0 <= self._selected_cell_idx < len(self._cells):
            return self._cells[self._selected_cell_idx]
        return None

    def _make_api_client(self):
        instance = self._selected_instance()
        if instance is None:
            return None
        if isinstance(instance, NOVACloudInstance):
            token = get_instances_api().get_auth_token_from_host(instance.host)
            return instance.create_api_client(token=token)
        return instance.create_api_client()

    def _compute_scene_hint(self) -> None:
        try:
            stage = omni.usd.get_context().get_stage()
            if not stage:
                return
            for path in get_scene_motion_group_prim_paths(
                include_prims_without_api=False
            ):
                prim = stage.GetPrimAtPath(path)
                mg = (
                    get_motion_group_configuration_from_prim(prim)
                    if prim and prim.IsValid()
                    else None
                )
                if not mg:
                    continue
                msc = mg.motion_stream_configuration
                self._hint_host = getattr(msc, "host", None)
                self._hint_cell = getattr(msc, "cell", None)
                return
        except Exception as exc:
            carb.log_verbose(f"Import: could not read scene motion group hint: {exc}")

    # -- combobox helper ---------------------------------------------------

    def _fill_combo(
        self,
        frame: ui.Frame | None,
        placeholder: str,
        items: list[str],
        handler: Callable[[int], None],
        selected: int = 0,
    ):
        """(Re)render a combobox with a leading placeholder inside ``frame``.

        Disabled when there are no real items. Index 0 is the placeholder; real
        items are ``index + 1`` (so the row height never changes).
        """
        if frame is None:
            return None
        frame.clear()
        with frame:
            combo = ui.ComboBox(selected, placeholder, *items)
            combo.enabled = bool(items)
            self._combo_subs.append(
                combo.model.subscribe_item_changed_fn(
                    lambda m, _, ws=weakref.ref(self): (
                        handler(m.get_item_value_model().get_value_as_int())
                        if ws()
                        else None
                    )
                )
            )
        return combo

    # -- UI ----------------------------------------------------------------

    def _build_window(self) -> None:
        self._refresh_instances()
        self._compute_scene_hint()
        self._scene_mg_paths = get_scene_motion_group_prim_paths(
            include_prims_without_api=False
        )
        self._window = ui.Window(
            "Import trajectory plan",
            width=_WINDOW_WIDTH,
            height=400,
            auto_resize=True,
            flags=ui.WINDOW_FLAGS_NO_SCROLLBAR | ui.WINDOW_FLAGS_MODAL,
        )
        self._window.visible = True
        with self._window.frame:
            with ui.VStack(spacing=8, height=0):
                with ui.HStack(height=0):
                    ui.Spacer(width=12)
                    with ui.VStack(spacing=8, height=0):
                        ui.Spacer(height=4)
                        ui.Label(
                            "Import a stored trajectory plan and map it onto a "
                            "motion group in the current scene.",
                            word_wrap=True,
                            alignment=ui.Alignment.LEFT,
                            style={"color": NOVAColor.TEXT_SECONDARY.color},
                            width=_CONTENT_WIDTH,
                            height=0,
                        )
                        ui.Spacer(height=4)
                        self._instance_frame = self._labeled_row(
                            "Instance", "The NOVA instance to import from"
                        )
                        self._cell_frame = self._labeled_row(
                            "Cell", "The cell the plan was stored in"
                        )
                        self._plan_frame = self._labeled_row(
                            "Trajectory Plan", "The stored trajectory plan to import"
                        )
                        with ui.HStack(height=24):
                            ui.Label(
                                "Model Name",
                                width=_LABEL_WIDTH,
                                tooltip="Model the plan was created with",
                            )
                            self._stored_model_label = ui.Label(
                                "-", style={"color": NOVAColor.TEXT_SECONDARY.color}
                            )
                        with ui.HStack(height=24):
                            ui.Label(
                                "Scene",
                                width=_LABEL_WIDTH,
                                tooltip="USD stage the plan was exported from",
                            )
                            self._stored_scene_label = ui.Label(
                                "-",
                                style={"color": NOVAColor.TEXT_SECONDARY.color},
                                elided_text=True,
                            )
                        self._scene_frame = self._labeled_row(
                            "Scene motion group",
                            "Motion group in the current scene to import onto",
                        )
                        self._warning_frame = ui.Frame(height=0)
                        ui.Spacer(height=4)
                        with ui.HStack(height=28, spacing=8):
                            ui.Spacer()
                            ui.Button(
                                "Cancel",
                                width=100,
                                tooltip="Close without importing",
                                clicked_fn=lambda ws=weakref.ref(self): (
                                    ws()._close() if ws() else None
                                ),
                            )
                            self._import_button = ui.Button(
                                "Import",
                                width=100,
                                tooltip="Import the selected plan onto the chosen "
                                "motion group",
                                clicked_fn=lambda ws=weakref.ref(self): (
                                    ws()._do_import() if ws() else None
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
                            ui.Spacer(width=4)
                        ui.Spacer(height=20)
                    ui.Spacer(width=12)
        self._set_import_enabled(False)

        inst_idx = self._default_instance_combo_idx()
        self._fill_combo(
            self._instance_frame,
            "Select instance",
            [i.display_name for i in self._instances],
            self._on_instance_chosen,
            selected=inst_idx,
        )
        self._fill_combo(self._cell_frame, "Select cell", [], self._on_cell_chosen)
        self._fill_combo(self._plan_frame, "Select plan", [], self._on_plan_chosen)
        # The scene motion group is associated automatically (matched, or the only
        # one present), shown as a read-only label — not chosen from a dropdown.
        self._render_scene_label(None)
        if inst_idx > 0:
            self._on_instance_chosen(inst_idx)

    def _labeled_row(self, label: str, tooltip: str) -> ui.Frame:
        with ui.HStack(height=24):
            ui.Label(label, width=_LABEL_WIDTH, tooltip=tooltip)
            frame = ui.Frame(tooltip=tooltip)
        return frame

    # -- instance ----------------------------------------------------------

    def _refresh_instances(self) -> None:
        api = get_instances_api()
        self._instances = [
            inst
            for instances in api.get_cloud_instances().values()
            for inst in instances
        ] + api.get_custom_instances()

    def _default_instance_combo_idx(self) -> int:
        if not self._instances:
            return 0
        if self._hint_host:
            for i, inst in enumerate(self._instances):
                if getattr(inst, "host", None) == self._hint_host:
                    return i + 1
        if len(self._instances) == 1:
            return 1
        return 0

    def _on_instance_chosen(self, combo_idx: int) -> None:
        self._selected_cell_idx = None
        self._selected_plan = None
        self._cells = []
        self._plan_names = []
        self._reset_plan_state()
        self._fill_combo(self._cell_frame, "Select cell", [], self._on_cell_chosen)
        self._fill_combo(self._plan_frame, "Select plan", [], self._on_plan_chosen)
        if combo_idx <= 0 or combo_idx > len(self._instances):
            self._selected_instance_idx = None
            return
        self._selected_instance_idx = combo_idx - 1
        if self._cells_task is not None:
            self._cells_task.cancel()
        self._cells_task = _spawn(self._fetch_cells())

    async def _fetch_cells(self) -> None:
        instance = self._selected_instance()
        if instance is None:
            return
        try:
            cells = await get_instances_api().fetch_cells_for_instance(instance)
            self._cells = [c.name for c in (cells or [])]
        except Exception as exc:
            carb.log_warn(f"Import: could not fetch cells: {exc}")
            self._cells = []
        cell_idx = self._default_cell_combo_idx()
        self._fill_combo(
            self._cell_frame,
            "Select cell",
            self._cells,
            self._on_cell_chosen,
            selected=cell_idx,
        )
        if cell_idx > 0:
            self._on_cell_chosen(cell_idx)

    def _default_cell_combo_idx(self) -> int:
        if not self._cells:
            return 0
        if self._hint_cell and self._hint_cell in self._cells:
            return self._cells.index(self._hint_cell) + 1
        if len(self._cells) == 1:
            return 1
        return 0

    def _on_cell_chosen(self, combo_idx: int) -> None:
        self._selected_plan = None
        self._plan_names = []
        self._reset_plan_state()
        self._fill_combo(self._plan_frame, "Select plan", [], self._on_plan_chosen)
        if combo_idx <= 0 or combo_idx > len(self._cells):
            self._selected_cell_idx = None
            return
        self._selected_cell_idx = combo_idx - 1
        if self._plans_task is not None:
            self._plans_task.cancel()
        self._plans_task = _spawn(self._fetch_plans())

    async def _fetch_plans(self) -> None:
        cell = self._selected_cell()
        client = self._make_api_client()
        if client is None or not cell:
            self._plan_names = []
        else:
            try:
                self._plan_names = await list_nova_plan_names(client, cell)
            except Exception as exc:
                carb.log_warn(f"Import: could not list plans: {exc}")
                self._plan_names = []
            finally:
                await _close_client(client)
        self._fill_combo(
            self._plan_frame, "Select plan", self._plan_names, self._on_plan_chosen
        )

    # -- plan + mapping ----------------------------------------------------

    def _reset_plan_state(self) -> None:
        self._skill_dict = None
        self._config = None
        self._stored_model = None
        self._stored_joints = None
        self._stored_mg_path = None
        self._stored_scene_path = None
        self._verify_state = "pending"
        self._set_value_label(self._stored_model_label, None, "-")
        self._set_value_label(self._stored_scene_label, None, "-")
        self._clear_warning()
        self._show_verify_status("pending", "Select a plan and a motion group.")
        self._set_import_enabled(False)

    def _on_plan_chosen(self, combo_idx: int) -> None:
        self._set_import_enabled(False)
        if combo_idx <= 0 or combo_idx > len(self._plan_names):
            self._selected_plan = None
            self._reset_plan_state()
            return
        self._selected_plan = self._plan_names[combo_idx - 1]
        self._show_warning_loading()
        _spawn(self._load_plan())

    async def _load_plan(self) -> None:
        cell = self._selected_cell()
        client = self._make_api_client()
        skill_dict = config = None
        if client is not None and cell:
            try:
                skill_dict, config = await load_nova_plan(
                    client, cell, self._selected_plan
                )
            finally:
                await _close_client(client)
        self._skill_dict = skill_dict
        self._config = config
        if skill_dict is None and config is None:
            self._show_verify_status("bad", "Could not load this plan from Nova.")
            self._set_import_enabled(False)
            return
        self._stored_model = exported_skill_model(skill_dict) if skill_dict else None
        self._stored_joints = (
            exported_skill_joint_count(skill_dict) if skill_dict else None
        )
        metadata = (skill_dict or {}).get("metadata") or {}
        self._stored_mg_path = (skill_dict or {}).get("robot_prim_path") or (
            metadata.get("motion_group_prim_path")
        )
        if config is not None and not self._stored_mg_path:
            self._stored_mg_path = config.robot_prim_path
        self._stored_scene_path = metadata.get("scene_path")

        model_text = None
        if self._stored_model:
            model_text = self._stored_model
            if self._stored_joints:
                model_text += f"   ({self._stored_joints} joints)"
        self._set_value_label(
            self._stored_model_label, model_text, "No model associated."
        )
        self._set_value_label(
            self._stored_scene_label, self._stored_scene_path, "No scene associated."
        )

        # The poses preview is non-critical; never let it block verification.
        try:
            self._build_warning(skill_dict, config)
        except Exception as exc:
            carb.log_warn(f"Import: could not build poses preview: {exc}")
            self._clear_warning()

        # Associate the scene motion group automatically and verify against it.
        self._associate_scene_mg()

    # -- "Poses to import" table -------------------------------------------

    def _clear_warning(self) -> None:
        self._warning_overlay = None
        if self._warning_frame is not None:
            self._warning_frame.clear()

    def _show_warning_loading(self) -> None:
        """Overlay the poses table with a loading indicator while a plan loads."""
        if self._warning_overlay is not None:
            self._warning_overlay.visible = True
            return
        # No table yet — show a loading-only section so there is feedback.
        if self._warning_frame is None:
            return
        self._warning_frame.clear()
        with self._warning_frame:
            section = CollapsibleSection("Poses to import", collapsed=False)
            with section.body:
                ui.Label(
                    "Loading poses…",
                    height=40,
                    alignment=ui.Alignment.CENTER,
                    style={"color": NOVAColor.TEXT_SECONDARY.color, "font_size": 13},
                )

    def _build_warning(self, skill_dict, config) -> None:
        """Show every pose of the plan; highlight the ones that can't be imported.

        A pose can be imported (relinked) only when its prim exists in the current
        scene; missing ones get a red background.
        """
        self._clear_warning()
        if self._warning_frame is None:
            return
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        rows = []  # (prim_path, tcp, joints, importable)
        importable = 0
        for row in extract_pose_rows(skill_dict, config):
            prim_path = row.get("prim_path") or "-"
            prim = stage.GetPrimAtPath(prim_path) if row.get("prim_path") else None
            ok = bool(prim and prim.IsValid())
            if ok:
                importable += 1
            rows.append(
                (
                    prim_path,
                    _tcp_text(row.get("tcp_pose")),
                    _joints_text(row.get("joint")),
                    ok,
                )
            )
        if not rows:
            return
        total = len(rows)

        sec = {"color": NOVAColor.TEXT_SECONDARY.color, "font_size": 12}
        with self._warning_frame:
            # The pose count lives in the headline instead of a separate label.
            section = CollapsibleSection(
                f"Poses to import ({importable}/{total})", collapsed=False
            )
            with section.body:
                # Darker background behind the whole table area.
                with ui.ZStack():
                    ui.Rectangle(
                        style={"background_color": NOVAColor.TREEVIEW_BACKGROUND.color}
                    )
                    with ui.VStack(spacing=2, height=0):
                        ui.Spacer(height=4)
                        with ui.HStack(height=20, spacing=8):
                            ui.Spacer(width=4)
                            ui.Label("Prim path", width=ui.Fraction(2), style=sec)
                            ui.Label("TCP", width=ui.Fraction(3), style=sec)
                            ui.Label("Joints", width=ui.Fraction(3), style=sec)
                            ui.Spacer(width=4)
                        with ui.ZStack():
                            with ui.ScrollingFrame(
                                height=180,
                                horizontal_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                                vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                            ):
                                with ui.VStack(spacing=2, height=0):
                                    for prim_path, tcp, joints, ok in rows:
                                        self._build_pose_row(
                                            prim_path, tcp, joints, ok, sec
                                        )
                            # Loading overlay (shown while a plan is loading).
                            self._warning_overlay = ui.Frame(visible=False)
                            with self._warning_overlay:
                                with ui.ZStack():
                                    ui.Rectangle(style={"background_color": 0xCC1A1A1A})
                                    ui.Label(
                                        "Loading…",
                                        alignment=ui.Alignment.CENTER,
                                        style={
                                            "color": NOVAColor.TEXT_SECONDARY.color,
                                            "font_size": 13,
                                        },
                                    )
                        ui.Spacer(height=4)

    @staticmethod
    def _build_pose_row(prim_path, tcp, joints, importable, sec) -> None:
        # Red translucent background (0xAABBGGRR) for non-importable poses.
        with ui.ZStack(height=22):
            if not importable:
                ui.Rectangle(style={"background_color": 0x330000FF})
            with ui.HStack(spacing=8):
                ui.Spacer(width=4)
                ui.Label(
                    prim_path,
                    width=ui.Fraction(2),
                    elided_text=True,
                    tooltip=prim_path,
                    style={"color": NOVAColor.TEXT_PRIMARY.color},
                )
                ui.Label(
                    tcp,
                    width=ui.Fraction(3),
                    elided_text=True,
                    tooltip=tcp,
                    style=sec,
                )
                ui.Label(
                    joints,
                    width=ui.Fraction(3),
                    elided_text=True,
                    tooltip=joints,
                    style=sec,
                )
                ui.Spacer(width=4)

    # -- verify ------------------------------------------------------------

    def _render_scene_label(self, path: str | None) -> None:
        if self._scene_frame is None:
            return
        self._scene_frame.clear()
        with self._scene_frame:
            if path:
                ui.Label(
                    path,
                    elided_text=True,
                    tooltip=path,
                    style={"color": NOVAColor.TEXT_SECONDARY.color},
                )
            else:
                ui.Label(
                    "No motion group in the scene",
                    style={"color": NOVAColor.TEXT_DISABLED.color},
                )

    def _associate_scene_mg(self) -> None:
        """Associate the scene motion group for the loaded plan and verify it.

        Uses the motion group whose prim path matches the stored one, otherwise
        the only motion group in the scene. Shown as a read-only label.
        """
        target = None
        if self._stored_mg_path in self._scene_mg_paths:
            target = self._stored_mg_path
        elif self._scene_mg_paths:
            target = self._scene_mg_paths[0]
        self._selected_scene_mg = target
        self._render_scene_label(target)
        if target:
            _spawn(self._verify())
        else:
            self._set_import_enabled(False)

    def _show_verify_status(self, state: str, msg: str) -> None:
        """state: 'pending' | 'ok' | 'warn' | 'bad' — colors the message."""
        if self._verify_label is None:
            return
        self._verify_label.text = msg
        color = {
            "ok": NOVAColor.SUCCESS_MAIN.color,
            "warn": NOVAColor.WARNING_DARK.color,
            "bad": NOVAColor.ERROR_MAIN.color,
        }.get(state, NOVAColor.TEXT_SECONDARY.color)
        self._verify_label.style = {"color": color}

    async def _verify(self) -> None:
        # Three states: "ok" (verified match), "mismatch" (confirmed model/joint
        # mismatch — blocks import), "unknown" (couldn't verify — allowed, with a
        # caution, since a failed fetch is not a confirmed mismatch).
        state, msg = await self._compare(self._selected_scene_mg)
        self._verify_state = state
        visual = {"ok": "ok", "mismatch": "bad"}.get(state, "warn")
        self._show_verify_status(visual, msg)
        self._set_import_enabled(state != "mismatch")

    async def _compare(self, scene_mg_path: str | None) -> tuple[str, str]:
        if not scene_mg_path:
            return "mismatch", "Select a scene motion group."
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(scene_mg_path) if stage else None
        mg_config = (
            get_motion_group_configuration_from_prim(prim)
            if prim and prim.IsValid()
            else None
        )
        if not mg_config:
            return "unknown", "Could not read the motion group; not verified."
        msc = mg_config.motion_stream_configuration
        try:
            async with get_api_client_from_config(msc.get_api_configuration()) as api:
                desc = await wb_v2.MotionGroupApi(api).get_motion_group_description(
                    cell=msc.cell,
                    controller=msc.controller,
                    motion_group=msc.motion_group,
                )
        except Exception as exc:
            carb.log_warn(
                f"Import verify: could not fetch description for "
                f"'{scene_mg_path}': {exc}"
            )
            return "unknown", "Could not verify the motion group — import allowed."
        scene_model = getattr(desc, "motion_group_model", None)
        dh = getattr(desc, "dh_parameters", None)
        scene_joints = len(dh) if dh else None
        if self._stored_model and scene_model and self._stored_model != scene_model:
            return "mismatch", f"Model mismatch: {self._stored_model} vs {scene_model}."
        if self._stored_joints and scene_joints and self._stored_joints != scene_joints:
            return (
                "mismatch",
                f"Joint-count mismatch: {self._stored_joints} vs {scene_joints}.",
            )
        label = scene_model or scene_mg_path
        if scene_joints:
            label += f" ({scene_joints} joints)"
        return "ok", f"Match: {label}."

    @staticmethod
    def _set_value_label(
        label: ui.Label | None, value: str | None, placeholder: str
    ) -> None:
        if label is None:
            return
        if value:
            label.text = value
            label.style = {"color": NOVAColor.TEXT_SECONDARY.color}
        else:
            label.text = placeholder
            label.style = {"color": NOVAColor.TEXT_DISABLED.color}

    def _set_import_enabled(self, enabled: bool) -> None:
        if self._import_button is not None:
            self._import_button.enabled = enabled

    # -- import ------------------------------------------------------------

    def _do_import(self) -> None:
        if self._verify_state == "mismatch" or not self._selected_scene_mg:
            return
        config = self._config
        if config is None and self._skill_dict is not None:
            config = config_from_exported_skill(self._skill_dict)
        if config is None:
            nm.post_notification(
                "Nothing to import for the selected plan.",
                duration=4.0,
                status=nm.NotificationStatus.WARNING,
            )
            return
        config.robot_prim_path = self._selected_scene_mg
        on_import = self._on_import
        self._close()
        try:
            on_import(config)
        except Exception as exc:
            import traceback

            carb.log_error("Import failed: " + "".join(traceback.format_exception(exc)))
            nm.post_notification(
                "Import failed. See log for details.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
