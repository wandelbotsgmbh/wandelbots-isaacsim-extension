from __future__ import annotations

from typing import Callable, Optional

import omni.ui as ui

from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import (
    NOVAControllerData,
    NOVAInstance,
    NOVAMotionGroupData,
)
from wandelbots.omni.instances.stage_discovery import get_prim_model_name
from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.instances.articulations.motion_group_section import (
    MotionGroupSection,
)


class AssignedArticulations(ui.VStack):
    """Connected motion-group sections for one instance, rendered inline in its body.

    The "Assigned Articulations" collapsible now lives once at the main-window
    level wrapping all instance groups, so this widget no longer renders its own
    frame - only the per-motion-group sections directly into the instance body.
    """

    def __init__(
        self,
        instance: NOVAInstance,
        instances_service: NOVAInstancesService,
        on_connection_changed: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        kwargs.setdefault("height", 0)
        super().__init__(**kwargs)

        self._instance = instance
        self._instances_service = instances_service
        self._on_connection_changed = on_connection_changed
        self._motion_group_sections: list[MotionGroupSection] = []

        self._build()

    def destroy(self):
        for section in self._motion_group_sections:
            section.destroy()
        self._motion_group_sections.clear()

    def _build(self):
        motion_groups = self.collect_all_groups(self._instances_service, self._instance)

        # The "Assigned Articulations" collapsible now lives once at the main-window
        # level wrapping all instances, so this per-instance widget renders only the
        # motion-group rows directly into the instance body.
        with self:
            with ui.VStack(spacing=2):
                for controller, mg in motion_groups:
                    mg_section = MotionGroupSection(
                        instances_service=self._instances_service,
                        instance=self._instance,
                        controller=controller,
                        motion_group=mg,
                        on_connection_changed=self._on_connection_changed,
                    )
                    self._motion_group_sections.append(mg_section)
                if not motion_groups:
                    with ui.HStack():
                        ui.Spacer(width=15)
                        ui.Label(
                            "No motion groups available",
                            style={"color": NOVAColor.TEXT_SECONDARY.color},
                        )
                ui.Spacer()

    @staticmethod
    def collect_all_groups(
        instances_service: NOVAInstancesService,
        instance: NOVAInstance,
    ) -> list[tuple[NOVAControllerData, NOVAMotionGroupData]]:
        # Every motion group the instance exposes (or, when unreachable, its
        # stage-configured cells) gets a section, regardless of whether it is yet
        # connected to a stage articulation. MotionGroupSection renders a
        # quick-connect button for the not-yet-connected ones.
        groups: list[tuple[NOVAControllerData, NOVAMotionGroupData]] = []
        covered_prim_paths: set[str] = set()

        for cell in instance.cells or []:
            for controller in cell.controllers or []:
                for mg in controller.motion_groups or []:
                    groups.append((controller, mg))
                    # No secured= filter: identity is the normalized host
                    # alone (same rule as NOVAInstance.owns_connection).
                    connected = instances_service.find_connected_motion_group_by(
                        host=instance.host,
                        cell=controller.cell_name,
                        controller=controller.name,
                        motion_group=mg.name,
                    )
                    covered_prim_paths.update(c.prim_path for c in connected)

        # Keep connections the instance does not report: a prim connected to this
        # instance earlier whose controller is missing from instance.cells. While
        # the instance is unreachable or still loading the absence cannot be
        # trusted; on a reachable instance the controller was deleted server-side
        # and MotionGroupSection offers to create it again.
        for config in instances_service.find_connected_motion_group_by(
            host=instance.host,
        ):
            if config.prim_path in covered_prim_paths:
                continue
            stream = config.motion_stream_configuration
            mg_name = stream.motion_group or config.name or ""
            controller = NOVAControllerData(
                name=stream.controller or "",
                cell_name=stream.cell or "",
                motion_groups=[],
            )
            motion_group = NOVAMotionGroupData(
                name=mg_name,
                motion_group_model_name=get_prim_model_name(config.prim_path)
                or mg_name,
            )
            groups.append((controller, motion_group))
            covered_prim_paths.add(config.prim_path)

        return groups
