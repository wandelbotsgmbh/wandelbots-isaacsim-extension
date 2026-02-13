import asyncio
import carb
import carb.events
import time
import weakref
from typing import Callable, Optional
import omni.kit.app
from omni.kit.async_engine import run_coroutine
from omni.usd import get_watcher
import omni.kit.notification_manager as nm
from wandelbots.omni.teaching.move_to_service import (
    execute_move_to,
    MoveToExecuteSettings,
    MoveToState,
)


class GhostTeachingFollowService:
    """Service that follows a ghost object using USD watcher."""

    def __init__(
        self,
        prim_path: str,
        configure_execution_fn: Callable[[], Optional[MoveToExecuteSettings]],
        delay_seconds: float = 0.5,
    ):
        """
        Args:
            prim_path: Path to the ghost object prim to follow
            configure_execution_fn: Callback to get the current execution configuration
            delay_seconds: Delay in seconds before executing after ghost object stops moving
        """
        self._prim_path = prim_path
        self._configure_execution_fn = configure_execution_fn
        self._delay_seconds = delay_seconds
        self._is_running = False
        self._is_executing = False
        self._last_change_time: float = 0
        self._pending_follow = False
        self._usd_watcher = None
        self._update_subscription = None
        self._follow_task = None

    @property
    def prim_path(self) -> str:
        """The prim path of the ghost object being followed."""
        return self._prim_path

    def start(self):
        """Start following the ghost object."""
        carb.log_info(f"Starting GhostTeachingFollowService for {self._prim_path}")
        self._is_running = True

        # Watch for ghost object changes
        self._usd_watcher = get_watcher().subscribe_to_change_info_path(
            self._prim_path,
            self._on_ghost_object_changed,
        )

        if self._update_subscription is None:
            self._update_subscription = (
                omni.kit.app.get_app()
                .get_update_event_stream()
                .create_subscription_to_pop(
                    lambda event, weak_self=weakref.proxy(self): weak_self._on_update(
                        event
                    ),
                    name="GhostTeachingFollowService_update",
                )
            )

        # Execute immediately when follow mode is enabled
        carb.log_info("Follow mode enabled, executing initial move")
        self._execute_follow()

    def stop(self):
        """Stop following the ghost object."""
        carb.log_info(f"Stopping GhostTeachingFollowService for {self._prim_path}")
        self._is_running = False
        self._is_executing = False
        self._pending_follow = False
        self._unsubscribe_usd_watcher()
        self._unsubscribe_update_subscription()
        if self._follow_task is not None:
            self._follow_task.cancel()
            self._follow_task = None

    def destroy(self):
        """Cleanup all resources."""
        self.stop()

    def _unsubscribe_usd_watcher(self):
        if self._usd_watcher is None:
            return
        try:
            self._usd_watcher.unsubscribe()
        except Exception as e:
            carb.log_warn(f"Failed to unsubscribe USD watcher: {str(e)}")
        finally:
            self._usd_watcher = None

    def _unsubscribe_update_subscription(self):
        if self._update_subscription is None:
            return
        try:
            self._update_subscription.unsubscribe()
        except Exception as e:
            carb.log_warn(f"Failed to unsubscribe update stream: {str(e)}")
        finally:
            self._update_subscription = None

    def _on_ghost_object_changed(self, path=None):
        """Called when the ghost object transform changes."""
        if not self._is_running:
            carb.log_verbose("Ghost object changed but service not running")
            return

        # Update last change time to invalidate older pending timers
        self._last_change_time = time.time()

        carb.log_verbose(
            f"Ghost object changed at {self._prim_path}, scheduling execution after {self._delay_seconds}s"
        )

        self._pending_follow = True

    def _on_update(self, event: carb.events.IEvent):
        if not self._is_running or not self._pending_follow:
            return

        if self._is_executing:
            return

        time_since_last_change = time.time() - self._last_change_time

        if time_since_last_change >= self._delay_seconds:
            carb.log_info("Conditions met, executing follow")
            self._pending_follow = False
            self._execute_follow()

    def _execute_follow(self):
        """Execute the follow action - moves to the ghost object target."""
        # Don't start if already executing
        if self._is_executing:
            carb.log_verbose("Already executing, skipping new follow request")
            return

        carb.log_info(f"Following target at {self._prim_path}")

        configuration = self._configure_execution_fn()
        if not configuration:
            carb.log_warn(
                "No valid configuration for follow execution - cannot execute"
            )
            nm.post_notification(
                "Cannot follow ghost object - invalid configuration",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return

        self._is_executing = True
        self._follow_task = run_coroutine(self._execute_follow_async(configuration))

        def on_done(future: asyncio.Future):
            try:
                future.result()
            except asyncio.CancelledError:
                carb.log_verbose("Follow task was cancelled")
            except Exception as e:
                carb.log_error(f"Error during follow execution: {str(e)}")
            finally:
                self._follow_task = None
            self._is_executing = False

        self._follow_task.add_done_callback(on_done)

    async def _execute_follow_async(self, configuration: MoveToExecuteSettings):
        """Execute the move to the ghost object target asynchronously."""

        def on_stopped():
            carb.log_info("Follow motion stopped")

        def on_state_change(state: MoveToState):
            carb.log_info(f"Follow state changed to: {state.name}")

        def on_motion_start():
            carb.log_info("Follow motion started")

        try:
            success = await execute_move_to(
                configuration,
                continue_fn=lambda: self._is_running,
                on_stopped=on_stopped,
                on_state_change=on_state_change,
                on_motion_start=on_motion_start,
                stop_on_standstill=True,
            )

            if not success:
                carb.log_info("The ghost object cannot be reached by the robot.")
        except Exception as e:
            carb.log_error(f"Unexpected error in follow execution: {str(e)}")
