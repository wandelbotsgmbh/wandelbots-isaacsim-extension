"""Async IK fetching and reachability checking for trajectory planner poses."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable

import carb

if TYPE_CHECKING:
    from wandelbots.omni.ui.tool.trajectory_planner.events import (
        TrajectoryPlannerEvents,
    )
import omni.kit.notification_manager as nm
from omni.kit.async_engine import run_coroutine

from wandelbots.omni.ui.tool.trajectory_planner.pose_tree_widget import (
    PoseItem,
    PoseModel,
)
from wandelbots.omni.ui.tool.trajectory_planner.service import (
    get_trajectory_planner_service,
)
from wandelbots.omni.utils.api import ApiConfiguration
from wandelbots.omni.utils.teaching import GhostObjectUtils

import omni.usd


def _read_preferred_from_prim(item: PoseItem) -> list[float] | None:
    """Read preferred joint values from the USD prim if the item is a ghost object."""
    if not item.is_ghost_object:
        return None
    stage = omni.usd.get_context().get_stage()
    if not stage:
        return None
    prim = stage.GetPrimAtPath(item.prim_path)
    return GhostObjectUtils.get_preferred_joint_values(prim)


class IKManager:
    """Manages async IK fetching and reachability checks for pose items."""

    def __init__(
        self,
        pose_model: PoseModel,
        get_api_config: Callable[[], ApiConfiguration | None],
        get_stream_params: Callable[[], tuple[str, str, str] | None],
        get_selected_tcp: Callable[[], str | None],
        get_collision_setup: Callable[[], str | None],
        events: "TrajectoryPlannerEvents",
        get_tcp_for_item: Callable[[PoseItem], str | None] | None = None,
    ) -> None:
        self._pose_model = pose_model
        self._get_api_config = get_api_config
        self._get_stream_params = get_stream_params
        self._get_selected_tcp = get_selected_tcp
        self._get_collision_setup = get_collision_setup
        self._events = events
        self._get_tcp_for_item = get_tcp_for_item

        self._ik_pending_count: int = 0
        self._ik_task: asyncio.Task | None = None
        self._reachability_task: asyncio.Task | None = None

    @property
    def ik_pending_count(self) -> int:
        return self._ik_pending_count

    def _tcp_for_item(self, item: PoseItem) -> str | None:
        if item.tcp_name:
            return item.tcp_name
        if self._get_tcp_for_item is not None:
            return self._get_tcp_for_item(item)
        return self._get_selected_tcp()

    def _tcp_for_items(self, items: list[PoseItem]) -> str | None:
        tcp_names = {item.tcp_name for item in items if item.tcp_name}
        if len(tcp_names) == 1:
            return next(iter(tcp_names))
        if self._get_tcp_for_item is not None:
            ghost_tcps = {
                self._get_tcp_for_item(item) for item in items if item.is_ghost_object
            } - {None}
            if len(ghost_tcps) == 1:
                return next(iter(ghost_tcps))
        return self._get_selected_tcp()

    def destroy(self) -> None:
        if self._ik_task is not None:
            self._ik_task.cancel()
            self._ik_task = None
        if self._reachability_task is not None:
            self._reachability_task.cancel()
            self._reachability_task = None

    def fetch_ik_for_pose(self, item: PoseItem, *, silent: bool = False) -> None:
        params = self._get_stream_params()
        if not params:
            if not silent:
                nm.post_notification(
                    "Select a motion group first.",
                    duration=3.0,
                    status=nm.NotificationStatus.WARNING,
                )
            carb.log_info(
                f"IK skipped for '{item.name_model.get_value_as_string()}': no motion group selected."
            )
            return
        carb.log_info(f"IK fetch started for '{item.name_model.get_value_as_string()}'")
        carb.log_verbose(f"IK pose={item.pose}, params={params}")
        self._ik_pending_count += 1
        self._notify_progress()
        run_coroutine(self._do_fetch_ik(item))

    def refresh_ik_for_pose(self, item: PoseItem) -> None:
        item.joint_configs = []
        item.selected_config_idx = 0
        self.fetch_ik_for_pose(item)

    def refresh_all_ik(self) -> None:
        params = self._get_stream_params()
        if not params:
            nm.post_notification(
                "Select a motion group first.",
                duration=3.0,
                status=nm.NotificationStatus.WARNING,
            )
            carb.log_info("refresh_all_ik: aborted - no motion group selected.")
            return
        items = self._pose_model.items
        carb.log_info(f"refresh_all_ik: batch IK for {len(items)} poses")
        carb.log_verbose(f"refresh_all_ik: params={params}")
        for item in items:
            item.joint_configs = []
            item.selected_config_idx = 0
            item.ik_loading = True
        self._ik_pending_count = len(items)
        self._notify_progress()
        if self._ik_task is not None:
            self._ik_task.cancel()
        self._ik_task = run_coroutine(self._do_fetch_ik_batch(list(items)))

    def check_reachability(self) -> None:
        items = self._pose_model.items
        if not items:
            nm.post_notification(
                "No poses to check.",
                duration=3.0,
                status=nm.NotificationStatus.INFO,
            )
            return
        if not self._get_stream_params():
            nm.post_notification(
                "Select a motion group first.",
                duration=3.0,
                status=nm.NotificationStatus.WARNING,
            )
            return
        if self._reachability_task is not None:
            self._reachability_task.cancel()
        self._reachability_task = run_coroutine(self._do_check_reachability())

    async def _do_fetch_ik(self, item: PoseItem) -> None:
        api_config = self._get_api_config()
        params = self._get_stream_params()
        if not api_config or not params:
            carb.log_warn(
                f"_do_fetch_ik: aborted for '{item.name_model.get_value_as_string()}' — "
                f"api_config={bool(api_config)}, params={params}"
            )
            item.joint_configs = []
            self._ik_pending_count = max(0, self._ik_pending_count - 1)
            self._notify_progress()
            return

        cell, controller, motion_group = params
        item.ik_loading = True
        tcp = self._tcp_for_item(item)
        carb.log_verbose(
            f"_do_fetch_ik: '{item.name_model.get_value_as_string()}' — "
            f"cell={cell}, controller={controller}, mg={motion_group}, "
            f"tcp={tcp}, collision={self._get_collision_setup()}, "
            f"pose={item.pose.pose[:3]}"
        )

        try:
            service = get_trajectory_planner_service()
            result = await service.fetch_ik(
                api_configuration=api_config,
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                pose=item.pose,
                tcp_name=tcp,
                collision_setup_name=self._get_collision_setup(),
            )
            item.joint_configs = result.joint_configs
            preferred = _read_preferred_from_prim(item)
            if result.joint_configs and preferred:
                idx = GhostObjectUtils.find_preferred_config_index(
                    result.joint_configs, preferred
                )
                item.selected_config_idx = idx if idx is not None else 0
            else:
                item.selected_config_idx = 0
            item.reachable = bool(result.joint_configs)
            carb.log_info(
                f"IK result for '{item.name_model.get_value_as_string()}': "
                f"{len(result.joint_configs)} config(s)"
            )
            carb.log_verbose(
                f"IK configs for '{item.name_model.get_value_as_string()}': {result.joint_configs}"
            )
            if not result.joint_configs:
                nm.post_notification(
                    f"No IK solution for '{item.name_model.get_value_as_string()}'.",
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
        except Exception as exc:
            import traceback

            carb.log_warn(
                f"IK failed for '{item.name_model.get_value_as_string()}': {exc}\n"
                f"{traceback.format_exc()}"
            )
            item.joint_configs = []
            item.reachable = False
            nm.post_notification(
                f"IK failed for '{item.name_model.get_value_as_string()}': no solution found.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
        finally:
            item.ik_loading = False
            self._ik_pending_count = max(0, self._ik_pending_count - 1)
            self._notify_progress()

        self._events.ik_complete.emit(item)

    async def _do_fetch_ik_batch(self, items: list[PoseItem]) -> None:
        api_config = self._get_api_config()
        params = self._get_stream_params()
        if not api_config or not params:
            carb.log_info(
                "_do_fetch_ik_batch: aborted - no api_config or stream params."
            )
            for item in items:
                item.ik_loading = False
                item.joint_configs = []
            self._ik_pending_count = 0
            self._notify_progress()
            return

        cell, controller, motion_group = params

        # Group items by TCP so each group uses the correct tcp_offset
        tcp_groups: dict[str | None, list[PoseItem]] = {}
        for item in items:
            tcp = self._tcp_for_item(item)
            tcp_groups.setdefault(tcp, []).append(item)

        carb.log_info(
            f"_do_fetch_ik_batch: {len(items)} poses in {len(tcp_groups)} TCP group(s): "
            f"{{{', '.join(f'{k!r}: {len(v)}' for k, v in tcp_groups.items())}}}"
        )

        failed_names: list[str] = []

        try:
            service = get_trajectory_planner_service()
            for tcp, group_items in tcp_groups.items():
                group_poses = [item.pose for item in group_items]

                def _make_group_callback(g_items):
                    def _cb(idx: int, result) -> None:
                        it = g_items[idx]
                        it.joint_configs = result.joint_configs
                        preferred = _read_preferred_from_prim(it)
                        if result.joint_configs and preferred:
                            idx = GhostObjectUtils.find_preferred_config_index(
                                result.joint_configs, preferred
                            )
                            it.selected_config_idx = idx if idx is not None else 0
                        else:
                            it.selected_config_idx = 0
                        it.reachable = bool(result.joint_configs)
                        it.ik_loading = False
                        self._ik_pending_count = max(0, self._ik_pending_count - 1)
                        self._notify_progress()
                        if not result.joint_configs:
                            failed_names.append(it.name_model.get_value_as_string())
                        self._events.ik_complete.emit(it)

                    return _cb

                await service.fetch_ik_batch(
                    api_configuration=api_config,
                    cell=cell,
                    controller=controller,
                    motion_group=motion_group,
                    poses=group_poses,
                    tcp_name=tcp,
                    collision_setup_name=self._get_collision_setup(),
                    on_result=_make_group_callback(group_items),
                )

            if failed_names:
                names_preview = ", ".join(failed_names[:5])
                suffix = (
                    f" (+{len(failed_names) - 5} more)" if len(failed_names) > 5 else ""
                )
                nm.post_notification(
                    f"No IK solution for {len(failed_names)} pose(s): {names_preview}{suffix}",
                    duration=6.0,
                    status=nm.NotificationStatus.WARNING,
                )
        except Exception as exc:
            carb.log_warn(f"Batch IK failed: {exc}")
            for item in items:
                if item.ik_loading:
                    item.ik_loading = False
                    item.joint_configs = []
                    item.reachable = False
                    self._events.ik_complete.emit(item)
            self._ik_pending_count = 0
            self._notify_progress()
        finally:
            self._ik_task = None

    async def _do_check_reachability(self) -> None:
        api_config = self._get_api_config()
        params = self._get_stream_params()
        if not api_config or not params:
            return

        cell, controller, motion_group = params
        service = get_trajectory_planner_service()
        items = list(self._pose_model.items)
        reachable_count = 0
        unreachable_count = 0

        # Group by TCP for correct per-pose reachability
        tcp_groups: dict[str | None, list[tuple[int, PoseItem]]] = {}
        for idx, item in enumerate(items):
            tcp = self._tcp_for_item(item)
            tcp_groups.setdefault(tcp, []).append((idx, item))

        def _make_reachability_callback(group_items: list[PoseItem]):
            def _cb(idx: int, result) -> None:
                it = group_items[idx]
                it.reachable = bool(result.joint_configs)
                self._pose_model.notify_item_changed(it)

            return _cb

        try:
            all_results = []
            for tcp, group in tcp_groups.items():
                group_items = [item for _, item in group]
                group_poses = [item.pose for item in group_items]
                results = await service.fetch_ik_batch(
                    api_configuration=api_config,
                    cell=cell,
                    controller=controller,
                    motion_group=motion_group,
                    poses=group_poses,
                    tcp_name=tcp,
                    collision_setup_name=self._get_collision_setup(),
                    on_result=_make_reachability_callback(group_items),
                )
                all_results.extend(results)
            for result in all_results:
                if result.joint_configs:
                    reachable_count += 1
                else:
                    unreachable_count += 1
        except Exception as exc:
            carb.log_warn(f"Reachability check failed: {exc}")
            for item in items:
                item.reachable = False
                self._pose_model.notify_item_changed(item)
            unreachable_count = len(items)

        self._reachability_task = None

        self._events.reachability_complete.emit(reachable_count, unreachable_count)

        if unreachable_count == 0:
            nm.post_notification(
                f"All {reachable_count} poses are reachable.",
                duration=4.0,
                status=nm.NotificationStatus.INFO,
            )
        else:
            nm.post_notification(
                f"{unreachable_count} of {len(items)} poses are NOT reachable (highlighted in red).",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )

    def _notify_progress(self) -> None:
        total = len(self._pose_model.items)
        self._events.ik_progress.emit(self._ik_pending_count, total)
