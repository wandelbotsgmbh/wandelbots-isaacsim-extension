from __future__ import annotations

from typing import Callable, Optional

import carb
import omni.ui as ui
from pxr import Usd

from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import NOVAInstance
from wandelbots.omni.manipulators.utils import get_scene_motion_group_prim_paths
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection

from wandelbots.omni.ui.instances.articulations.unassigned_articulation_row import (
    UnassignedArticulationRow,
)


class UnassignedArticulations(ui.VStack):
    """Top-level "Unassigned Articulations" section in the main window.

    The articulation set is global (all unconnected scene robots); each row picks
    its target instance. Enforces single-expanded-row: when one row opens, others
    collapse.
    """

    def __init__(
        self,
        instances: list[NOVAInstance],
        instances_service: NOVAInstancesService,
        on_created: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._instances = instances
        self._instances_service = instances_service
        self._on_created = on_created
        self._rows: list[UnassignedArticulationRow] = []
        self._expanded_row: Optional[UnassignedArticulationRow] = None
        # True when there is nothing to assign, so the main window can hide the
        # whole region (section + its separating divider).
        self.is_empty = True

        self._build()

    def destroy(self):
        for row in self._rows:
            row.destroy()
        self._rows.clear()
        self._expanded_row = None

    def _build(self):
        unassigned_prims = self._get_unassigned_prims()
        # The section is only meaningful while there is something to assign, so
        # render nothing when empty (the main window hides the whole region).
        if not unassigned_prims:
            return
        self.is_empty = False

        with self:
            section = CollapsibleSection(
                title=f"Unassigned Articulations ({len(unassigned_prims)})",
                collapsed=True,
                header_color=NOVAColor.SURFACE_TRANSPARENT,
                header_hover_color=NOVAColor.SURFACE_OVERLAY,
                title_color=NOVAColor.WARNING_LIGHT,
                title_icon="warning.svg",
            )
            with section.body:
                with ui.VStack(spacing=2):
                    for prim in unassigned_prims:
                        row = UnassignedArticulationRow(
                            prim=prim,
                            instances=self._instances,
                            instances_service=self._instances_service,
                            on_expand=lambda r, _self=self: _self._on_row_expanded(r),
                            on_created=self._on_created,
                        )
                        self._rows.append(row)

    def _on_row_expanded(self, row: UnassignedArticulationRow):
        if self._expanded_row and self._expanded_row is not row:
            self._expanded_row.collapse()
        self._expanded_row = row

    def _get_unassigned_prims(self) -> list[Usd.Prim]:
        try:
            import isaacsim.core.utils.stage as stage_utils

            stage = stage_utils.get_current_stage()
            if not stage:
                return []

            # Discover robots via MotionGroupAPI or ArticulationRootAPI. The API may
            # live on a descendant (e.g. a root_joint carrying ArticulationRootAPI),
            # so each hit is resolved up to the robot prim that holds the
            # motion_group_name custom data used as the display name.
            all_prim_paths = get_scene_motion_group_prim_paths(
                include_prims_without_api=True
            )

            unassigned_prims: list[Usd.Prim] = []
            seen_paths: set[str] = set()
            for path in all_prim_paths:
                prim = stage.GetPrimAtPath(path)
                if not prim.IsValid():
                    continue
                display_prim = self._resolve_display_prim(prim)
                display_path = display_prim.GetPrimPath().pathString
                if display_path in seen_paths:
                    continue
                seen_paths.add(display_path)
                # Unassigned is the complement of assigned: every prim with
                # either API, unless a known reachable instance owns it. Foreign
                # or unreachable hosts stay here so they can be (re)assigned.
                connected = self._instances_service.find_connected_motion_group_by(
                    prim_path=display_path
                )
                if connected and any(
                    self._is_assigned_to_known_instance(c) for c in connected
                ):
                    continue
                unassigned_prims.append(display_prim)
            return unassigned_prims
        except Exception as e:
            carb.log_warn(f"[Unassigned] Error finding prims: {e}")
            return []

    def _is_assigned_to_known_instance(self, config) -> bool:
        # Assigned only when a known instance owns the connection AND still
        # reports the cell/controller/motion group. Foreign hosts and
        # server-side-deleted controllers fall through to the unassigned section
        # so the prim can be re-assigned. Ownership goes through
        # owns_connection - the same check the header and the row use.
        stream = config.motion_stream_configuration
        for instance in self._instances:
            if instance.owns_connection(config):
                return instance.has_live_motion_group(
                    stream.cell, stream.controller, stream.motion_group
                )
        return False

    def _resolve_display_prim(self, prim: Usd.Prim) -> Usd.Prim:
        # The motion_group_name custom data lives on the robot's root prim, while the
        # discovered API may sit on a descendant (e.g. a root_joint). Walk up to the
        # nearest ancestor carrying that custom data so the row is labelled and
        # connected via the robot prim; fall back to the API prim if none is found.
        current = prim
        while current and current.IsValid():
            if current.GetCustomData().get("motion_group_name"):
                return current
            current = current.GetParent()
        return prim
