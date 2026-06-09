"""IK service methods for the trajectory planner."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

import carb
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.datatypes import WSPose
from wandelbots.omni.utils.api import ApiConfiguration, get_api_client_from_config

from .helpers import (
    _REQUEST_TIMEOUT,
    fetch_motion_group_context,
)


@dataclass
class IKResult:
    joint_configs: list[list[float]]
    error: str | None = None


class IKService:
    """Inverse kinematics operations for the trajectory planner."""

    async def fetch_ik(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
        motion_group: str,
        pose: WSPose,
        tcp_name: str | None = None,
        collision_setup_name: str | None = None,
    ) -> IKResult:
        """Compute inverse kinematics for a single pose."""
        async with get_api_client_from_config(api_configuration) as api_client:
            ctx = await fetch_motion_group_context(
                api_client,
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                tcp_name=tcp_name,
                collision_setup_name=collision_setup_name,
                include_joint_limits=True,
            )

            nova_pose = pose.to_nova_pose()
            ik_request = wb_v2_models.InverseKinematicsRequest(
                motion_group_model=ctx.model_name,
                tcp_poses=[nova_pose],
                tcp_offset=ctx.tcp_offset,
                mounting=ctx.description.mounting,
                joint_position_limits=ctx.joint_position_limits,
                collision_setups=ctx.collision_setups,
            )
            ik_api = wb_v2.KinematicsApi(api_client)
            carb.log_verbose(
                f"fetch_ik: sending IK request — "
                f"model={ctx.model_name}, "
                f"has_tcp_offset={ctx.tcp_offset is not None}, "
                f"has_collision={ctx.collision_setups is not None}, "
                f"has_joint_limits={ctx.joint_position_limits is not None}"
            )
            response = await ik_api.inverse_kinematics(
                cell=cell,
                inverse_kinematics_request=ik_request,
                _request_timeout=_REQUEST_TIMEOUT,
            )
            if response.joints and response.joints[0]:
                carb.log_verbose(f"fetch_ik: got {len(response.joints[0])} config(s)")
                return IKResult(joint_configs=response.joints[0])
            carb.log_verbose("fetch_ik: no IK solutions returned")
            return IKResult(joint_configs=[])

    async def fetch_ik_batch(
        self,
        api_configuration: ApiConfiguration,
        cell: str,
        controller: str,
        motion_group: str,
        poses: list[WSPose],
        tcp_name: str | None = None,
        collision_setup_name: str | None = None,
        on_result: Callable[[int, IKResult], None] | None = None,
    ) -> list[IKResult]:
        """Compute IK for multiple poses concurrently.

        Fetches the motion group description and collision setup once,
        then fires all IK requests in parallel.  Calls *on_result(index,
        result)* as each completes so the caller can update the UI
        incrementally.
        """
        async with get_api_client_from_config(api_configuration) as api_client:
            ctx = await fetch_motion_group_context(
                api_client,
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                tcp_name=tcp_name,
                collision_setup_name=collision_setup_name,
                include_joint_limits=True,
            )

            ik_api = wb_v2.KinematicsApi(api_client)
            carb.log_verbose(
                f"fetch_ik_batch: {len(poses)} poses, tcp={tcp_name}, "
                f"model={ctx.model_name}, "
                f"has_collision={ctx.collision_setups is not None}"
            )

            async def _single_ik(idx: int, pose: WSPose) -> tuple[int, IKResult]:
                nova_pose = pose.to_nova_pose()
                ik_request = wb_v2_models.InverseKinematicsRequest(
                    motion_group_model=ctx.model_name,
                    tcp_poses=[nova_pose],
                    tcp_offset=ctx.tcp_offset,
                    mounting=ctx.description.mounting,
                    joint_position_limits=ctx.joint_position_limits,
                    collision_setups=ctx.collision_setups,
                )
                try:
                    response = await ik_api.inverse_kinematics(
                        cell=cell,
                        inverse_kinematics_request=ik_request,
                        _request_timeout=_REQUEST_TIMEOUT,
                    )
                    if response.joints and response.joints[0]:
                        result = IKResult(joint_configs=response.joints[0])
                    else:
                        result = IKResult(joint_configs=[])
                except Exception as exc:
                    carb.log_verbose(f"fetch_ik_batch[{idx}]: failed — {exc}")
                    result = IKResult(joint_configs=[], error=str(exc))
                if on_result:
                    on_result(idx, result)
                return idx, result

            tasks = [_single_ik(i, p) for i, p in enumerate(poses)]
            completed = await asyncio.gather(*tasks, return_exceptions=True)

            results: list[IKResult] = [IKResult(joint_configs=[])] * len(poses)
            for entry in completed:
                if isinstance(entry, Exception):
                    carb.log_warn(f"IK batch task failed: {entry}")
                    continue
                idx, result = entry
                results[idx] = result
            return results
