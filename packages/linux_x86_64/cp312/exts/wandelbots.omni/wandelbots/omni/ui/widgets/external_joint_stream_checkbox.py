import asyncio
import weakref
from typing import TYPE_CHECKING, Optional

import carb
import omni.kit.notification_manager as nm
import omni.ui as ui
from omni.kit.async_engine import run_coroutine
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    get_motion_group_service,
)
from wandelbots.omni.ui.wb_theme import TOOLTIP_RESET, build_tooltip
from wandelbots.omni.ui.widgets.styled_checkbox import styled_checkbox

if TYPE_CHECKING:
    from wandelbots.omni.ui.instances.models.external_joint_stream_model import (
        ExternalJointStreamModel,
    )


class ExternalJointStreamCheckbox(ui.HStack):
    """Checkbox that toggles the external-joint-stream flag for a connected motion group."""

    def __init__(
        self,
        motion_group_config: MotionGroupConfiguration,
        **kwargs,
    ):
        kwargs.setdefault("height", 26)
        super().__init__(**kwargs)

        # Imported lazily: importing the model pulls in the instances package,
        # which imports back into this widgets package and would otherwise form
        # a circular import at module load time.
        from wandelbots.omni.ui.instances.models.external_joint_stream_model import (
            ExternalJointStreamModel,
        )

        self._model = ExternalJointStreamModel(
            motion_group_config.prim_path,
            read_only=True,
        )
        self._update_task: Optional[asyncio.Task] = None
        # Guards the revert in _apply_change from being taken for a user toggle,
        # which would re-issue the failing update and revert again, forever.
        self._reverting = False

        with self:
            ui.Spacer(width=15)
            # The themed tooltip lives on the label: the checkbox below carries flat
            # background_color/color keys that would bleed into the self-drawn
            # build_tooltip popup, so it must not host it.
            ui.Label(
                "Sync with simulation:",
                width=150,
                alignment=ui.Alignment.LEFT_CENTER,
                style=TOOLTIP_RESET,
                tooltip_fn=lambda: build_tooltip(
                    "Enable to sync this motion group with the simulation."
                ),
            )

            ui.Spacer()
            # Wrap the fixed-height checkbox in a VStack with spacers so it centers
            # vertically against the label. The VStack (width pinned, height free)
            # fills the taller row, giving the spacers room to center the 20px box.
            with ui.VStack(width=20):
                ui.Spacer()
                checkbox = styled_checkbox(
                    model=self._model,
                    width=20,
                    height=20,
                )
                ui.Spacer()
            ui.Spacer(width=10)

            prim_path = motion_group_config.prim_path

            # weakref: the model outlives this widget when the row is rebuilt.
            def _on_changed(
                model: "ExternalJointStreamModel", weak_self=weakref.ref(self)
            ):
                widget = weak_self()
                if widget is None or widget._reverting:
                    return
                requested = model.get_value_as_bool()
                carb.log_info(f"use_external_joint_stream changing to: {requested}")
                widget._update_task = run_coroutine(
                    widget._apply_change(prim_path, model, requested)
                )

            checkbox.model.add_value_changed_fn(_on_changed)

    async def _apply_change(
        self, prim_path: str, model: "ExternalJointStreamModel", requested: bool
    ):
        """Push the new flag to the motion group service, reverting on failure.

        The service raises when its connection revalidation fails; without this
        the checkbox kept showing a state that was never applied.
        """
        try:
            await get_motion_group_service().update_motion_group_stream_configuration(
                motion_group_prim_path=prim_path,
                motion_stream_configuration=model.motion_stream_configuration,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            carb.log_error(
                f"Failed to set use_external_joint_stream={requested} for "
                f"{prim_path}: {e}"
            )
            nm.post_notification(
                f"Could not change 'Sync with simulation': {e}",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            self._reverting = True
            try:
                model.set_value(not requested)
            finally:
                self._reverting = False

    @property
    def use_external_joint_stream(self) -> bool:
        return self._model.get_value_as_bool()
