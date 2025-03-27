from wandelbots.omni.environment import host_database
from wandelbots.omni.core.robot import ConfigurableRobot


def get_robot_by_prim_path(prim_path: str) -> ConfigurableRobot.Configuration | None:
    for robot_key in host_database["robots"]:
        robot: ConfigurableRobot.Configuration = host_database[
            f"robots.{robot_key}.configuration"
        ]
        if robot["prim_path"] == prim_path:
            return ConfigurableRobot.Configuration(**robot)
    return None
