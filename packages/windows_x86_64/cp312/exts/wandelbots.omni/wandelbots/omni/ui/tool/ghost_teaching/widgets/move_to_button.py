import asyncio
import weakref
from typing import Callable

import carb
import omni.ui as ui
from omni.kit.async_engine import run_coroutine
from wandelbots.omni.teaching.move_to_service import (
    MoveToExecuteSettings,
    MoveToState,
    execute_move_to,
)
from wandelbots.omni.ui.colors import NOVAColor


class MoveToButton:
    def __init__(
        self,
        configure_execution_fn: Callable[[], MoveToExecuteSettings | None],
        on_released_fn: Callable[[], None] | None = None,
    ):
        """

        Args:
            configure_execution_fn (Callable[[], MoveToExecuteSettings  |  None]): Configuration may be none if no valid configuration could be created
        """
        self._move_to_task = None
        self._move_to_pressed = False
        self._move_to_state = MoveToState.IDLE
        self._configure_execution_fn = configure_execution_fn
        self._on_released_fn = on_released_fn
        self._stop_event: asyncio.Event | None = None
        self._enabled = True
        self._is_following = False

        self._build_ui()

    def __del__(self):
        self._move_to_pressed = False
        self._stop_move_to()
        if self._move_to_task is not None:
            self._move_to_task.cancel()

    def _build_ui(self):
        self._move_to_pressed = False  # rebuilding the button does not trigger the release event, so we reset the state here
        self._button = ui.Button(
            self._get_state_button_text(),
            height=ui.Fraction(1),
            style={
                "margin": 0,
                "background_color": NOVAColor.PRIMARY_MAIN.color,
                "color": NOVAColor.PRIMARY_CONTRAST_TEXT.color,
                ":hovered": {"background_color": NOVAColor.PRIMARY_DARK.color},
                ":disabled": {"background_color": NOVAColor.DIVIDER.color},
            },
            width=90,
            enabled=self._enabled,
            mouse_pressed_fn=lambda x, y, button, modifier, weak_self=weakref.proxy(self): (
                weak_self._move_to_mouse_pressed(x, y, button, modifier)
            ),
            mouse_released_fn=lambda x, y, button, modifier, weak_self=weakref.proxy(self): (
                weak_self._move_to_mouse_released(x, y, button, modifier)
            ),
        )

    def _get_state_button_text(self) -> str:
        if self._is_following:
            return "Following"
        elif self._move_to_state == MoveToState.IDLE:
            return "Move to"
        elif self._move_to_state == MoveToState.CONFIGURATION:
            return "Configuring..."
        elif self._move_to_state == MoveToState.PLAN:
            return "Planning..."
        elif self._move_to_state == MoveToState.EXECUTE:
            return "Executing..."

    def _set_move_to_state(self, state: MoveToState):
        if state == self._move_to_state:
            return
        self._move_to_state = state
        if self._button:
            self._button.text = self._get_state_button_text()

    def _move_to_mouse_pressed(
        self,
        x: float,
        y: float,
        button: int,
        modifier,
    ):
        if self.enabled is False:
            return

        carb.log_info("Move to...")
        self._move_to_pressed = True
        configuration = self._configure_execution_fn()
        if not configuration:
            return
        self._move_to_task = run_coroutine(
            self._execute_planning_configuration(configuration)
        )

        def on_done(future: asyncio.Future):
            try:
                future.result()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                carb.log_error(f"Error during move to ghost object: {str(e)}")
            self._set_move_to_state(MoveToState.IDLE)
            self._stop_event = None

        self._move_to_task.add_done_callback(on_done)

    def _move_to_mouse_released(
        self,
        x: float,
        y: float,
        button: int,
        modifier,
    ):
        self._move_to_pressed = False
        self._stop_move_to()
        if self._on_released_fn:
            self._on_released_fn()

    def _stop_move_to(self):
        if self._stop_event is not None:
            self._stop_event.set()
        if self._move_to_task is not None:
            self._move_to_task.cancel()

    async def _execute_planning_configuration(
        self, configuration: MoveToExecuteSettings
    ):
        self._stop_event = asyncio.Event()

        def on_state_change(
            state: MoveToState,
            weak_self: Callable[[], MoveToButton] = weakref.ref(self),
        ):
            self_instance = weak_self()
            if self_instance is None:
                return
            self_instance._set_move_to_state(state)

        def on_stopped(weak_self: Callable[[], MoveToButton] = weakref.ref(self)):
            self_instance = weak_self()
            if self_instance is None:
                return
            carb.log_info("Move to stopped")

        try:
            await execute_move_to(
                configuration,
                stop_event=self._stop_event,
                on_state_change=on_state_change,
                on_stopped=on_stopped,
            )
        finally:
            self._set_move_to_state(MoveToState.IDLE)
            self._stop_event = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
        if self._button:
            self._button.enabled = value

    @property
    def is_following(self) -> bool:
        return self._is_following

    @is_following.setter
    def is_following(self, value: bool):
        self._is_following = value
        if self._button:
            self._button.text = self._get_state_button_text()
