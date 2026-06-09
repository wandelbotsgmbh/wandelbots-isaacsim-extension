from pxr import Usd
import wandelbots.usd as wb_schema
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.manipulators import (
    get_motion_group_configuration_from_prim,
)
from wandelbots.omni.utils.api import get_api_client_from_config
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_models
from wandelbots.omni.instances.instances_service import NOVAInstancesService
import omni.kit.notification_manager as nm


def can_create_mounting_from_payload(payload: dict) -> bool:
    prim_list: list[Usd.Prim] = payload.get("prim_list", [])
    if len(prim_list) == 0 or len(prim_list) > 1:
        return False
    robot_prim = prim_list[0]
    if not robot_prim.HasAPI(wb_schema.MotionGroupAPI):
        return False
    return True


async def create_nova_mounting_from_payload(payload: dict):
    prim_list: list[Usd.Prim] = payload.get("prim_list", [])
    robot_prim = prim_list[0]

    motion_group_config = get_motion_group_configuration_from_prim(robot_prim)
    host = motion_group_config.motion_stream_configuration.host

    instance = NOVAInstancesService().find_instance_by_host(host)
    if instance is None:
        nm.post_notification(
            "No connected NOVA instances found. Please connect to a NOVA instance first.",
            duration=5.0,
            status=nm.NotificationStatus.WARNING,
        )
        return
    if not instance.is_reachable:
        nm.post_notification(
            "The connected NOVA instance is not reachable. Please check your connection.",
            duration=5.0,
            status=nm.NotificationStatus.WARNING,
        )
        return

    prim_pose_world = PrimUtils.get_prim_pose(robot_prim.GetPath())
    async with get_api_client_from_config(
        motion_group_config.motion_stream_configuration.get_api_configuration()
    ) as api_client:
        virtual_controller_api = wb_v2.VirtualControllerApi(api_client)
        await virtual_controller_api.set_virtual_controller_mounting(
            cell=motion_group_config.motion_stream_configuration.cell,
            controller=motion_group_config.motion_stream_configuration.controller,
            motion_group=motion_group_config.motion_stream_configuration.motion_group,
            coordinate_system=wb_models.CoordinateSystem(
                coordinate_system=robot_prim.GetPath().pathString,
                reference_coordinate_system="world",
                position=list(prim_pose_world.pose[:3]),
                orientation=list(prim_pose_world.pose[3:]),
                orientation_type=wb_models.OrientationType.ROTATION_VECTOR,
            ),
        )
    nm.post_notification(
        "NOVA mounting successfully created.",
        duration=5.0,
        status=nm.NotificationStatus.INFO,
    )
    pass
