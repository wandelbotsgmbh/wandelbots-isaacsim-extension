"""
Wandelbots NOVA + Isaac Lab Starter Kit

This is a beginner-friendly template for getting started with Wandelbots NOVA and Isaac Lab.
Simply customize the settings below and run the script!

Quick Setup:
1. Update YOUR_SETTINGS below with your robot and API details
2. Run: python isaaclab_starter.py
3. Watch your robot move in simulation!
"""

import argparse
import asyncio
import logging
import threading
import time

from isaaclab.app import AppLauncher

# Set up Isaac Lab (this is required boilerplate)
parser = argparse.ArgumentParser(description="Wandelbots NOVA + Isaac Lab Starter Kit")
AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

# Import Isaac Lab components
from isaaclab.sim import SimulationCfg, SimulationContext
import isaaclab.sim as sim_utils
from isaaclab.assets import AssetBaseCfg
from isaaclab.scene import InteractiveScene, InteractiveSceneCfg
from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.utils import configclass

# Import Wandelbots utilities
from scripts.wandelbots.utils import WandelbotsUtils, get_default_poses, create_wandelbots_connection, restart_timeline_for_nova

# ================================================================================
# YOUR SETTINGS - CUSTOMIZE THESE FOR YOUR SETUP
# ================================================================================

# Your Wandelbots NOVA API details (get these from your Wandelbots account)
# YOUR_NOVA_API = "XYZ.instance.wandelbots.io"  # Replace with your NOVA API endpoint
# YOUR_ACCESS_TOKEN = "your_access_token_here"  # Replace with your actual access token

# Your robot details
# YOUR_ROBOT_NAME = ""  # Name of your robot controller in NOVA
# YOUR_ROBOT_PRIM_PATH = ""  # Prim path of your robot in the simulation
# YOUR_USD_SCENE_PATH = ""  # Path to your robot USD file


# Camera position (where you want to view the scene from)
CAMERA_POSITION = [2.5, 2.5, 2.5]  # [x, y, z] coordinates
CAMERA_TARGET = [0.0, 0.0, 0.0]     # What point to look at

# ================================================================================
# SCENE SETUP - This defines what appears in your simulation
# ================================================================================

@configclass
class NovaSceneCfg(InteractiveSceneCfg):
    """Define what objects appear in your simulation scene."""

    # Ground plane (floor)
    ground = AssetBaseCfg(
        prim_path="/World/defaultGroundPlane",
        spawn=sim_utils.GroundPlaneCfg(),
    )

    # Lighting (so you can see everything)
    dome_light = AssetBaseCfg(
        prim_path="/World/Light", 
        spawn=sim_utils.DomeLightCfg(intensity=3000.0, color=(0.75, 0.75, 0.75))
    )

    # Your robot (loaded from USD file)
    robot = AssetBaseCfg(
        prim_path=YOUR_ROBOT_PRIM_PATH,
        spawn=UsdFileCfg(usd_path=YOUR_USD_SCENE_PATH),
    )

# ================================================================================
# HELPER FUNCTIONS - These make the complex stuff simple
# ================================================================================

def setup_simulation():
    """Create and start the Isaac Lab simulation."""
    logging.info("Setting up Isaac Lab simulation...")
    
    # Create simulation with 10ms timesteps
    sim_cfg = SimulationCfg(dt=0.01, device=args_cli.device)
    sim = SimulationContext(sim_cfg)
    
    # Set up the camera view
    from isaacsim.core.utils.viewports import set_camera_view
    set_camera_view(eye=CAMERA_POSITION, target=CAMERA_TARGET)
    
    # Create the scene with your robot
    scene_cfg = NovaSceneCfg(num_envs=1, env_spacing=2.0)
    scene = InteractiveScene(scene_cfg)
    
    # Start the simulation
    sim.reset()
    logging.info("Simulation is now running!")
    
    return sim, scene

def connect_to_nova():
    """Connect to Wandelbots NOVA and set up the robot controller."""
    logging.info("Connecting to Wandelbots NOVA...")
    
    # Set up a new event loop for NOVA (required for async operations)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Connect to NOVA and create robot controller
        utils, controller = loop.run_until_complete(
            create_wandelbots_connection(
                nova_api=YOUR_NOVA_API,
                access_token=YOUR_ACCESS_TOKEN,
                robot_name=YOUR_ROBOT_NAME,
                prim_path=YOUR_ROBOT_PRIM_PATH
            )
        )
        
        logging.info("Successfully connected to NOVA!")
        return controller
        
    except Exception as e:
        logging.error(f"Failed to connect to NOVA: {e}")
        return None
    finally:
        # Clean up the event loop
        try:
            pending_tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                loop.run_until_complete(asyncio.gather(*pending_tasks, return_exceptions=True))
        except:
            pass
        finally:
            if not loop.is_closed():
                loop.close()

def execute_robot_motion(simulation, controller):
    """Make the robot move using predefined poses."""
    if controller is None:
        logging.error("Cannot execute motion - no controller available")
        return
        
    logging.info("Starting robot motion...")
    
    # Set up a new event loop for motion execution
    motion_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(motion_loop)
    
    try:
        # Create utilities and get default poses
        utils = WandelbotsUtils(nova_api=YOUR_NOVA_API, access_token=YOUR_ACCESS_TOKEN)
        poses = get_default_poses()
        
        logging.info(f"Executing motion sequence with {len(poses)} poses...")
        
        # Execute the motion sequence
        motion_loop.run_until_complete(utils.execute_motion_sequence(controller, poses))
        
        logging.info("Robot motion completed successfully!")
        
    except Exception as e:
        logging.error(f"Motion execution failed: {e}")
    finally:
        # Clean up
        try:
            pending_tasks = [task for task in asyncio.all_tasks(motion_loop) if not task.done()]
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                motion_loop.run_until_complete(asyncio.gather(*pending_tasks, return_exceptions=True))
        except:
            pass
        finally:
            if not motion_loop.is_closed():
                motion_loop.close()

# ================================================================================
# MAIN PROGRAM - This is where everything happens
# ================================================================================

def main():
    """Main function that runs your NOVA + Isaac Lab integration."""
    
    logging.info("=== Wandelbots NOVA + Isaac Lab Starter Kit ===")
    
    # Step 1: Set up the Isaac Lab simulation
    sim, scene = setup_simulation()
    
    # Step 2: Connect to NOVA in a background thread (so simulation keeps running)
    nova_controller = None
    connection_ready = threading.Event()

    def nova_connection_thread():
        nonlocal nova_controller
        time.sleep(2)  # Wait for simulation to stabilize
        nova_controller = connect_to_nova()
        connection_ready.set()
    
    # Start NOVA connection in background
    threading.Thread(target=nova_connection_thread, daemon=True).start()

    # Step 3: Run simulation loop
    logging.info("Running simulation... waiting for NOVA connection...")
    step_count = 0
    motion_executed = False
    timeline_restarted = False  # Track if we've restarted timeline after NOVA connection
    
    while simulation_app.is_running():
        # Update simulation
        sim.step()
        scene.update(sim.get_physics_dt())
        step_count += 1
        
        # Small pause to prevent CPU overload
        time.sleep(0.001)
        
        # Step 4: Restart timeline after NOVA connection (handled automatically by utils)
        if (connection_ready.is_set() and 
            nova_controller is not None and  # Make sure controller was created successfully
            not timeline_restarted):
            
            restart_timeline_for_nova(sim)
            timeline_restarted = True
            step_count = 0  # Reset step counter after timeline restart
            continue
        
        # Step 5: Execute motion after timeline has been restarted
        if (timeline_restarted and 
            nova_controller is not None and  # Double-check controller is available
            not motion_executed and 
            step_count > 30):  # Wait ~30 steps after timeline restart
            
            logging.info("Timeline synced! Starting robot motion...")
            
            # Execute motion in background thread (so simulation continues)
            def motion_thread():
                execute_robot_motion(sim, nova_controller)
            
            threading.Thread(target=motion_thread, daemon=True).start()
            motion_executed = True
        
        # Log status every 10 seconds
        if step_count % 1000 == 0:
            if connection_ready.is_set():
                if nova_controller is not None:
                    if timeline_restarted:
                        status = "NOVA connected & timeline synced"
                    else:
                        status = "NOVA connected, restarting timeline..."
                else:
                    status = "NOVA connection failed - controller is None"
            else:
                status = "Connecting to NOVA..."
            
            motion_status = "Motion executed" if motion_executed else "Waiting for motion"
            logging.info(f"Status: {status} | {motion_status} | Steps: {step_count}")
    
    logging.info("=== Integration Complete ===")

# ================================================================================
# RUN THE PROGRAM
# ================================================================================

if __name__ == "__main__":
    # Check if user has updated their settings
    if YOUR_ACCESS_TOKEN == "your_access_token_here":
        print("\n" + "="*80)
        print("⚠️  PLEASE UPDATE YOUR SETTINGS FIRST!")
        print("="*80)
        print("1. Open this file (nova_starter_kit.py) in a text editor")
        print("2. Find the 'YOUR SETTINGS' section at the top")
        print("3. Replace 'your_access_token_here' with your actual NOVA access token")
        print("4. Update YOUR_NOVA_API with your NOVA endpoint")
        print("5. Update YOUR_USD_SCENE_PATH if needed")
        print("6. Save the file and run again")
        print("="*80 + "\n")
        input("Press Enter to continue anyway (will likely fail)...")
    
    # Run the main program
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Program stopped by user")
    except Exception as e:
        logging.error(f"Program failed: {e}")
    finally:
        # Clean shutdown
        simulation_app.close()