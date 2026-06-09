import os
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import carb
import omni
import omni.client
import omni.kit.actions.core
import omni.kit.app
import omni.kit.commands
import omni.kit.menu.utils
import omni.kit.notification_manager as nm
import omni.ui as ui
import omni.usd
from omni.kit.async_engine import run_coroutine
from omni.kit.window.filepicker import FilePickerDialog
from omni.kit.window.property.templates import HORIZONTAL_SPACING
from pxr import Usd

from wandelbots.omni.constants import EXTENSION_ID, EXTENSION_WINDOW_MENU_ROOT
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.widgets import PrimPathList


WINDOW_MENU_ROOT = "Tools"
_DEFAULT_RECORD_SUBFOLDER = "nova_recordings"
_STAGE_RECORDER_EXT = "omni.kit.stagerecorder.bundle"
_LABEL_WIDTH = 160
_BROWSE_BUTTON_STYLE = {
    "Button": {
        "background_color": 0x40000000,
        "border_radius": 2,
        "margin": 0,
        "padding": 0,
        "font_size": 16,
    },
}


class AnimationRecorderWindow:
    def __init__(self):
        self._recording: bool = False
        self._recording_file_valid: bool = False
        self._last_recorded_file: str | None = None
        self._main_stage_valid: bool = False
        self._file_picker: FilePickerDialog | None = None
        self._recording_file_picker: FilePickerDialog | None = None
        self._main_stage_file_picker: FilePickerDialog | None = None
        self._prim_path_list: PrimPathList | None = None
        self._record_controls_frame: ui.Frame | None = None
        self._playback_controls_frame: ui.Frame | None = None

        self._stage = omni.usd.get_context().get_stage()

        self._recording_name_model = ui.SimpleStringModel("nova_recording")
        self._record_folder_model = ui.SimpleStringModel(_get_default_record_folder())
        self._recording_file_model = ui.SimpleStringModel("")
        self._main_stage_model = ui.SimpleStringModel("")

        self.window = ui.Window("Animation Recorder", width=420, height=520)
        self.window.set_visibility_changed_fn(
            lambda _: omni.kit.menu.utils.refresh_menu_items(WINDOW_MENU_ROOT)
        )
        self.window.visible = False
        self.window.deferred_dock_in("Property", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE)

        self._stage_event_sub = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(self._on_stage_event)
        )

        self._build_ui()

    def _build_ui(self):
        saved_paths = self._prim_path_list.paths if self._prim_path_list else []
        self.window.frame.clear()
        with self.window.frame:
            with ui.ScrollingFrame(
                vertical_scroll_bar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                width=ui.Percent(100),
                height=ui.Percent(100),
            ):
                with ui.HStack():
                    ui.Spacer(width=8)
                    with ui.VStack(spacing=8):
                        ui.Spacer(height=4)
                        self._build_record_options_section(saved_paths)
                        self._record_controls_frame = ui.Frame(height=0)
                        with self._record_controls_frame:
                            self._build_record_controls()
                        self._build_playback_section()
                        ui.Spacer(height=8)
                    ui.Spacer(width=8)

    def _build_record_options_section(self, saved_paths: list[str] | None = None):
        with ui.HStack(height=30, spacing=12):
            ui.Label(
                "Record Options",
                height=30,
                width=0,
                style={"font_size": 18},
            )
            ui.Line(style={"color": NOVAColor.DIVIDER.color}, width=ui.Fraction(1))

        ui.Label(
            "Choose the prims to record an animation for, and specify the output folder and base name for the recording.",
            style={"color": NOVAColor.TEXT_SECONDARY.color},
            word_wrap=True,
            height=0,
        )

        with ui.HStack(
            height=0, spacing=HORIZONTAL_SPACING, style={"HStack": {"margin": 4}}
        ):
            ui.Label(
                "Animation Prims",
                tooltip="The prims to record animation for",
                width=_LABEL_WIDTH,
                alignment=ui.Alignment.LEFT_TOP,
            )
            self._prim_path_list = None
            if self._stage:
                self._prim_path_list = PrimPathList(
                    stage=self._stage,
                    on_changed_fn=lambda _paths, _ws=weakref.ref(self): (
                        _ws()._rebuild_record_controls() if _ws() else None
                    ),
                    target_name="Prim",
                    target_plural_name="Prims",
                )
                if saved_paths:
                    self._prim_path_list.paths = saved_paths

        with ui.VStack(height=0, spacing=4, style={"VStack": {"margin": 4}}):
            with ui.HStack(spacing=4, height=28):
                ui.Label(
                    "Output folder",
                    tooltip="Folder to save the recorded USD file",
                    width=_LABEL_WIDTH,
                )
                field = ui.StringField(
                    model=self._record_folder_model,
                    tooltip=self._record_folder_model.as_string,
                )
                self._record_folder_model.add_value_changed_fn(
                    lambda m, f=field: setattr(f, "tooltip", m.as_string)
                )
                ui.Button(
                    "...",
                    width=28,
                    height=28,
                    tooltip="Browse for output folder",
                    clicked_fn=lambda ws=weakref.proxy(self): ws._open_folder_picker(),
                    style=_BROWSE_BUTTON_STYLE,
                )

            with ui.HStack(spacing=HORIZONTAL_SPACING, height=28):
                ui.Label(
                    "Recording name",
                    tooltip="Base name for the recording",
                    width=_LABEL_WIDTH,
                )
                ui.StringField(model=self._recording_name_model, height=24)

    def _build_record_controls(self):
        with ui.HStack(height=40, spacing=HORIZONTAL_SPACING):
            ui.Spacer(width=_LABEL_WIDTH)
            if self._recording:
                ui.Button(
                    "Stop Recording",
                    height=36,
                    tooltip="Stop the active recording session",
                    style={
                        "Button": {
                            "background_color": NOVAColor.ERROR_MAIN.color,
                            "color": NOVAColor.ERROR_CONTRAST_TEXT.color,
                        },
                        "Button:hovered": {
                            "background_color": NOVAColor.ERROR_DARK.color,
                        },
                    },
                    clicked_fn=lambda ws=weakref.proxy(self): ws._stop_recording(),
                )
            else:
                ui.Button(
                    "Start Recording",
                    height=36,
                    tooltip="Record animation for the selected prims",
                    enabled=bool(self._prim_path_list and self._prim_path_list.paths),
                    style={
                        "Button": {
                            "background_color": NOVAColor.PRIMARY_MAIN.color,
                            "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                        },
                        "Button:hovered": {
                            "background_color": NOVAColor.PRIMARY_DARK.color,
                        },
                        "Button:disabled": {
                            "background_color": NOVAColor.DIVIDER.color,
                        },
                    },
                    clicked_fn=lambda ws=weakref.proxy(self): ws._start_recording(),
                )

    def _build_playback_section(self):
        with ui.HStack(height=30, spacing=12):
            ui.Label(
                "Playback Preparation",
                height=30,
                width=0,
                style={"font_size": 18},
            )
            ui.Line(style={"color": NOVAColor.DIVIDER.color}, width=ui.Fraction(1))

        ui.Label(
            "Combines the selected recording with the current stage as a layered USD "
            "for immediate timeline playback.",
            style={"color": NOVAColor.TEXT_SECONDARY.color},
            word_wrap=True,
            height=0,
        )
        ui.Spacer(height=4)

        with ui.VStack(height=0, spacing=4, style={"VStack": {"margin": 4}}):
            with ui.HStack(spacing=4, height=28):
                ui.Label(
                    "Recording file",
                    tooltip="The recorded USD file to combine with the stage",
                    width=_LABEL_WIDTH,
                )
                rec_field = ui.StringField(
                    model=self._recording_file_model,
                    tooltip=self._recording_file_model.as_string,
                )
                self._recording_file_model.add_value_changed_fn(
                    lambda m, f=rec_field: setattr(f, "tooltip", m.as_string)
                )
                ui.Button(
                    "...",
                    width=28,
                    height=28,
                    tooltip="Browse for a recording file",
                    clicked_fn=lambda ws=weakref.proxy(self): (
                        ws._open_recording_file_picker()
                    ),
                    style=_BROWSE_BUTTON_STYLE,
                )

            with ui.HStack(spacing=4, height=28):
                ui.Label(
                    "Main stage",
                    tooltip="The main USD stage to merge with the recording",
                    width=_LABEL_WIDTH,
                )
                stage_field = ui.StringField(
                    model=self._main_stage_model,
                    tooltip=self._main_stage_model.as_string,
                )
                self._main_stage_model.add_value_changed_fn(
                    lambda m, f=stage_field: setattr(f, "tooltip", m.as_string)
                )
                ui.Button(
                    "...",
                    width=28,
                    height=28,
                    tooltip="Browse for the main stage file",
                    clicked_fn=lambda ws=weakref.proxy(self): (
                        ws._open_main_stage_file_picker()
                    ),
                    style=_BROWSE_BUTTON_STYLE,
                )

        ui.Spacer(height=4)

        self._playback_controls_frame = ui.Frame(height=0)
        with self._playback_controls_frame:
            self._build_playback_controls()

    def _build_playback_controls(self):
        with ui.HStack(height=40, spacing=HORIZONTAL_SPACING):
            ui.Spacer(width=_LABEL_WIDTH)
            ui.Button(
                "Process & Open for Playback",
                height=36,
                enabled=self._recording_file_valid and self._main_stage_valid,
                tooltip="Combine the recording with the current stage and open for playback",
                style={
                    "Button": {
                        "background_color": NOVAColor.PRIMARY_MAIN.color,
                    },
                    "Button:hovered": {
                        "background_color": NOVAColor.PRIMARY_LIGHT.color,
                    },
                    "Button:disabled": {
                        "background_color": NOVAColor.DIVIDER.color,
                    },
                },
                clicked_fn=lambda ws=weakref.proxy(self): (
                    ws._combine_recording_and_stage_and_open()
                ),
            )

    def _open_folder_picker(self) -> None:
        def _on_apply(filename: str, path: str, ws=weakref.proxy(self)) -> None:
            ws._file_picker.hide()
            if path and "://" in path:
                ws._record_folder_model.set_value(path.rstrip("/"))
            elif path:
                ws._record_folder_model.set_value(path)

        def _on_cancel(filename: str, path: str, ws=weakref.proxy(self)) -> None:
            ws._file_picker.hide()

        stage_dir = _get_stage_directory()
        default_dir = self._record_folder_model.as_string or stage_dir

        self._file_picker = FilePickerDialog(
            "Select Output Folder",
            apply_button_label="Select Folder",
            click_apply_handler=_on_apply,
            click_cancel_handler=_on_cancel,
        )
        self._file_picker.show(default_dir)

    def _start_recording(self):
        selected = self._prim_path_list.paths if self._prim_path_list else []
        if not selected:
            nm.post_notification(
                "No target prims selected for recording.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        self._ensure_stage_recorder_enabled()

        output_folder = self._record_folder_model.as_string
        recording_name = self._recording_name_model.as_string
        target_paths = [[path, True] for path in selected]

        if not _is_nucleus_path(output_folder):
            os.makedirs(output_folder, exist_ok=True)

        omni.kit.commands.execute(
            "StartRecording",
            target_paths=target_paths,
            live_mode=True,
            use_frame_range=False,
            start_frame=0,
            end_frame=100,
            use_preroll=False,
            preroll_frame=0,
            record_to="FILE",
            take_name=recording_name,
            record_folder=output_folder,
            increment_name=True,
            apply_root_anim=False,
            fps=0.0,
        )

        self._recording = True
        self._last_recorded_file = None
        self._deferred_build_ui()

    def _stop_recording(self):
        omni.kit.commands.execute("StopRecording")
        self._recording = False
        run_coroutine(self._find_and_set_last_recording())

    async def _find_and_set_last_recording(self):
        folder = self._record_folder_model.as_string
        recording_name = self._recording_name_model.as_string

        found = await _find_latest_recording_async(folder, recording_name)
        if found is None:
            nm.post_notification(
                f"Recording stopped but no file found in: {folder}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
        self._last_recorded_file = found
        if found:
            self._recording_file_model.set_value(found)
            self._recording_file_valid = True
        stage_url = omni.usd.get_context().get_stage_url()
        if stage_url:
            self._main_stage_model.set_value(stage_url)
            self._main_stage_valid = True
        self._deferred_build_ui()

    def _open_recording_file_picker(self) -> None:
        def _on_apply(filename: str, path: str, ws=weakref.proxy(self)) -> None:
            ws._recording_file_picker.hide()
            file_url = _join_url(path.rstrip("/"), filename) if path else filename
            ws._recording_file_model.set_value(file_url)
            ws._recording_file_valid = True
            ws._rebuild_playback_controls()

        def _on_cancel(filename: str, path: str, ws=weakref.proxy(self)) -> None:
            ws._recording_file_picker.hide()

        current = self._recording_file_model.as_string
        if current:
            default_dir = (
                current.rsplit("/", 1)[0]
                if "/" in current
                else os.path.dirname(current)
            )
            default_file = _url_basename(current)
        else:
            default_dir = _get_stage_directory() or self._record_folder_model.as_string
            default_file = ""

        self._recording_file_picker = FilePickerDialog(
            "Select Recording File",
            apply_button_label="Select",
            click_apply_handler=_on_apply,
            click_cancel_handler=_on_cancel,
            file_extension_options=[("USD Files", "*.usd *.usda *.usdc")],
            filename=default_file,
        )
        self._recording_file_picker.show(default_dir)

    def _open_main_stage_file_picker(self) -> None:
        def _on_apply(filename: str, path: str, ws=weakref.proxy(self)) -> None:
            ws._main_stage_file_picker.hide()
            file_url = _join_url(path.rstrip("/"), filename) if path else filename
            ws._main_stage_model.set_value(file_url)
            ws._main_stage_valid = True
            ws._rebuild_playback_controls()

        def _on_cancel(filename: str, path: str, ws=weakref.proxy(self)) -> None:
            ws._main_stage_file_picker.hide()

        current = self._main_stage_model.as_string
        if current:
            default_dir = (
                current.rsplit("/", 1)[0]
                if "/" in current
                else os.path.dirname(current)
            )
            default_file = _url_basename(current)
        else:
            default_dir = _get_stage_directory() or ""
            default_file = ""

        self._main_stage_file_picker = FilePickerDialog(
            "Select Main Stage",
            apply_button_label="Select",
            click_apply_handler=_on_apply,
            click_cancel_handler=_on_cancel,
            file_extension_options=[("USD Files", "*.usd *.usda *.usdc")],
            filename=default_file,
        )
        self._main_stage_file_picker.show(default_dir)

    def _combine_recording_and_stage_and_open(self):
        recording_url = self._recording_file_model.as_string
        if not recording_url:
            nm.post_notification(
                "No recording file selected.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return
        run_coroutine(self._async_combine_recording_and_stage_and_open())

    async def _async_combine_recording_and_stage_and_open(self):
        from wandelbots.omni.manipulators import get_motion_group_service

        from .usd_playback_processor import UsdPlaybackProcessor

        # Stop and remove all motion streams before switching stages
        motion_group_service = get_motion_group_service()
        if motion_group_service:
            try:
                await motion_group_service.stop_streams()
            except Exception:
                pass

        original_stage_url = self._main_stage_model.as_string
        recording_url = self._recording_file_model.as_string

        original_stage = Usd.Stage.Open(original_stage_url)
        if not original_stage:
            nm.post_notification(
                f"Failed to open main stage: {original_stage_url}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        recording_basename = _url_basename(recording_url)
        recording_folder = recording_url[: -len(recording_basename)].rstrip("/")
        stem = _url_stem(recording_url)
        playback_url = _join_url(recording_folder, f"{stem}_playback.usd")

        try:
            UsdPlaybackProcessor.create_playback_stage(
                original_stage, original_stage_url, recording_url, playback_url
            )
        except Exception as e:
            carb.log_error(f"Failed to create playback stage: {e}")
            nm.post_notification(
                f"Failed to create playback stage: {e}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        nm.post_notification(
            f"Playback stage created: {_url_basename(playback_url)}",
            duration=5.0,
            status=nm.NotificationStatus.INFO,
        )

        await omni.kit.app.get_app().next_update_async()
        omni.usd.get_context().open_stage(playback_url)

    def _on_stage_event(self, event) -> None:
        if event.type == int(omni.usd.StageEventType.OPENED):
            self._stage = omni.usd.get_context().get_stage()
            self._on_stage_changed()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._stage = None
            self._on_stage_changed()

    def _on_stage_changed(self) -> None:
        if self._prim_path_list is not None:
            self._prim_path_list.destroy()
            self._prim_path_list = None
        self._record_folder_model.set_value(_get_default_record_folder())
        self._build_ui()

    def _rebuild_record_controls(self):
        if not self._record_controls_frame:
            return
        self._record_controls_frame.clear()
        with self._record_controls_frame:
            self._build_record_controls()

    def _rebuild_playback_controls(self):
        if not self._playback_controls_frame:
            return
        self._playback_controls_frame.clear()
        with self._playback_controls_frame:
            self._build_playback_controls()

    def _deferred_build_ui(self):
        async def _rebuild():
            await omni.kit.app.get_app().next_update_async()
            self._build_ui()

        run_coroutine(_rebuild())

    @staticmethod
    def _ensure_stage_recorder_enabled():
        manager = omni.kit.app.get_app().get_extension_manager()
        if not manager.is_extension_enabled(_STAGE_RECORDER_EXT):
            manager.set_extension_enabled_immediate(_STAGE_RECORDER_EXT, True)
            carb.log_info(f"Enabled extension: {_STAGE_RECORDER_EXT}")

    def destroy(self) -> None:
        """Tear down all resources. Safe to call multiple times."""
        self._stage_event_sub = None
        if self._prim_path_list:
            self._prim_path_list.destroy()
            self._prim_path_list = None
        if self.window:
            self.window.set_visibility_changed_fn(None)
            self.window.visible = False
        self.window = None


@dataclass
class AnimationRecorderWindowSubscription:
    animation_recorder_window: AnimationRecorderWindow = None
    menu_subscriptions: list = None

    def __del__(self):
        if self.animation_recorder_window:
            self.animation_recorder_window.destroy()
            self.animation_recorder_window = None
        if self.menu_subscriptions:
            omni.kit.menu.utils.remove_menu_items(
                self.menu_subscriptions, WINDOW_MENU_ROOT
            )


def register_animation_recorder_window():
    animation_recorder_window = AnimationRecorderWindow()

    def toggle_visibility():
        animation_recorder_window.window.visible = (
            not animation_recorder_window.window.visible
        )

    def _is_visible(
        toolbar: Callable[[], AnimationRecorderWindow | None] = weakref.ref(
            animation_recorder_window
        ),
    ):
        return toolbar().window.visible if toolbar() else False

    ext_id = EXTENSION_ID
    name = "Animation Recorder"
    action_name = "toggle_animation_recorder_window"
    action_unique = f"{ext_id}_{name}_{action_name}"
    action_registry = omni.kit.actions.core.get_action_registry()
    action_registry.deregister_action(ext_id, action_unique)
    action_registry.register_action(
        ext_id, action_unique, toggle_visibility, display_name=name, tag="MenuItem"
    )

    return AnimationRecorderWindowSubscription(
        animation_recorder_window,
        omni.kit.menu.utils.add_menu_items(
            [
                omni.kit.menu.utils.MenuItemDescription(
                    name=EXTENSION_WINDOW_MENU_ROOT,
                    sub_menu=[
                        omni.kit.menu.utils.MenuItemDescription(
                            name=name,
                            onclick_action=(ext_id, action_unique),
                            ticked_fn=_is_visible,
                        )
                    ],
                )
            ],
            WINDOW_MENU_ROOT,
        ),
    )


def _is_nucleus_path(path: str) -> bool:
    return "omniverse://" in path


def _get_stage_directory() -> str | None:
    stage_url = omni.usd.get_context().get_stage_url() or ""
    if not stage_url:
        return None
    if "://" in stage_url:
        return stage_url.rsplit("/", 1)[0] if "/" in stage_url else stage_url
    return os.path.dirname(stage_url) or None


def _get_default_record_folder() -> str:
    stage_dir = _get_stage_directory()
    if stage_dir:
        return _join_url(stage_dir, _DEFAULT_RECORD_SUBFOLDER)
    return str(Path.home() / _DEFAULT_RECORD_SUBFOLDER)


def _join_url(folder: str, filename: str) -> str:
    if _is_nucleus_path(folder):
        return folder.rstrip("/") + "/" + filename
    return os.path.join(folder, filename)


def _url_stem(url: str) -> str:
    name = _url_basename(url)
    return name.rsplit(".", 1)[0] if "." in name else name


def _url_basename(url: str) -> str:
    return url.rsplit("/", 1)[-1] if "/" in url else url


async def _find_latest_recording_async(folder: str, recording_name: str) -> str | None:
    if _is_nucleus_path(folder):
        return await _find_latest_recording_nucleus(folder, recording_name)
    return _find_latest_recording_local(folder, recording_name)


def _find_latest_recording_local(folder: str, recording_name: str) -> str | None:
    folder_path = Path(folder)
    if not folder_path.exists():
        return None
    candidates = sorted(
        folder_path.glob(f"{recording_name}*.usd*"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


async def _find_latest_recording_nucleus(
    folder: str, recording_name: str
) -> str | None:
    result, entries = await omni.client.list_async(folder)
    if result != omni.client.Result.OK:
        return None
    matches = [
        e
        for e in entries
        if e.relative_path.startswith(recording_name)
        and ".usd" in e.relative_path.lower()
    ]
    if not matches:
        return None
    matches.sort(key=lambda e: e.modified_time, reverse=True)
    return folder.rstrip("/") + "/" + matches[0].relative_path
