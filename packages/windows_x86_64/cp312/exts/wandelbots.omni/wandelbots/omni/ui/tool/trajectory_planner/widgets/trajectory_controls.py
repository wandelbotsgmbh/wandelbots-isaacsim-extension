"""Trajectory control bar: Calculate IKs -> Plan -> Execute -> Pause/Resume."""

from __future__ import annotations

import weakref
from typing import Callable

import omni.ui as ui

from wandelbots.omni.ui.colors import NOVAColor
from wandelbots.omni.ui.styles import TOOLTIP_STYLE, _TOOLTIP_SUB


class TrajectoryControls:
    """Manages the trajectory action buttons: plan, execute, pause, stop, replan.

    Button layout by state::

        No poses:             [                    ] [Calculate IKs (disabled)]
        Has poses:            [                    ] [Calculate IKs           ]
        All IKs ready:        [                    ] [Plan / Plan *           ]
        Planning:             [                    ] [Cancel                  ]
        Trajectory planned:   [Replan              ] [Execute                ]
        Executing:            [Stop  (red)         ] [Pause                  ]
        Paused:               [Stop  (red)         ] [Resume                 ]
    """

    def __init__(
        self,
        on_calculate_iks: Callable[[], None],
        on_plan: Callable[[], None],
        on_execute: Callable[[], None],
        on_replan: Callable[[], None],
        on_force_stop: Callable[[], None],
        on_start_from_here: Callable[[], None] | None = None,
    ) -> None:
        self._on_calculate_iks = on_calculate_iks
        self._on_plan = on_plan
        self._on_execute = on_execute
        self._replan_callback = on_replan
        self._on_force_stop = on_force_stop
        self._start_from_here_callback = on_start_from_here

        self._action_btn: ui.Button | None = None
        self._replan_btn: ui.Button | None = None
        self._start_from_here_btn: ui.Button | None = None
        self._stop_btn: ui.Button | None = None
        self._collision_free_label: ui.Label | None = None
        self._execution_time_label: ui.Label | None = None

        self._trajectory_planned: bool = False
        self._busy: bool = False
        self._collision_free: bool = False

    def build(self, live_update_widget_fn=None) -> None:
        with ui.VStack(spacing=2, style=TOOLTIP_STYLE):
            with ui.HStack(height=40, spacing=8):
                ui.Spacer(width=5)
                ui.Spacer()
                with ui.HStack(spacing=4, width=0, height=34):
                    self._start_from_here_btn = ui.Button(
                        "Start from here",
                        width=120,
                        height=34,
                        tooltip="Execute the planned trajectory starting at the "
                        "selected pose.",
                        visible=False,
                        clicked_fn=lambda ws=weakref.ref(self): (
                            ws()._on_start_from_here() if ws() else None
                        ),
                        style={
                            "background_color": 0xFF292929,
                            "font_size": 15,
                            ":hovered": {
                                "background_color": NOVAColor.BUTTON_HOVER.color
                            },
                        },
                    )
                    self._replan_btn = ui.Button(
                        "Replan",
                        width=80,
                        height=34,
                        tooltip="Re-plan the trajectory.",
                        visible=False,
                        clicked_fn=lambda ws=weakref.ref(self): (
                            ws()._on_replan() if ws() else None
                        ),
                        style={
                            "background_color": 0xFF292929,
                            "font_size": 15,
                            ":hovered": {
                                "background_color": NOVAColor.BUTTON_HOVER.color
                            },
                        },
                    )
                    if live_update_widget_fn:
                        live_update_widget_fn()
                self._stop_btn = ui.Button(
                    "Stop",
                    width=80,
                    height=34,
                    tooltip="Stop execution and return to planned state.",
                    visible=False,
                    clicked_fn=lambda ws=weakref.ref(self): (
                        ws()._on_stop_clicked() if ws() else None
                    ),
                    style={
                        "Button": {
                            "background_color": NOVAColor.BUTTON_STOP.color,
                            "font_size": 15,
                        },
                        "Button:hovered": {
                            "background_color": NOVAColor.ERROR_DARK.color
                        },
                        **_TOOLTIP_SUB,
                    },
                )
                self._action_btn = ui.Button(
                    "Calculate IKs",
                    width=160,
                    height=34,
                    tooltip="Calculate inverse kinematics for all poses.",
                    clicked_fn=lambda ws=weakref.ref(self): (
                        ws()._on_clicked() if ws() else None
                    ),
                    style={
                        "Button": {
                            "background_color": NOVAColor.PRIMARY_MAIN.color,
                            "font_size": 15,
                        },
                        "Button:hovered": {
                            "background_color": NOVAColor.PRIMARY_LIGHT.color
                        },
                        **_TOOLTIP_SUB,
                    },
                )
                ui.Spacer(width=5)
            with ui.HStack(height=18):
                ui.Spacer(width=5)
                self._execution_time_label = ui.Label(
                    "",
                    visible=False,
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 13,
                    },
                )
                ui.Spacer()
                self._collision_free_label = ui.Label(
                    "* Collision Free" if self._collision_free else "",
                    alignment=ui.Alignment.RIGHT,
                    visible=self._collision_free,
                    style={
                        "color": NOVAColor.TEXT_SECONDARY.color,
                        "font_size": 13,
                    },
                )
                ui.Spacer(width=5)
        self.update()

    def set_collision_free(self, enabled: bool) -> None:
        self._collision_free = enabled
        if self._collision_free_label:
            self._collision_free_label.text = "* Collision Free" if enabled else ""
            self._collision_free_label.visible = enabled

    def set_trajectory_planned(self, planned: bool) -> None:
        self._trajectory_planned = planned
        self.update()
        if not planned and self._execution_time_label:
            self._execution_time_label.visible = False

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if self._action_btn and busy:
            self._action_btn.enabled = True
        elif not busy:
            self.update()

    def show_execution_time(self, duration: float | None) -> None:
        if not self._execution_time_label:
            return
        if duration is not None:
            self._execution_time_label.text = (
                f"Expected execution time: {duration:.2f}s"
            )
            self._execution_time_label.visible = True
        else:
            self._execution_time_label.visible = False

    def set_cancel_label(self) -> None:
        if self._action_btn:
            self._action_btn.text = "Cancel"

    def set_pause_label(self) -> None:
        """Execution is active: show [Stop (red)] [Pause]."""
        if self._action_btn:
            self._action_btn.text = "Pause"
        if self._stop_btn:
            self._stop_btn.visible = True
        self._set_secondary_buttons_visible(False)

    def set_resume_label(self) -> None:
        """Execution is paused: show [Stop (red)] [Resume]."""
        if self._action_btn:
            self._action_btn.text = "Resume"
        if self._stop_btn:
            self._stop_btn.visible = True
        self._set_secondary_buttons_visible(False)

    def update(
        self,
        has_poses: bool | None = None,
        all_iks_ready: bool | None = None,
        has_trajectory: bool | None = None,
        has_motion_group: bool = True,
    ) -> None:
        if not self._action_btn:
            return
        if self._busy:
            return

        _has_trajectory = (
            has_trajectory if has_trajectory is not None else self._trajectory_planned
        )
        # Keep internal state in sync so _on_clicked dispatches correctly.
        self._trajectory_planned = _has_trajectory
        if not _has_trajectory and self._execution_time_label:
            self._execution_time_label.visible = False

        # Always hide the red stop button when returning to a non-execution state.
        if self._stop_btn:
            self._stop_btn.visible = False

        if not has_motion_group:
            self._action_btn.text = "Calculate IKs"
            self._action_btn.tooltip = "Select a motion group first."
            self._action_btn.enabled = False
            self._set_secondary_buttons_visible(False)
            return

        if _has_trajectory:
            self._action_btn.text = "Execute"
            self._action_btn.tooltip = "Execute the planned trajectory on the robot."
            self._action_btn.enabled = True
            self._set_secondary_buttons_visible(True)
        elif all_iks_ready:
            label = "Plan *" if self._collision_free else "Plan"
            self._action_btn.text = label
            self._action_btn.tooltip = "Plan the trajectory via the NOVA API."
            self._action_btn.enabled = True
            self._set_secondary_buttons_visible(False)
        else:
            self._action_btn.text = "Calculate IKs"
            self._action_btn.tooltip = (
                "Calculate inverse kinematics for all poses."
                if has_poses
                else "Add poses first."
            )
            self._action_btn.enabled = bool(has_poses)
            self._set_secondary_buttons_visible(False)

    def _on_clicked(self) -> None:
        if not self._action_btn:
            return
        text = self._action_btn.text
        if text == "Cancel":
            self._on_plan()
        elif text == "Pause":
            self._on_execute()
        elif text == "Resume":
            self._on_execute()
        elif self._trajectory_planned:
            self._on_execute()
        elif text.startswith("Plan"):
            self._on_plan()
        else:
            self._on_calculate_iks()

    def _on_stop_clicked(self) -> None:
        self._on_force_stop()

    def _on_replan(self) -> None:
        self._replan_callback()

    def _on_start_from_here(self) -> None:
        if self._start_from_here_callback:
            self._start_from_here_callback()

    def _set_secondary_buttons_visible(self, visible: bool) -> None:
        """Show/hide the planned-state-only buttons (Replan, Start from here)."""
        if self._replan_btn:
            self._replan_btn.visible = visible
        if self._start_from_here_btn:
            self._start_from_here_btn.visible = visible
