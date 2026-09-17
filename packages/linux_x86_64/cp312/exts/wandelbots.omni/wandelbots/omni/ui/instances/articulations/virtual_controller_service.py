from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

import carb
import omni.kit.notification_manager as nm
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_models
from wandelbots_api_client.v2.exceptions import NotFoundException
from pxr import Usd

from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import NOVAControllerData, NOVAInstance
from wandelbots.omni.instances.stage_discovery import find_robot_prim
from wandelbots.omni.utils.api import get_api_client
from wandelbots.omni.utils.prims import PrimUtils

# Upper bound for NOVA SDK calls issued from the connect UI. The bundled client
# defaults to a 300 s per-request timeout and exposes no Configuration override,
# so every call here is wrapped in asyncio.wait_for to keep an unreachable
# instance from stalling the UI. Matches the reachability service's 5 s cap.
_API_TIMEOUT_S = 5.0
# Virtual controller creation involves several sequential NOVA API calls, so
# cap the whole flow rather than letting it hang on a stalled backend.
_CREATE_TIMEOUT_S = 60.0
# The motion group only appears a moment after the controller is created, so the
# first connect attempts can legitimately fail; retry a few times before giving up.
_CONNECT_RETRIES = 5
_CONNECT_RETRY_DELAY_S = 2.0


@dataclass
class TcpDefinition:
    name: str
    position: list[float]
    orientation: list[float]


class ControllerAlreadyExistsError(Exception):
    """Raised when a robot controller with the given name already exists."""

    def __init__(self, controller_name: str, cell: str):
        self.controller_name = controller_name
        self.cell = cell
        super().__init__(
            f"A controller named '{controller_name}' already exists in cell '{cell}'."
        )


ProgressCallback = Callable[[float, str], None]


def normalize_name(name: str) -> str:
    return name.lower().replace("-", "").replace("_", "").replace(" ", "")


def resolve_controller_type(model_name: str, available_types: list[str]) -> str | None:
    normalized = normalize_name(model_name)
    carb.log_info(
        f"Resolving controller type for '{model_name}' (normalized: '{normalized}') "
        f"against available types: {available_types}"
    )
    for type_str in available_types:
        if normalize_name(type_str) == normalized:
            return type_str
    return None


async def fetch_cells(instance: NOVAInstance) -> list[str]:
    api = get_instances_api()
    token = api.get_auth_token_from_host(instance.host)
    try:
        async with get_api_client(
            host=instance.host,
            secure=instance.is_secure_connection,
            token=token,
        ) as api_client:
            cell_api = wb_v2.CellApi(api_client=api_client)
            return await asyncio.wait_for(cell_api.list_cells(), timeout=_API_TIMEOUT_S)
    except Exception as e:
        carb.log_warn(f"Failed to fetch cells: {e}")
        return []


async def fetch_robot_configurations(instance: NOVAInstance) -> list[str]:
    api = get_instances_api()
    token = api.get_auth_token_from_host(instance.host)
    try:
        async with get_api_client(
            host=instance.host,
            secure=instance.is_secure_connection,
            token=token,
        ) as api_client:
            robot_configs_api = wb_v2.RobotConfigurationsApi(api_client=api_client)
            return await asyncio.wait_for(
                robot_configs_api.get_robot_configurations(), timeout=_API_TIMEOUT_S
            )
    except Exception as e:
        carb.log_warn(f"Failed to fetch robot configurations: {e}")
        return []


_configuration_for_model_cache: dict[tuple[str, str], str] = {}


async def fetch_configuration_for_motion_group(
    instance: NOVAInstance, model_name: str
) -> str | None:
    """Resolve the robot-configuration id containing this motion group model.

    Returns None when no configuration contains the model (404), or on any
    other failure - including an older NOVA server that predates this
    endpoint entirely - so the caller can fall back to the manual
    Manufacturer/Controller Type combos. Successful lookups are cached per
    (host, model name) - the endpoint is instance-specific and optional, so a
    hit on one NOVA host must not be reused for another, older or differently
    configured, host; failures are never cached, so a transient error or an
    endpoint that only becomes available after a server upgrade is retried on
    the next attempt.
    """
    # Display model names may carry spaces (see is_robot_model below);
    # normalize back to the canonical underscore form the endpoint expects.
    normalized_model_name = model_name.replace(" ", "_")
    cache_key = (instance.host, normalized_model_name)
    if cache_key in _configuration_for_model_cache:
        return _configuration_for_model_cache[cache_key]

    api = get_instances_api()
    token = api.get_auth_token_from_host(instance.host)
    try:
        async with get_api_client(
            host=instance.host,
            secure=instance.is_secure_connection,
            token=token,
        ) as api_client:
            models_api = wb_v2.MotionGroupModelsApi(api_client)
            config = await asyncio.wait_for(
                models_api.get_configuration_for_motion_group(
                    motion_group_model=normalized_model_name
                ),
                timeout=_API_TIMEOUT_S,
            )
    except NotFoundException:
        return None
    except Exception as e:
        carb.log_warn(f"Failed to resolve configuration for '{model_name}': {e}")
        return None

    _configuration_for_model_cache[cache_key] = config.robot_configuration
    return config.robot_configuration


_ROBOT_MIN_ROTARY_JOINTS = 6
_robot_model_cache: dict[tuple[str, str], bool] = {}


async def is_robot_model(instance: NOVAInstance, model_name: str) -> bool:
    """True if the motion group model is a robot, False if an external axis.

    A model counts as a robot when its kinematic chain has at least six rotary
    (revolute) joints; otherwise it is treated as an external axis. Results are
    cached per (host, model name) - the kinematic model an instance serves for a
    given name is its own, so a hit on one NOVA host must not be reused for
    another, older or differently configured, host. Failures are never cached:
    the request raises out of here, so a transient error or an endpoint that
    only becomes available after a server upgrade is retried on the next
    attempt.
    """
    # Assigned motion groups carry a display model name with spaces (the API
    # identifier has its underscores swapped for readability), so normalize back
    # to the canonical underscore form the kinematics endpoint expects.
    model_name = model_name.replace(" ", "_")
    cache_key = (instance.host, model_name)
    if cache_key in _robot_model_cache:
        return _robot_model_cache[cache_key]

    api = get_instances_api()
    token = api.get_auth_token_from_host(instance.host)
    async with get_api_client(
        host=instance.host,
        secure=instance.is_secure_connection,
        token=token,
    ) as api_client:
        models_api = wb_v2.MotionGroupModelsApi(api_client)
        kinematic_model = await asyncio.wait_for(
            models_api.get_motion_group_kinematic_model(motion_group_model=model_name),
            timeout=_API_TIMEOUT_S,
        )

    rotary_joints = sum(
        1
        for joint in (kinematic_model.dh_parameters or [])
        if joint.type == wb_models.JointTypeEnum.REVOLUTE_JOINT
    )
    is_robot = rotary_joints >= _ROBOT_MIN_ROTARY_JOINTS
    _robot_model_cache[cache_key] = is_robot
    return is_robot


async def create_virtual_controller(
    instance: NOVAInstance,
    cell: str,
    controller_name: str,
    model_name: str,
    manufacturer_str: str,
    mounting_position: list[float],
    mounting_orientation: list[float],
    mounting_coordinate_system: str,
    define_mounting: bool = True,
    tcps: list[TcpDefinition] | None = None,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Create a virtual controller in NOVA with TCPs and mounting.

    Returns the new motion group id (e.g. "0@controller_name").
    Raises on failure.
    """
    manufacturer = wb_models.Manufacturer(manufacturer_str)

    def _progress(value: float, text: str) -> None:
        if on_progress:
            on_progress(value, text)

    _progress(0.25, "Creating virtual controller in NOVA...")

    api = get_instances_api()
    token = api.get_auth_token_from_host(instance.host)

    async with get_api_client(
        host=instance.host,
        secure=instance.is_secure_connection,
        token=token,
    ) as api_client:
        robot_configs_api = wb_v2.RobotConfigurationsApi(api_client)
        available_types = await robot_configs_api.get_robot_configurations()

        controller_type = resolve_controller_type(model_name, available_types)
        if not controller_type:
            raise ValueError(
                f"No matching virtual controller type found for '{model_name}'."
            )

        controller_api = wb_v2.ControllerApi(api_client)
        existing_controllers = await controller_api.list_robot_controllers(cell=cell)
        if controller_name in existing_controllers:
            raise ControllerAlreadyExistsError(controller_name, cell)

        await controller_api.add_robot_controller(
            cell=cell,
            robot_controller=wb_models.RobotController(
                name=controller_name,
                configuration=wb_models.RobotControllerConfiguration(
                    wb_models.VirtualController(
                        kind="VirtualController",
                        manufacturer=manufacturer,
                        type=controller_type,
                    )
                ),
            ),
        )
        carb.log_info(
            f"Virtual controller '{controller_name}' created in cell '{cell}'"
        )

        virtual_controller_api = wb_v2.VirtualControllerApi(api_client)
        # Poll until the motion group appears. The overall time budget is owned
        # by the caller via asyncio.wait_for, so this loop must not enforce its
        # own competing deadline (doing so previously cancelled work that had
        # actually succeeded server-side and reported a false timeout).
        motion_groups = None
        while not motion_groups:
            try:
                motion_groups = await virtual_controller_api.get_motion_groups(
                    cell=cell, controller=controller_name
                )
            except Exception:
                motion_groups = None
            if not motion_groups:
                await asyncio.sleep(1.0)

        motion_group_index = motion_groups[0].motion_group
        new_motion_group_id = motion_group_index

        _progress(0.5, "Defining TCPs in NOVA...")
        if tcps:
            created = await _create_tcps(
                virtual_controller_api, cell, controller_name, motion_group_index, tcps
            )
            if created:
                nm.post_notification(
                    f"{created} TCP(s) defined in NOVA.",
                    duration=3.0,
                    status=nm.NotificationStatus.INFO,
                )
            if created < len(tcps):
                nm.post_notification(
                    f"{len(tcps) - created} TCP(s) could not be defined.",
                    duration=3.0,
                    status=nm.NotificationStatus.WARNING,
                )

        _progress(0.75, "Defining Mountings in NOVA...")
        if define_mounting:
            await virtual_controller_api.set_virtual_controller_mounting(
                cell=cell,
                controller=controller_name,
                motion_group=motion_group_index,
                coordinate_system=wb_models.CoordinateSystem(
                    coordinate_system=mounting_coordinate_system,
                    reference_coordinate_system="world",
                    position=mounting_position,
                    orientation=mounting_orientation,
                    orientation_type=wb_models.OrientationType.ROTATION_VECTOR,
                ),
            )
            nm.post_notification(
                "Mounting defined in NOVA.",
                duration=3.0,
                status=nm.NotificationStatus.INFO,
            )

        _progress(1.0, "All set up!")

    return new_motion_group_id


def preset_manufacturer_from_prim(prim) -> str | None:
    """Manufacturer stored in the robot prim's custom data, if any."""
    return find_robot_prim(prim).GetCustomData().get("manufacturer")


def preset_configuration_from_prim(prim) -> tuple[str, str] | None:
    """(manufacturer, robot configuration) stored in the robot prim's custom data.

    Both are needed to create a controller without asking the user, so a prim
    carrying only one of them yields None.
    """
    custom_data = find_robot_prim(prim).GetCustomData()
    manufacturer = custom_data.get("manufacturer")
    configuration = custom_data.get("robot_configuration_name")
    if manufacturer and configuration:
        return manufacturer, configuration
    return None


async def resolve_robot_configuration(
    instance: NOVAInstance, prim, model_name: str
) -> tuple[str, str] | None:
    """(manufacturer, robot configuration) for *prim*.

    The prim's presets win; otherwise NOVA resolves the configuration from the
    model name. None when neither yields a configuration.
    """
    preset = preset_configuration_from_prim(prim)
    if preset:
        return preset
    configuration = await fetch_configuration_for_motion_group(instance, model_name)
    if not configuration:
        return None
    return configuration.split("-", 1)[0], configuration


async def create_virtual_controller_for_prim(
    instance: NOVAInstance,
    cell: str,
    controller_name: str,
    robot_configuration: str,
    manufacturer_str: str,
    prim,
    define_mounting: bool,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Create the controller for *prim*, with the prim's TCPs and world pose as
    the controller's TCPs and mounting. Returns the new motion group id."""
    from wandelbots.omni.usd.schema_utils import SchemaUtils

    # Unassigned articulations are discovered via ArticulationRootAPI and only
    # carry MotionGroupAPI once configured, so apply it now to make the TCP
    # lookup (which requires the schema) succeed.
    SchemaUtils.ensure_motion_group_api(prim)
    robot_tcp = SchemaUtils.find_motion_group_tcp(prim)
    tcps: list[TcpDefinition] = []
    if robot_tcp:
        tcps = collect_tcps_from_prim(prim, robot_tcp.GetPath().pathString)
    else:
        carb.log_warn("No TCP found. Skipping TCP creation.")

    prim_pose_world = PrimUtils.get_prim_pose(prim.GetPath())
    return await create_virtual_controller(
        instance=instance,
        cell=cell,
        controller_name=controller_name,
        model_name=robot_configuration,
        manufacturer_str=manufacturer_str,
        mounting_position=list(prim_pose_world.pose[:3]),
        mounting_orientation=list(prim_pose_world.pose[3:]),
        mounting_coordinate_system=prim.GetPath().pathString,
        define_mounting=define_mounting,
        tcps=tcps,
        on_progress=on_progress,
    )


async def connect_motion_group_with_retry(
    instances_service: NOVAInstancesService,
    instance: NOVAInstance,
    cell: str,
    controller_name: str,
    motion_group_name: str,
    prim_path: str,
    attempts: int = _CONNECT_RETRIES,
    delay: float = _CONNECT_RETRY_DELAY_S,
) -> tuple[bool, str]:
    """Connect *prim_path* to the motion group; (success, last error message)."""
    controller_data = NOVAControllerData(name=controller_name, cell_name=cell)
    last_message = ""
    for attempt in range(attempts):
        future: asyncio.Future = asyncio.get_event_loop().create_future()

        def _on_complete(success: bool, message: str = "", _future=future):
            if not _future.done():
                _future.set_result((success, message))

        # No use_external_joint_stream: recreating a controller must not reset
        # the joint stream source the prim already carries.
        instances_service.create_motion_group_from_nova(
            instance=instance,
            controller=controller_data,
            motion_group_name=motion_group_name,
            prim_path=prim_path,
            callback=_on_complete,
        )
        success, last_message = await future
        if success:
            return True, ""
        if attempt < attempts - 1:
            await asyncio.sleep(delay)
    return False, last_message or "Failed to connect motion group."


async def create_and_connect_virtual_controller(
    instances_service: NOVAInstancesService,
    instance: NOVAInstance,
    prim,
    cell: str,
    controller_name: str,
    robot_configuration: str,
    manufacturer_str: str,
    define_mounting: bool,
    on_progress: ProgressCallback | None = None,
) -> bool:
    """Create the controller, then connect *prim* to its motion group.

    Every outcome is reported as a notification. Returns True once the
    controller exists in NOVA, whether or not the connect succeeded, so the
    caller knows the instance data changed.
    """
    if not prim or not prim.IsValid():
        notify_warning("Selected motion group prim is no longer valid.")
        return False
    try:
        motion_group_name = await asyncio.wait_for(
            create_virtual_controller_for_prim(
                instance,
                cell,
                controller_name,
                robot_configuration,
                manufacturer_str,
                prim,
                define_mounting,
                on_progress,
            ),
            timeout=_CREATE_TIMEOUT_S,
        )
    except ControllerAlreadyExistsError as error:
        carb.log_warn(str(error))
        notify_warning(
            f"A controller named '{controller_name}' already exists. "
            "Choose a different name."
        )
        return False
    except asyncio.TimeoutError:
        carb.log_error(
            f"Virtual controller creation timed out after {_CREATE_TIMEOUT_S:.0f}s."
        )
        notify_warning("Virtual controller creation timed out.")
        return False
    except Exception as error:
        carb.log_error(f"Failed to create virtual controller: {error}")
        notify_warning(f"Failed to create virtual controller: {error}")
        return False

    nm.post_notification(
        f"Virtual controller '{controller_name}' created successfully.",
        duration=3.0,
        status=nm.NotificationStatus.INFO,
    )
    # shield: cancelling the caller must not abort the server-side connect, or
    # the controller ends up created but the prim unconfigured.
    success, message = await asyncio.shield(
        connect_motion_group_with_retry(
            instances_service,
            instance,
            cell,
            controller_name,
            motion_group_name,
            prim.GetPath().pathString,
        )
    )
    if not success:
        notify_warning(message)
    return True


def notify_warning(message: str) -> None:
    nm.post_notification(message, duration=5.0, status=nm.NotificationStatus.WARNING)


async def _create_tcps(
    virtual_controller_api: wb_v2.VirtualControllerApi,
    cell: str,
    controller: str,
    motion_group: str,
    tcps: list[TcpDefinition],
) -> int:
    """Create the given TCPs, returning the number created successfully."""
    created = 0
    for tcp in tcps:
        try:
            await virtual_controller_api.add_virtual_controller_tcp(
                cell=cell,
                controller=controller,
                motion_group=motion_group,
                tcp=tcp.name,
                robot_tcp_data=wb_models.RobotTcpData(
                    name=tcp.name,
                    position=tcp.position,
                    orientation=tcp.orientation,
                    orientation_type="ROTATION_VECTOR",
                ),
            )
            carb.log_info(f"TCP '{tcp.name}' created on virtual controller.")
            created += 1
        except Exception as e:
            carb.log_warn(f"Failed to create TCP '{tcp.name}': {e}")
    return created


def collect_tcps_from_prim(prim, flange_path: str) -> list[TcpDefinition]:
    from wandelbots.omni.usd.schema_utils import SchemaUtils
    from wandelbots.omni.usd.tcp_utils import TcpUtils

    tools = SchemaUtils.list_motion_group_tools(prim)
    if not tools:
        return []

    tcps: list[TcpDefinition] = []

    for tool_prim in tools:
        # Walk each tool's subtree. The old code walked the whole stage once
        # per tool, and its path test also matched siblings whose name starts
        # the same.
        for child in Usd.PrimRange(tool_prim):
            if not TcpUtils.is_tcp(child):
                continue

            tcp_name = child.GetPath().pathString.rsplit("/", 1)[-1]
            tcp_path = child.GetPath().pathString
            rel_pose = PrimUtils.get_relative_prim_pose(flange_path, tcp_path)
            tcps.append(
                TcpDefinition(
                    name=tcp_name,
                    position=rel_pose.pose[:3],
                    orientation=rel_pose.pose[3:],
                )
            )

    return tcps
