import weakref

import omni.kit.notification_manager as nm
import omni.kit.viewport.utility
import omni.usd
from pxr import Gf, Sdf, Usd


class CameraNearClipCheck:
    def __init__(self):
        self._render_settings_sub = None
        self._pending_notification = None
        self._active_camera_path: Sdf.Path = Sdf.Path.emptyPath
        self._dismissed_cameras: set[Sdf.Path] = set()
        self._stage_event_sub = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(
                lambda event, weak_self=weakref.ref(self): (
                    weak_self()._on_stage_event(event) if weak_self() else None
                )
            )
        )

    def _reset_state(self) -> None:
        self._active_camera_path = Sdf.Path.emptyPath
        self._dismissed_cameras.clear()
        self._dismiss_pending_notification()

    def _on_stage_event(self, event) -> None:
        if event.type == int(omni.usd.StageEventType.OPENED):
            self._reset_state()
            self._subscribe_to_viewport()
        elif event.type == int(omni.usd.StageEventType.CLOSED):
            self._reset_state()
            self._unsubscribe_from_viewport()

    def _subscribe_to_viewport(self) -> None:
        self._unsubscribe_from_viewport()
        viewport_api = omni.kit.viewport.utility.get_active_viewport()
        if viewport_api is None:
            return
        self._render_settings_sub = viewport_api.subscribe_to_render_settings_change(
            lambda camera_path, resolution, vp_api, weak_self=weakref.ref(self): (
                weak_self()._on_render_settings_changed(camera_path, resolution, vp_api)
                if weak_self()
                else None
            )
        )

    def _unsubscribe_from_viewport(self) -> None:
        if self._render_settings_sub:
            self._render_settings_sub.destroy()
            self._render_settings_sub = None

    def _dismiss_pending_notification(self) -> None:
        if self._pending_notification and not self._pending_notification.dismissed:
            self._pending_notification.dismiss()
        self._pending_notification = None

    def _on_render_settings_changed(
        self, camera_path: Sdf.Path, resolution, viewport_api
    ) -> None:
        if camera_path == self._active_camera_path:
            return
        self._active_camera_path = camera_path
        self._dismiss_pending_notification()
        self._check_camera(camera_path)

    def _check_camera(self, camera_path: Sdf.Path) -> None:
        if not camera_path or camera_path in self._dismissed_cameras:
            return
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return
        prim = stage.GetPrimAtPath(camera_path)
        if not prim or not prim.IsValid() or prim.GetTypeName() != "Camera":
            return
        clip_attr = prim.GetAttribute("clippingRange")
        clipping_range = clip_attr.Get() if clip_attr.IsValid() else None
        if clipping_range is None or abs(clipping_range[0] - 1.0) >= 1e-6:
            return

        self._dismissed_cameras.add(camera_path)

        def apply_fix():
            current_stage = omni.usd.get_context().get_stage()
            if current_stage is None or not prim.IsValid():
                return
            current_clip = clip_attr.Get()
            if current_clip is None:
                return
            session_layer = current_stage.GetSessionLayer()
            target_layer = (
                session_layer
                if session_layer.GetPrimAtPath(camera_path)
                else current_stage.GetRootLayer()
            )
            with Usd.EditContext(current_stage, Usd.EditTarget(target_layer)):
                clip_attr.Set(Gf.Vec2f(0.001, current_clip[1]))

        self._pending_notification = nm.post_notification(
            f'Camera "{camera_path}" has near clipping plane set to 1.0. Reset to 0.001?',
            hide_after_timeout=False,
            status=nm.NotificationStatus.INFO,
            button_infos=[
                nm.NotificationButtonInfo("Yes", on_complete=apply_fix),
                nm.NotificationButtonInfo("No", on_complete=None),
            ],
        )

    def __del__(self):
        self._dismiss_pending_notification()
        self._unsubscribe_from_viewport()
        self._stage_event_sub = None


def register_camera_near_clip_check() -> CameraNearClipCheck:
    return CameraNearClipCheck()
