import asyncio
import carb
import omni.ui as ui
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    get_motion_group_service,
)
from wandelbots.omni.ui.colors import NOVAColor
from ..models.external_joint_stream_model import ExternalJointStreamModel


class ExternalJointStreamCheckbox(ui.HStack):
    """Checkbox that toggles the external-joint-stream flag for a connected motion group."""

    def __init__(
        self,
        motion_group_config: MotionGroupConfiguration,
        **kwargs,
    ):
        kwargs.setdefault("height", 20)
        super().__init__(**kwargs)

        self._model = ExternalJointStreamModel(
            motion_group_config.prim_path,
            read_only=True,
        )

        with self:
            ui.Spacer(width=15)
            ui.Label("Sync with simulation:", width=150)

            checkbox = ui.CheckBox(
                width=20,
                height=20,
                model=self._model,
                style={
                    "background_color": NOVAColor.SECONDARY_TONAL.color,
                    "color": NOVAColor.SECONDARY_CONTRAST_TEXT.color,
                },
                tooltip="Enable to sync this motion group with the simulation.",
            )

            prim_path = motion_group_config.prim_path

            def _on_changed(model: ExternalJointStreamModel):
                carb.log_info(
                    f"use_external_joint_stream changing to: {model.get_value_as_bool()}"
                )
                asyncio.get_event_loop().create_task(
                    get_motion_group_service().update_motion_group_stream_configuration(
                        motion_group_prim_path=prim_path,
                        motion_stream_configuration=model.motion_stream_configuration,
                    )
                )

            checkbox.model.add_value_changed_fn(_on_changed)

    @property
    def use_external_joint_stream(self) -> bool:
        return self._model.get_value_as_bool()
