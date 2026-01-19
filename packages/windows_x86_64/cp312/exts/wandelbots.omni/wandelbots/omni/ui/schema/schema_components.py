import carb
from .widgets import SchemaComponent
import wandelbots.usd as wb_schema  # type: ignore
from pxr import UsdPhysics, Usd, Sdf
from wandelbots.omni.utils.teaching import GhostObjectUtils, TCPSource
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.usd.schema_utils import SchemaUtils
from wandelbots.omni.usd.tcp_utils import TcpUtils
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_models
from wandelbots_api_client.v2.models.virtual_controller import VirtualController
import omni.kit.window.popup_dialog
import omni.ui
import asyncio
import omni.kit.notification_manager as nm
from wandelbots.omni.manipulators import (
    get_motion_group_configuration_from_prim,
    MotionGroupConfiguration,
)
from wandelbots.omni.utils.api import get_api_client_from_config
from wandelbots.omni.ui.dialogs import PrimSelectDialog
from omni.kit.async_engine import run_coroutine


class ToolApiSchema(SchemaComponent):
    def __init__(self):
        super().__init__("Tool", wb_schema.ToolAPI)

    def can_add(self, prim):
        return all(
            [
                super().can_add(prim),
                not prim.HasAPI(wb_schema.MotionGroupAPI),
                not prim.HasAPI(wb_schema.GhostObjectAPI),
            ]
        )


class MotionGroupApiSchema(SchemaComponent):
    def __init__(self):
        super().__init__(
            "Motion Group",
            wb_schema.MotionGroupAPI,
            [
                "enabled",
                "cell",
                "controller",
                "motionGroup",
                "externalJointStream",
                "host",
                "secure",
                "responseRate",
            ],
        )

    def can_add(self, prim):
        return all(
            [
                super().can_add(prim),
                prim.HasAPI(UsdPhysics.ArticulationRootAPI),
                not prim.HasAPI(wb_schema.GhostObjectAPI),
                not prim.HasAPI(wb_schema.ToolAPI),
            ]
        )


class GhostObjectApiSchema(SchemaComponent):
    def __init__(self):
        super().__init__("Ghost Object", wb_schema.GhostObjectAPI)

    def can_add(self, prim):
        # Ghost objects prims will be created from prims with tool api
        return False

    @staticmethod
    async def _get_existing_tcps(
        motion_group_config: MotionGroupConfiguration,
    ) -> list[str]:
        """Get list of all existing TCP names for the motion group."""
        async with get_api_client_from_config(
            motion_group_config.motion_stream_configuration.get_api_configuration()
        ) as api_client:
            motion_group_api = wb_v2.MotionGroupApi(api_client)

            # Get motion group description which contains all TCPs
            motion_group_desc = await motion_group_api.get_motion_group_description(
                cell=motion_group_config.motion_stream_configuration.cell,
                controller=motion_group_config.motion_stream_configuration.controller,
                motion_group=motion_group_config.motion_stream_configuration.motion_group,
            )

            # Return list of TCP names (keys of the tcps dictionary)
            if motion_group_desc.tcps:
                tcp_names = list(motion_group_desc.tcps.keys())
                carb.log_info(f"Existing TCPs in motion group: {tcp_names}")
                return tcp_names
            else:
                carb.log_info("No existing TCPs found in motion group")
                return []

    @staticmethod
    async def _create_tcp_on_nova(
        tcp_name: str, tcp_pose, motion_group_config: MotionGroupConfiguration
    ):
        """Create TCP on NOVA controller"""
        # First, check if TCP name already exists
        existing_tcps = await GhostObjectApiSchema._get_existing_tcps(
            motion_group_config
        )
        if tcp_name in existing_tcps:
            nm.post_notification(
                f"TCP name '{tcp_name}' is already taken. Please choose a different name.",
                duration=5.0,
                status=nm.NotificationStatus.WARNING,
            )
            return None
        # if TCP name is available, proceed to create it
        async with get_api_client_from_config(
            motion_group_config.motion_stream_configuration.get_api_configuration()
        ) as api_client:
            # Get robot controller to check if it is virtual
            controller_api = wb_v2.ControllerApi(api_client)
            robot_controller = await controller_api.get_robot_controller(
                cell=motion_group_config.motion_stream_configuration.cell,
                controller=motion_group_config.motion_stream_configuration.controller,
            )
            is_virtual = isinstance(
                robot_controller.configuration.actual_instance, VirtualController
            )
            # if it is not virtual, return and inform user
            if not is_virtual:
                nm.post_notification(
                    f"Cannot create NOVA TCP '{tcp_name}': Robot controller '{motion_group_config.motion_stream_configuration.controller}' in cell '{motion_group_config.motion_stream_configuration.cell}' is not a virtual controller.",
                    duration=5.0,
                    status=nm.NotificationStatus.WARNING,
                )
                return None
            # if it is virtual, create VirtualControllerApi instance
            virtual_controller_api = wb_v2.VirtualControllerApi(api_client)
            # Call the add_virtual_controller_tcp method with all required arguments
            result = await virtual_controller_api.add_virtual_controller_tcp(
                cell=motion_group_config.motion_stream_configuration.cell,
                controller=motion_group_config.motion_stream_configuration.controller,
                motion_group=motion_group_config.motion_stream_configuration.motion_group,
                tcp=tcp_name,
                robot_tcp_data=wb_models.RobotTcpData(
                    name=tcp_name,
                    position=tcp_pose[:3],
                    orientation=tcp_pose[3:],
                    orientation_type="ROTATION_VECTOR",
                ),
            )
            nm.post_notification(
                f"TCP '{tcp_name}' was successfully created in NOVA.", duration=5.0
            )
            return result

    def can_create_ghost_object(payload: dict) -> bool:
        prim_list: list[Usd.Prim] = payload.get("prim_list", [])
        if len(prim_list) == 0 or len(prim_list) > 1:
            return False
        return prim_list[0].HasAPI(wb_schema.ToolAPI)

    def can_create_nova_tcp_object(payload: dict) -> bool:
        """Check if a NOVA TCP can be created from the given payload."""
        prim_list: list[Usd.Prim] = payload.get("prim_list", [])
        if len(prim_list) == 0 or len(prim_list) > 1:
            return False

        if TcpUtils.is_tcp(prim_list[0]):
            # Check if it's a child of a tool with ToolAPI
            tool_prim = SchemaUtils.find_parent_tool(prim_list[0])
            return tool_prim is not None and tool_prim.HasAPI(wb_schema.ToolAPI)
        return GhostObjectUtils.is_ghost_object(prim_list[0])

    def create_ghost_object_from_payload(payload: dict) -> bool:
        prim_list: list[Usd.Prim] = payload.get("prim_list", [])
        if len(prim_list) == 0 or len(prim_list) > 1:
            carb.log_warn("Cannot create a ghost object for multiple prims")
            return False

        run_coroutine(
            GhostObjectApiSchema.create_ghost_object_from_prim(prim_list[0])
        ).add_done_callback(
            lambda fut: carb.log_error(
                f"Error creating ghost object: {fut.exception()}"
            )
            if fut.exception()
            else None
        )

    async def create_ghost_object_from_prim(tool_prim: Usd.Prim) -> bool:
        tcp_sources: list[TCPSource] = GhostObjectUtils.get_all_tcp_sources(tool_prim)
        if len(tcp_sources) == 0:
            carb.log_warn(
                f"Cannot create ghost object for {tool_prim.GetPath().pathString} because no TCP source found"
            )
            return False

        # Default to first tcp but offer user to select if multiple available
        selected_tcp: TCPSource = tcp_sources[0]

        if len(tcp_sources) > 1:

            def _filter_fn(prim: Usd.Prim) -> bool:
                return prim.GetPath().pathString in [
                    prim.prim_path for prim in tcp_sources
                ]

            dialog = PrimSelectDialog(
                stage=tool_prim.GetStage(),
                window_title="Select TCP Source for Ghost Object",
                modal_window=True,
            )

            selected_prims = await dialog.show(1, _filter_fn)
            if selected_prims is None:
                carb.log_verbose("Ghost object creation cancelled by user")
                return False

            if selected_prims and len(selected_prims) > 0:
                # Find the matching TCP source
                selected_tcp = next(
                    (
                        tcp
                        for tcp in tcp_sources
                        if tcp.prim_path == selected_prims[0].GetPath().pathString
                    ),
                    tcp_sources[0],
                )
            else:
                carb.log_warn("No TCP source selected, using first available")

        pose = PrimUtils.get_prim_pose(
            selected_tcp.prim_path,
            coordinate_system="world",
        )

        GhostObjectUtils.add_ghost_object(
            source_prim=tool_prim,
            tcp_world_pose=pose,
            tcp_prim=tool_prim.GetStage().GetPrimAtPath(selected_tcp.prim_path),
        )
        return True

    async def create_nova_tcp_from_payload(payload: dict) -> bool:
        prim_list: list[Usd.Prim] = payload.get("prim_list", [])
        if len(prim_list) == 0:
            nm.post_notification("No prims selected")
            return False
        if len(prim_list) > 1:
            nm.post_notification("Cannot create a NOVA TCP for multiple prims")
            return False

        prim = prim_list[0]
        if TcpUtils.is_tcp(prim):
            return await GhostObjectApiSchema.create_nova_tcp_from_tcp_prim(prim)
        elif GhostObjectUtils.is_ghost_object(prim):
            return await GhostObjectApiSchema.create_tcp_from_ghost_object(prim)

    async def create_nova_tcp_from_tcp_prim(tcp_prim: Usd.Prim) -> bool:
        """Create a NOVA TCP from the given payload."""
        flange_prim = SchemaUtils.get_flange_tcp_from_tool_tcp(tcp_prim)
        if flange_prim is None:
            nm.post_notification(
                f"Could not find motion group flange tcp for tool tcp '{tcp_prim.GetPath().pathString}'",
                nm.NotificationStatus.WARNING,
            )

        tcp_pose = PrimUtils.get_relative_prim_pose(  # Get relative pose between flange and tool TCP
            flange_prim.GetPath().pathString, tcp_prim.GetPath().pathString
        ).pose
        tool_prim = SchemaUtils.find_parent_tool(tcp_prim)  # Find the parent tool prim
        motion_group_prim = SchemaUtils.find_tool_linked_motion_group(
            tool_prim
        )  # Find linked motion group prim

        motion_group_config = get_motion_group_configuration_from_prim(
            motion_group_prim
        )  # Extract motion group properties

        def _on_ok_clicked(
            dialog: omni.kit.window.popup_dialog.FormDialog,
            tcp_pose,
            motion_group_config,
        ):
            """Handler function to process the form data when user clicks OK"""
            tcp_name = dialog.get_value("name")
            carb.log_info(f"Creating NOVA TCP with name: {tcp_name}")

            # Create async task to call the API using the event loop
            asyncio.get_event_loop().create_task(
                GhostObjectApiSchema._create_tcp_on_nova(
                    tcp_name, tcp_pose, motion_group_config
                )
            )
            dialog.destroy()

        def _on_cancel_clicked(dialog: omni.kit.window.popup_dialog.FormDialog):
            """Handler function when user cancels"""
            carb.log_warn("TCP creation cancelled by user")
            dialog.destroy()

        # Create and show the dialog - store reference to prevent garbage collection
        dialog = omni.kit.window.popup_dialog.FormDialog(
            width=400,
            message="Please enter the name for the new NOVA TCP:",
            title="Create NOVA TCP",
            ok_handler=lambda d: _on_ok_clicked(d, tcp_pose, motion_group_config),
            cancel_handler=_on_cancel_clicked,
            ok_label="Create",
            cancel_label="Cancel",
            field_defs=[
                omni.kit.window.popup_dialog.FormDialog.FieldDef(
                    "name",
                    "TCP Name:",
                    omni.ui.StringField,
                    tool_prim.GetName().lower(),
                )
            ],
            input_width=250,
        )

        # Show the dialog explicitly
        dialog.show()

        return True

    async def create_tcp_from_ghost_object(ghost_object_prim: Usd.Prim) -> bool:
        """Create a NOVA TCP from the given payload."""

        ghost_object = GhostObjectUtils.get_ghost_object_from_prim(ghost_object_prim)
        if ghost_object is None:
            nm.post_notification(
                f"Prim '{ghost_object_prim.GetPath().pathString}' is not a ghost object"
            )
            return False

        ghost_object_api = wb_schema.GhostObjectAPI.Get(
            ghost_object_prim.GetStage(), ghost_object_prim.GetPath()
        )
        ghost_object_api.GetSourceTcpRel()
        tcp_prim_paths: list[Sdf.Path] = (
            ghost_object_api.GetSourceTcpRel().GetForwardedTargets()
        )

        if len(tcp_prim_paths) == 0:
            nm.post_notification(
                f"Cannot create a NOVA TCP for ghost object '{ghost_object_prim.GetPath().pathString}' without source TCP"
            )
            return False

        if len(tcp_prim_paths) > 1:
            carb.log_warn(
                f"'{ghost_object_prim.GetPath().pathString}' has multiple source TCPs, using '{tcp_prim_paths[0]}'"
            )

        await GhostObjectApiSchema.create_nova_tcp_from_tcp_prim(
            ghost_object_prim.GetStage().GetPrimAtPath(tcp_prim_paths[0])
        )


schema_components: list[SchemaComponent] = [
    ToolApiSchema(),
    MotionGroupApiSchema(),
    GhostObjectApiSchema(),
]
