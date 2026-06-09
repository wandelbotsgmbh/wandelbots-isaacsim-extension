"""TreeView model and delegate for trajectory pose items with motion type selection."""

from __future__ import annotations

from typing import Callable

import carb
import omni.ui as ui

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.styles import TOOLTIP_STYLE

from wandelbots.omni.ui.tool.trajectory_planner.cells import (
    build_name_cell,
    build_motion_type_cell,
    build_edit_buttons_cell,
    build_tcp_detail,
    build_joint_config_detail,
    build_joint_config_selector,
    MOTION_TYPES,
)

ROW_HEIGHT = 44
_DETAILROW_HEIGHT = 28


class PoseDetailItem(ui.AbstractItem):
    """Child item shown when a PoseItem is expanded (TCP or joint config)."""

    def __init__(self, detail_type: str, parent: "PoseItem"):
        super().__init__()
        self.detail_type = detail_type  # "tcp" or "joint_config"
        self.parent = parent


class PoseItem(ui.AbstractItem):
    def __init__(
        self,
        prim_path: str,
        name: str,
        pose: WSPose,
        visible: bool = True,
        is_ghost_object: bool = False,
    ):
        super().__init__()
        self.prim_path: str = prim_path
        self.name_model = ui.SimpleStringModel(name)
        self.pose: WSPose = pose
        self.pose_model = ui.SimpleStringModel(str(pose))
        self.is_visible: bool = visible
        self.is_ghost_object: bool = is_ghost_object
        self.motion_type: str = MOTION_TYPES[0]
        self.reachable: bool | None = None
        self.planned: bool | None = None
        self.joint_configs: list[list[float]] = []
        self.selected_config_idx: int = 0
        self.ik_loading: bool = False
        self.tcp_name: str | None = None  # per-pose TCP override (None = use global)
        self.blending: dict | None = None  # MotionCommandBlending.to_dict()
        self.limits_override: dict | None = None  # LimitsOverride.to_dict()
        self.cycle_time_s: float | None = (
            None  # per-segment duration from planned trajectory
        )
        # Cached child items to prevent GC by C++ TreeView
        self._detail_children: list[PoseDetailItem] = [
            PoseDetailItem("tcp", self),
            PoseDetailItem("joint_config", self),
            PoseDetailItem("overrides", self),
        ]

    @property
    def selected_joint_config(self) -> list[float] | None:
        if self.joint_configs and self.selected_config_idx < len(self.joint_configs):
            return self.joint_configs[self.selected_config_idx]
        return None

    def update_pose(self, pose: WSPose) -> None:
        self.pose = pose
        self.pose_model.set_value(str(pose))

    def set_name(self, name: str) -> None:
        self.name_model.set_value(name)


class PoseModel(ui.AbstractItemModel):
    def __init__(self) -> None:
        super().__init__()
        self._items: list[PoseItem] = []
        self.collision_free: bool = False

    def get_item_children(self, item=None):
        if item is None:
            return self._items
        if isinstance(item, PoseItem):
            # The first pose is the trajectory start: its joint configuration is
            # mandatory for planning regardless of motion type, so always offer the
            # selector for it.
            is_first = bool(self._items) and item is self._items[0]
            shows_joint_config = (
                self.collision_free or item.motion_type == "PathJointPTP" or is_first
            )
            # Only show overrides detail row when there are actual overrides.
            # Joint config selection otherwise applies to JointPTP and collision-free
            # moves — other motion types ignore the selected config.
            return [
                c
                for c in item._detail_children
                if (
                    c.detail_type != "overrides"
                    or item.blending
                    or item.limits_override
                )
                and (c.detail_type != "joint_config" or shows_joint_config)
            ]
        return []

    def get_item_value_model_count(self, item=None) -> int:
        return 3

    def get_item_value_model(self, item=None, column_id: int = 0):
        if item is None:
            return ui.SimpleStringModel("")
        if isinstance(item, PoseDetailItem):
            return ui.SimpleStringModel("")
        if column_id == 0:
            return item.name_model
        if column_id == 1:
            return item.pose_model
        return ui.SimpleStringModel("")

    def add_pose(
        self,
        prim_path: str,
        name: str,
        pose: WSPose,
        is_ghost_object: bool = False,
        tcp_name: str | None = None,
    ) -> PoseItem:
        item = PoseItem(
            prim_path=prim_path, name=name, pose=pose, is_ghost_object=is_ghost_object
        )
        item.tcp_name = tcp_name
        self._items.append(item)
        self._item_changed(None)
        return item

    def remove_pose(self, prim_path: str) -> None:
        self._items = [i for i in self._items if i.prim_path != prim_path]
        self._item_changed(None)

    def get_item_by_path(self, prim_path: str) -> PoseItem | None:
        for item in self._items:
            if item.prim_path == prim_path:
                return item
        return None

    def get_items_by_path(self, prim_path: str) -> list["PoseItem"]:
        return [item for item in self._items if item.prim_path == prim_path]

    def get_item_index(self, item: PoseItem) -> int:
        try:
            return self._items.index(item)
        except ValueError:
            return -1

    def move_up(self, item: PoseItem) -> None:
        idx = self.get_item_index(item)
        if idx <= 0:
            return
        self._items[idx - 1], self._items[idx] = self._items[idx], self._items[idx - 1]
        self._item_changed(None)

    def move_down(self, item: PoseItem) -> None:
        idx = self.get_item_index(item)
        if idx < 0 or idx >= len(self._items) - 1:
            return
        self._items[idx], self._items[idx + 1] = self._items[idx + 1], self._items[idx]
        self._item_changed(None)

    def clear(self) -> None:
        self._items = []
        self._item_changed(None)

    @property
    def items(self) -> list[PoseItem]:
        return self._items

    def notify_item_changed(self, item: PoseItem | None = None) -> None:
        """Notify the TreeView that an item has changed.

        This is the public API that external code should call instead
        of the inherited ``_item_changed`` private method.
        """
        self._item_changed(item)

    def get_ordered_paths(self) -> list[str]:
        return [item.prim_path for item in self._items]


class PoseDelegate(ui.AbstractItemDelegate):
    def __init__(
        self,
        visibility_fn: Callable[[PoseItem], None],
        remove_fn: Callable[[PoseItem], None],
        copy_fn: Callable[[PoseItem], None],
        move_up_fn: Callable[[PoseItem], None],
        move_down_fn: Callable[[PoseItem], None],
        motion_type_changed_fn: Callable[[PoseItem, str], None] | None = None,
        joint_config_changed_fn: Callable[[PoseItem, int], None] | None = None,
        refresh_ik_fn: Callable[[PoseItem], None] | None = None,
        settings_fn: Callable[[PoseItem], None] | None = None,
        get_selected_tcp: Callable[[], str | None] | None = None,
    ) -> None:
        super().__init__()
        self._visibility_fn = visibility_fn
        self._remove_fn = remove_fn
        self._copy_fn = copy_fn
        self._move_up_fn = move_up_fn
        self._move_down_fn = move_down_fn
        self._motion_type_changed_fn = motion_type_changed_fn
        self._joint_config_changed_fn = joint_config_changed_fn
        self._refresh_ik_fn = refresh_ik_fn
        self._settings_fn = settings_fn
        self._get_selected_tcp = get_selected_tcp
        self._widgets: list = []
        self._subs: list = []
        self._building: bool = False
        self.collision_free: bool = False
        self.edit_mode: bool = False
        self.executing_index: int | None = None

    def _make_triangle(self, collapsed: bool) -> ui.Triangle:
        if collapsed:
            alignment = ui.Alignment.RIGHT_CENTER
            width, height = 5, 7
        else:
            alignment = ui.Alignment.CENTER_BOTTOM
            width, height = 7, 5
        return ui.Triangle(
            style={
                "background_color": NOVAColor.COLLAPSIBLE_SECTION_HEADER_ICON.color,
                "color": NOVAColor.COLLAPSIBLE_SECTION_HEADER_ICON.color,
            },
            width=width,
            height=height,
            alignment=alignment,
        )

    def build_branch(self, model, item, column_id, level, expanded):
        if column_id != 0:
            return
        if isinstance(item, PoseItem):
            with ui.HStack(width=20, height=ROW_HEIGHT):
                ui.Spacer(width=10)
                with ui.VStack(width=16):
                    ui.Spacer()
                    self._make_triangle(not expanded)
                    ui.Spacer()
                ui.Spacer(width=2)
        elif isinstance(item, PoseDetailItem):
            ui.Spacer(width=20)

    def build_widget(self, model, item, column_id, level, expanded):
        if item is None:
            return

        if isinstance(item, PoseDetailItem):
            self._build_detail_widget(model, item, column_id)
            return

        self._building = True
        item_idx = (
            model.get_item_index(item) if hasattr(model, "get_item_index") else -1
        )
        with ui.ZStack(height=ROW_HEIGHT, style=TOOLTIP_STYLE):
            if self.executing_index is not None and item_idx == self.executing_index:
                ui.Rectangle(
                    style={
                        "background_color": 0x3040C4FF,
                        "border_radius": 4,
                        "border_width": 1,
                        "border_color": 0x6040C4FF,
                    }
                )
            elif item.reachable is False or item.planned is False:
                ui.Rectangle(
                    style={
                        "background_color": ui.color("#EF535030"),
                    }
                )
            with ui.HStack(height=ROW_HEIGHT):
                if column_id == 0:
                    default_tcp = (
                        self._get_selected_tcp() if self._get_selected_tcp else None
                    )
                    build_name_cell(
                        name=item.name_model.get_value_as_string(),
                        is_ghost_object=item.is_ghost_object,
                        has_overrides=item.blending is not None
                        or item.limits_override is not None
                        or (item.tcp_name is not None and item.tcp_name != default_tcp),
                        on_settings_clicked=(
                            (lambda i=item: self._settings_fn(i))
                            if self._settings_fn and not self.collision_free
                            else None
                        ),
                        cycle_time_s=item.cycle_time_s,
                        reachable=item.reachable,
                    )
                elif column_id == 1:
                    item_idx = (
                        model.get_item_index(item)
                        if hasattr(model, "get_item_index")
                        else -1
                    )
                    build_motion_type_cell(
                        motion_type=item.motion_type,
                        item_index=item_idx,
                        collision_free=self.collision_free,
                        on_changed=lambda idx, i=item: self._on_motion_type_changed(
                            i, idx
                        ),
                        widgets_out=self._widgets,
                        subs_out=self._subs,
                    )
                elif column_id == 2:
                    if self.edit_mode:
                        item_idx = (
                            model.get_item_index(item)
                            if hasattr(model, "get_item_index")
                            else -1
                        )
                        item_count = len(model.get_item_children(None)) if model else 0
                        build_edit_buttons_cell(
                            item_index=item_idx,
                            item_count=item_count,
                            is_visible=item.is_visible,
                            on_move_up=lambda i=item: self._move_up_fn(i),
                            on_move_down=lambda i=item: self._move_down_fn(i),
                            on_toggle_visibility=lambda i=item: self._visibility_fn(i),
                            on_remove=lambda i=item: self._remove_fn(i),
                            widgets_out=self._widgets,
                        )
                    else:
                        item_idx = (
                            model.get_item_index(item)
                            if hasattr(model, "get_item_index")
                            else -1
                        )
                        # First pose = trajectory start: always show the joint
                        # selector so the start configuration can be chosen,
                        # independent of the motion command type.
                        shows_joint_config = (
                            self.collision_free
                            or item.motion_type == "PathJointPTP"
                            or item_idx == 0
                        )
                        if shows_joint_config:
                            build_joint_config_selector(
                                joint_configs=item.joint_configs,
                                selected_config_idx=item.selected_config_idx,
                                is_ghost_object=item.is_ghost_object,
                                ik_loading=item.ik_loading,
                                prim_path=item.prim_path,
                                on_config_changed=lambda idx, i=item: (
                                    self._on_joint_config_changed(i, idx)
                                ),
                                widgets_out=self._widgets,
                                subs_out=self._subs,
                                row_height=ROW_HEIGHT,
                            )
                ui.Spacer(width=4)
        self._building = False

    def _build_detail_widget(self, model, item: PoseDetailItem, column_id: int) -> None:
        parent = item.parent
        if column_id == 0:
            if item.detail_type == "tcp":
                tcp_label = (
                    parent.tcp_name
                    or (self._get_selected_tcp() if self._get_selected_tcp else None)
                    or "–"
                )
                build_tcp_detail(parent.pose, tcp_label=tcp_label)
            elif item.detail_type == "overrides":
                _build_overrides_detail(parent)
            else:
                build_joint_config_detail(
                    joint_configs=parent.joint_configs,
                    selected_config_idx=parent.selected_config_idx,
                    ik_loading=parent.ik_loading,
                )
        else:
            ui.Spacer(height=_DETAILROW_HEIGHT)

    def _on_motion_type_changed(self, item: PoseItem, idx: int) -> None:
        if self._building:
            return
        if 0 <= idx < len(MOTION_TYPES):
            new_type = MOTION_TYPES[idx]
            if item.motion_type == new_type:
                return
            item.motion_type = new_type
            carb.log_info(
                f"Motion type changed: {item.name_model.get_value_as_string()} \u2192 {new_type}"
            )
            if self._motion_type_changed_fn:
                self._motion_type_changed_fn(item, item.motion_type)

    def _on_joint_config_changed(self, item: PoseItem, idx: int) -> None:
        if self._building:
            return
        if 0 <= idx < len(item.joint_configs):
            if item.selected_config_idx == idx:
                return
            item.selected_config_idx = idx
            carb.log_verbose(
                f"_on_joint_config_changed: idx={idx} item={item.prim_path} building={self._building} callback={'set' if self._joint_config_changed_fn else 'None'}"
            )
            if self._joint_config_changed_fn:
                self._joint_config_changed_fn(item, idx)

    def build_header(self, column_id):
        headers = ["Name", "Type", ""]
        with ui.HStack():
            ui.Spacer(width=4)
            ui.Label(
                headers[column_id] if column_id < len(headers) else "",
                alignment=ui.Alignment.LEFT_CENTER,
                style={"color": NOVAColor.TEXT_SECONDARY.color, "font_size": 13},
            )
            ui.Spacer(width=4)


def _build_overrides_detail(item: PoseItem) -> None:
    """Show override indicators in expanded pose detail."""
    parts = []
    if item.blending:
        parts.append("custom blending")
    if item.limits_override:
        parts.append("limits applied")
    if not parts:
        return
    text = ", ".join(parts)
    with ui.HStack(height=_DETAILROW_HEIGHT, spacing=4):
        ui.Label(
            text,
            alignment=ui.Alignment.LEFT_CENTER,
            style={
                "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                "font_size": 13,
            },
        )
