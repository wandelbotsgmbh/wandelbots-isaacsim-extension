"""Trajectory planner service layer.

Exposes :class:`TrajectoryPlannerService` as the public facade,
composed of :class:`IKService` and :class:`ExecutionService`.
Consumers should import from this package rather than from the
individual sub-modules.

Planning functions have moved to :mod:`wandelbots.omni.ui.tool.planner_utils`.
"""

from __future__ import annotations

from wandelbots.omni.utils.api import ApiConfiguration

from .execution_service import ExecutionService
from .helpers import MotionGroupContext, build_global_limits, fetch_motion_group_context
from .ik_service import IKResult, IKService


class TrajectoryPlannerService(IKService, ExecutionService):
    """Unified facade combining IK and execution services.

    Planning has moved to stateless functions in
    :mod:`wandelbots.omni.ui.tool.planner_utils`.
    """

    async def get_motion_group_model(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
        motion_group: str,
    ) -> str | None:
        """Fetch the motion group model name from the motion group description."""
        from wandelbots.omni.utils.api import get_api_client_from_config

        async with get_api_client_from_config(api_configuration) as api_client:
            ctx = await fetch_motion_group_context(
                api_client,
                cell=cell,
                controller=controller,
                motion_group=motion_group,
            )
            return ctx.model_name

    async def get_tcp_offset(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
        motion_group: str,
        tcp_name: str,
    ):
        """Resolve TCP offset pose from the motion group description."""
        from wandelbots.omni.utils.api import get_api_client_from_config

        async with get_api_client_from_config(api_configuration) as api_client:
            ctx = await fetch_motion_group_context(
                api_client,
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                tcp_name=tcp_name,
            )
            return ctx.tcp_offset

    async def fetch_tcp_names(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
        motion_group: str,
    ) -> list[str]:
        """Fetch available TCP names for a motion group."""
        from wandelbots.omni.utils.api import get_api_client_from_config
        import wandelbots_api_client.v2 as wb_v2

        async with get_api_client_from_config(api_configuration) as api_client:
            mg_api = wb_v2.MotionGroupApi(api_client)
            description = await mg_api.get_motion_group_description(
                cell=cell,
                controller=controller,
                motion_group=motion_group,
            )
            return list(description.tcps.keys()) if description.tcps else []

    async def fetch_motion_group_models(
        self,
        api_configuration: ApiConfiguration,
    ) -> list[str]:
        """Fetch available motion group model names from the API."""
        from wandelbots.omni.utils.api import get_api_client_from_config
        import wandelbots_api_client.v2 as wb_v2

        async with get_api_client_from_config(api_configuration) as api_client:
            models_api = wb_v2.MotionGroupModelsApi(api_client)
            return await models_api.get_motion_group_models()


_service = TrajectoryPlannerService()


def get_trajectory_planner_service() -> TrajectoryPlannerService:
    return _service


__all__ = [
    "ExecutionService",
    "IKResult",
    "IKService",
    "MotionGroupContext",
    "TrajectoryPlannerService",
    "build_global_limits",
    "fetch_motion_group_context",
    "get_trajectory_planner_service",
]
