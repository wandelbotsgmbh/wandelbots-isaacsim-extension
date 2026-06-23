import logging

from nova import Nova
from nova.config import NovaConfig
from nova.cell import virtual_controller
from nova.api import models
from nova.core.controller import Controller
import wandelbots_isaacsim_api as isaac_sim_api
from nova.types import MotionSettings, Pose
from nova.actions import ptp
import os

import wandelbots_api_client.v2 as wb_v2


class WandelbotsUtils:
    """Utility class for Wandelbots NOVA integration with Isaac Lab."""

    def __init__(
        self,
        nova_api: str,
        access_token: str,
        nova_cell: str = "cell",
        omniservice_base_url: str = "http://127.0.0.1:8011/omniservice/api/v2",
        nats_broker: str = "",
    ):
        """Initialize WandelbotsUtils with connection parameters.

        Args:
            simulation: Isaac Lab SimulationContext instance
            nova_api: NOVA API endpoint URL
            access_token: Authentication token for NOVA
            nova_cell: Cell name (default: "cell")
            omniservice_base_url: Isaac Sim omniservice API base URL
            nats_broker: NATS broker configuration
        """
        self.nova_cell = nova_cell
        self.nova_api = nova_api
        self.nova_access_token = access_token
        self.nats_broker = nats_broker
        self.omniservice_base_url = omniservice_base_url
        self._setup()

    def _setup(self):
        """Setup environment variables for NOVA connection."""
        os.environ["NOVA_API"] = f"https://{self.nova_api}"
        os.environ["NOVA_ACCESS_TOKEN"] = self.nova_access_token
        os.environ["CELL_NAME"] = self.nova_cell

    # ===== NOVA CONTROLLER MANAGEMENT =====

    async def create_nova_controller(self, robot_name: str) -> Controller:
        """Create and ensure a NOVA controller exists.

        Args:
            robot_name: Name of the robot controller

        Returns:
            Controller instance
        """
        logging.info(f"Creating NOVA controller for robot '{robot_name}'...")

        # Validate credentials before attempting connection
        if not self.nova_access_token or self.nova_access_token.strip() == "":
            raise ValueError("NOVA access token is empty or not provided")

        if not self.nova_api or self.nova_api.strip() == "":
            raise ValueError("NOVA API endpoint is empty or not provided")

        logging.info(f"Validating connection to NOVA API: {self.nova_api}")
        logging.info(
            f"Using access token: {self.nova_access_token[:10]}..."
            if len(self.nova_access_token) > 10
            else "Token too short"
        )

        config = NovaConfig(
            host=self.nova_api,
            access_token=self.nova_access_token,
            nats_client_config={},
        )

        logging.info("Establishing connection to NOVA...")
        try:
            nova = Nova(config=config)
            cell = nova.cell()

            logging.info(f"Ensuring controller '{robot_name}' exists...")
            controller: Controller = await cell.ensure_controller(
                virtual_controller(
                    name=robot_name,
                    manufacturer=models.Manufacturer.UNIVERSALROBOTS,
                    type=models.VirtualControllerTypes.UNIVERSALROBOTS_MINUS_UR10E,
                )
            )
            logging.info(
                f"Controller '{robot_name}' ready with ID: {controller.configuration.id}"
            )
            return controller

        except Exception as e:
            error_msg = str(e)
            logging.error(f"NOVA connection failed: {error_msg}")

            # Provide specific guidance for common errors
            if "400" in error_msg and "authentication" in error_msg.lower():
                logging.error("Authentication failed - please check:")
                logging.error("1. Access token is valid and not expired")
                logging.error("2. NOVA API endpoint is correct")
                logging.error("3. Network connectivity to NOVA server")
            elif "timeout" in error_msg.lower() or "connection" in error_msg.lower():
                logging.error("Connection failed - please check:")
                logging.error("1. NOVA server is running and accessible")
                logging.error("2. Network connectivity")
                logging.error("3. Firewall/proxy settings")

            raise e

    async def get_nova_connection(self) -> Nova:
        """Get a NOVA connection instance.

        Returns:
            Nova connection instance
        """
        config = NovaConfig(
            host=self.nova_api,
            access_token=self.nova_access_token,
            nats_client_config={},
        )
        return Nova(config=config)

    # ===== ISAAC SIM API UTILITIES =====

    async def ensure_authenticated(self, api_client: isaac_sim_api.ApiClient):
        """Authenticate Isaac Sim Extension with Wandelbots NOVA.

        Args:
            api_client: Isaac Sim API client instance
        """
        logging.info("Authenticating Isaac Sim Extension with Wandelbots NOVA...")
        default_api = isaac_sim_api.DefaultApi(api_client)
        await default_api.authenticate(
            isaac_sim_api.models.Auth0Credentials(
                host=self.nova_api, is_secured=True, access_token=self.nova_access_token
            )
        )

    async def has_motion_group(
        self, api_client: isaac_sim_api.ApiClient, prim_path: str
    ) -> bool:
        """Check if motion group for given prim path exists.

        Args:
            api_client: Isaac Sim API client instance
            prim_path: Prim path of the robot in Isaac Sim

        Returns:
            True if motion group exists, False otherwise
        """
        logging.info(f"Checking if motion group for {prim_path} exists...")
        motion_group_api = isaac_sim_api.ManipulatorsMotionGroupApi(api_client)
        try:
            motion_groups = await motion_group_api.list_motion_groups()
            return prim_path in motion_groups.keys()
        except isaac_sim_api.exceptions.ApiException as e:
            if e.status == 404:
                return False
            else:
                raise

    # ===== MOTION GROUP MANAGEMENT =====

    async def connect_motion_group(self, controller: Controller, prim_path: str):
        """Connect motion group for controller at specified prim path.

        Args:
            controller: NOVA Controller instance
            prim_path: Prim path of the robot in Isaac Sim
        """
        logging.info(
            f"Connecting motion group for controller {controller.configuration.id} at prim path {prim_path}..."
        )
        configuration = isaac_sim_api.Configuration(host=self.omniservice_base_url)
        async with isaac_sim_api.ApiClient(configuration) as api_client:
            await self.ensure_authenticated(api_client)
            motion_group_api = isaac_sim_api.ManipulatorsMotionGroupApi(api_client)
            motion_group_id = f"0@{controller.configuration.id}"
            motion_stream_configuration = (
                isaac_sim_api.models.MotionStreamConfiguration(
                    host=self.nova_api,
                    secure_connection=True,
                    cell=self.nova_cell,
                    motion_group=motion_group_id,
                    controller=controller.configuration.id,
                )
            )
            motion_group_configuration = isaac_sim_api.models.MotionGroupConfiguration(
                name=motion_group_id,
                prim_path=prim_path,
                motion_stream_configuration=motion_stream_configuration,
            )

            if await self.has_motion_group(api_client, prim_path):
                logging.info(f"Motion group for {prim_path} already exists.")
            else:
                logging.info(f"Creating motion group for {prim_path}...")
                await motion_group_api.create_motion_group(motion_group_configuration)
                logging.info(f"Motion group for {prim_path} created successfully.")

    # ===== ROBOT MOTION UTILITIES =====
    async def get_tcp(self, motion_group):
        """Get TCP (Tool Center Point) named 'Flange' from motion group.

        Args:
            motion_group: NOVA motion group instance

        Returns:
            TCP name string

        Raises:
            ValueError: If 'Flange' TCP is not found
        """
        logging.info("Retrieving TCP named 'Flange'...")
        tcp_names = await motion_group.tcp_names()
        if "Flange" in tcp_names:
            return "Flange"
        logging.error(f"TCP 'Flange' not found. Available TCPs: {tcp_names}")
        raise ValueError(f"TCP named 'Flange' not found.")

    async def plan_and_execute(self, actions, motion_group, tcp):
        """Plan and execute one or more actions with given motion group and TCP.

        Args:
            actions: List of motion actions to execute
            motion_group: NOVA motion group instance
            tcp: TCP name to use for motion
        """
        logging.info(f"Planning and executing actions with TCP '{tcp}'...")
        joint_trajectory = await motion_group.plan(actions, tcp)
        await motion_group.execute(joint_trajectory, tcp, actions=actions)

    async def move_robot(self, motion_group, tcp, poses: list[Pose]):
        """Move robot from start pose to target pose.

        Args:
            motion_group: NOVA motion group instance
            tcp: TCP name to use for motion
            poses: List of Pose objects with 'start' and 'target' poses
        """
        logging.info(f"Moving robot from {poses[0]} to {poses[1]}...")
        # Approach
        actions = []
        for i in range(len(poses)):
            actions.append(
                ptp(
                    poses[i],
                    settings=MotionSettings(
                        tcp_velocity_limit=300, position_zone_radius=100
                    ),
                )
            )

        await self.plan_and_execute(actions, motion_group, tcp)
        logging.info(f"Robot at Target pose {poses[len(poses) - 1]}")

    # ===== HIGH-LEVEL INTEGRATION FUNCTIONS =====

    async def setup_controller_and_motion_group(
        self, robot_name: str, prim_path: str
    ) -> Controller:
        """Setup NOVA controller and connect motion group.

        Args:
            robot_name: Name of the robot controller
            prim_path: Prim path of the robot in Isaac Sim

        Returns:
            Controller instance ready for motion
        """
        logging.info(
            f"Setting up controller and motion group for '{robot_name}' at '{prim_path}'..."
        )

        controller = await self.create_nova_controller(robot_name)

        await self.connect_motion_group(controller, prim_path)

        logging.info(f"Controller and motion group setup complete for '{robot_name}'")
        return controller

    async def execute_motion_sequence(self, controller: Controller, poses: list[Pose]):
        """Execute a predefined motion sequence using the controller.

        Args:
            controller: NOVA Controller instance
            poses: List of Pose objects
        """
        logging.info("Executing motion sequence...")
        async with controller[0] as motion_group:
            tcp = await self.get_tcp(motion_group)
            await self.move_robot(motion_group, tcp, poses)
        logging.info("Motion sequence completed successfully!")

    async def reset_position(self, controller: Controller):
        """Reset robot position to home pose.

        Args:
            controller: NOVA Controller instance
            prim_path: Prim path of the robot in Isaac Sim
        """
        logging.info("Resetting robot position to home pose...")
        api_configuration = wb_v2.Configuration(
            host=f"https://{self.nova_api}/api/v2", access_token=self.nova_access_token
        )
        api_client = wb_v2.ApiClient(configuration=api_configuration)
        controller_api = wb_v2.api.ControllerApi(api_client=api_client)
        virtual_controller_api = wb_v2.api.VirtualControllerApi(api_client=api_client)

        logging.info(f"Controller ID: {controller.configuration.id}")
        logging.info(f"NOVA Cell: {self.nova_cell}")
        logging.info(f"Host: https://{self.nova_api}")
        try:
            await controller_api.set_default_mode(
                cell=self.nova_cell,
                controller=controller.configuration.id,
                mode=wb_v2.models.SettableRobotSystemMode.ROBOT_SYSTEM_MODE_MONITOR,
            )
        except Exception as e:
            logging.error(
                f"Could not set controller to MONITOR mode before reset_position: {e}"
            )

        try:
            joints = wb_v2.models.MotionGroupJoints(
                positions=[0.000, -1.571, -1.571, -1.571, 1.571, -1.571]
            )
            await virtual_controller_api.set_motion_group_state(
                cell=self.nova_cell,
                controller=controller.configuration.id,
                motion_group="0@" + controller.configuration.id,
                motion_group_joints=joints,
            )
        except Exception as e:
            logging.error(f"Error during reset_position motion execution: {e}")
            raise e

        try:
            await controller_api.set_default_mode(
                cell=self.nova_cell,
                controller=controller.configuration.id,
                mode=wb_v2.models.SettableRobotSystemMode.ROBOT_SYSTEM_MODE_CONTROL,
            )
        except Exception as e:
            logging.warning(
                f"Could not set controller back to CONTROL mode after reset_position: {e}"
            )

        await api_client.close()


# ===== UTILITY FUNCTIONS ====
def get_default_poses() -> list[Pose]:
    """Get default home and target poses for UR10e robot.

    Returns:
        List of Pose objects
    """
    return [
        Pose((691.5, -174.1, 676.5, -3.1414, -0.0003, 0.001)),
        Pose((691.5, -567.3, 676.5, -3.1414, -0.0003, 0.001)),
        Pose((691.4, -567.3, 417.7, -3.1414, -0.0003, 0.001)),
        Pose((691.4, -815.5, 417.7, -3.1414, -0.0003, 0.001)),
        Pose((691.4, -393.4, 708.7, -3.1414, -0.0003, 0.001)),
        Pose((691.5, -174.1, 676.5, -3.1414, -0.0003, 0.001)),
    ]


async def create_wandelbots_connection(
    nova_api: str, access_token: str, robot_name: str, prim_path: str
) -> tuple[WandelbotsUtils, Controller]:
    """Convenience function to create WandelbotsUtils and setup controller.

    Args:
        simulation: Isaac Lab SimulationContext instance
        nova_api: NOVA API endpoint URL
        access_token: Authentication token for NOVA
        robot_name: Name of the robot controller
        prim_path: Prim path of the robot in Isaac Sim

    Returns:
        Tuple of (WandelbotsUtils instance, Controller instance)
    """
    wandelbots_utils = WandelbotsUtils(nova_api=nova_api, access_token=access_token)
    controller = await wandelbots_utils.setup_controller_and_motion_group(
        robot_name, prim_path
    )
    return wandelbots_utils, controller


def restart_timeline_for_nova(sim):
    """Restart the simulation timeline to sync with NOVA.

    This is a technical requirement for NOVA integration - the timeline
    needs to be restarted after NOVA connection to ensure proper synchronization
    between NOVA and Isaac Sim before executing motions.

    Args:
        sim: Isaac Lab SimulationContext instance
    """
    import time

    logging.info("Restarting timeline to sync with NOVA...")

    # Pause the timeline first
    sim.pause()
    logging.info("Timeline paused - syncing with NOVA...")
    time.sleep(3)  # Give time for the pause to take effect and sync

    # Restart the timeline
    sim.play()
    logging.info("Timeline restarted! Simulation is now synced with NOVA.")
