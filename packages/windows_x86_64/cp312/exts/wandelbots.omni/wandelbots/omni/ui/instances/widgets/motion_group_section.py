import weakref
from typing import Callable, Optional

import carb
import omni.ui as ui

from wandelbots.omni.instances.events import (
    subscribe_to_motion_group_connection_changed,
)
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import (
    NOVAControllerData,
    NOVAInstance,
    NOVAMotionGroupData,
)
from wandelbots.omni.instances.stage_discovery import list_motion_group_prim_suggestions
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.instances.models.motion_group_enabled_model import (
    MotionGroupEnabledModel,
)
from wandelbots.omni.ui.instances.motion_group_widget import MotionGroupWidget
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection
from wandelbots.omni.ui.widgets.switch import Switch, WARNING_SWITCH_STYLE


class MotionGroupSection(ui.VStack):
    """Collapsible section for a single NOVA motion group.

    Renders a ``CollapsibleSection`` with an optional quick-connect
    button and enable/disable switch in the header, and a full
    ``MotionGroupWidget`` in the body.
    """

    def __init__(
        self,
        instances_service: NOVAInstancesService,
        instance: NOVAInstance,
        controller: NOVAControllerData,
        motion_group: NOVAMotionGroupData,
        on_connection_changed: Optional[Callable] = None,
        **kwargs,
    ):
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._instances_service = instances_service
        self._instance = instance
        self._controller = controller
        self._motion_group = motion_group
        self._on_connection_changed = on_connection_changed
        self._connect_btn_container: Optional[ui.VStack] = None
        self._connected_prim_path: Optional[str] = None

        self._event_sub = subscribe_to_motion_group_connection_changed(
            lambda payload, weak_self=weakref.ref(self): (
                weak_self()._on_global_connection_changed(payload)
                if weak_self()
                else None
            )
        )

        self._build()

    def _build(self):
        title = (
            f"{self._motion_group.motion_group_model_name} ({self._motion_group.name})"
        )

        connected = self._instances_service.find_connected_motion_group_by(
            host=self._instance.host,
            cell=self._controller.cell_name,
            controller=self._controller.name,
            motion_group=self._motion_group.name,
        )
        if len(connected) > 1:
            carb.log_warn(
                f"Multiple connected motion groups found for "
                f"{self._motion_group.name} in controller "
                f"{self._controller.name} on instance "
                f"{self._instance.display_name}"
            )

        prim_path = connected[0].prim_path if connected else None
        self._connected_prim_path = prim_path

        can_connect = self._instance.is_reachable
        matched = (
            self._find_matching_articulation()
            if can_connect and not self._connected_prim_path
            else None
        )
        connect_fn = self._make_connect_fn(matched) if matched else None

        with self:
            self._section = CollapsibleSection(
                title=title,
                collapsed=True,
                build_header_fn=lambda sec, _prim=prim_path, _fn=connect_fn, _matched=matched, _self=self: (
                    _self._build_header(_prim, _fn, _matched)
                ),
                on_collapsed_changed=lambda collapsed, _self=self: (
                    _self._on_collapsed_changed(collapsed)
                ),
            )
            with self._section.body:
                self._motion_group_widget = MotionGroupWidget(
                    instances_service=self._instances_service,
                    instance=self._instance,
                    controller=self._controller,
                    motion_group=self._motion_group,
                    matched_prim_path=matched,
                    on_connection_changed=self._on_connection_changed,
                )

    def _build_header(
        self,
        prim_path: Optional[str],
        connect_fn: Optional[Callable],
        matched_prim: Optional[str] = None,
    ):
        if connect_fn:
            self._connect_btn_container = ui.VStack(width=0)
            with self._connect_btn_container:
                ui.Spacer()
                ui.Button(
                    "Connect",
                    width=70,
                    height=17,
                    style={
                        "background_color": NOVAColor.PRIMARY_MAIN.color,
                        "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                        "Button::hovered": {
                            "background_color": NOVAColor.PRIMARY_DARK.color,
                        },
                    },
                    clicked_fn=lambda: connect_fn(),
                    tooltip=(
                        f"Connect to:\n{matched_prim}"
                        if matched_prim
                        else "Connect to the matching articulation on stage"
                    ),
                )
                ui.Spacer()

        if prim_path:
            with ui.VStack(width=0):
                ui.Spacer()
                Switch(
                    height=17,
                    model=MotionGroupEnabledModel(prim_path),
                    style=(
                        WARNING_SWITCH_STYLE
                        if not self._instance.is_reachable
                        else None
                    ),
                    tooltip="Toggle motion group for simulation",
                )
                ui.Spacer()

    def _on_collapsed_changed(self, collapsed: bool):
        if self._connect_btn_container:
            self._connect_btn_container.visible = collapsed

    def _on_global_connection_changed(self, payload):
        """Rebuild this section when the event affects it."""
        event_host = payload.get("host", "")
        event_prim = payload.get("prim_path", "")
        event_mg = payload.get("motion_group", "")

        is_this_section = (
            event_host == self._instance.host and event_mg == self._motion_group.name
        )
        prim_was_stolen = (
            self._connected_prim_path
            and event_prim == self._connected_prim_path
            and not is_this_section
        )
        if is_this_section or prim_was_stolen:
            self.rebuild()

    def rebuild(self):
        self.clear()
        self._build()

    def _find_matching_articulation(self) -> Optional[str]:
        from wandelbots.omni.manipulators.utils import get_scene_motion_group_prim_paths

        configs = self._instances_service._get_stage_motion_group_configurations()
        articulations = get_scene_motion_group_prim_paths()
        suggestions = list_motion_group_prim_suggestions(
            configs,
            cell=self._controller.cell_name,
            controller=self._controller.name,
            motion_group=self._motion_group.name,
            scene_articulations=articulations,
            motion_group_model_name=self._motion_group.motion_group_model_name,
        )
        if suggestions:
            return suggestions[0]
        return None

    def _make_connect_fn(self, prim_path: str) -> Callable:
        weak_self = weakref.ref(self)

        def _connect():
            self_ref = weak_self()
            if not self_ref:
                return

            def on_complete(success, message=""):
                if success and self_ref._on_connection_changed:
                    self_ref._on_connection_changed()

            self_ref._instances_service.create_motion_group_from_nova(
                instance=self_ref._instance,
                controller=self_ref._controller,
                motion_group_name=self_ref._motion_group.name,
                prim_path=prim_path,
                use_external_joint_stream=False,
                callback=on_complete,
            )

        return _connect
