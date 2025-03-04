import yaml
from wandelbots.omni.core.networks import StreamingConnector
from wandelbots.omni.datatypes import UsdStageModel
from wandelbots.omni.environment import host_database
from wandelbots.omni.router.v1.robot import ConfigurableRobot, create_robot
from wandelbots.omni.router.v1.scene import open_stage
from wandelbots.omni.router.v1.stream import create_streams
from wandelbots.omni.router.v1.tool import ConfigurableTool, create_tools
from wandelbots.omni.utils.configuration import format_object_for_export
from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status

configuration_router = APIRouter(prefix="/configuration", tags=["configuration"])


@configuration_router.post(
    path="/load_configuration",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="load_configuration",
    response_model=None,
)
async def load_configuration(file: UploadFile = File(...)) -> None:
    """
    Configures the scene using the uploaded configuration. It can have the following keys:
    scene: uri of the scene which has to be loaded
    robots: the robots which you want to configure
    tools: the tools which you want to configure
    streams: the streams which you want to configure

    Look at data/sample_config for more info
    Args:
        file: .yaml configuration file

    Returns:
        None

    """
    try:
        yaml_content = await file.read()
        config = yaml.safe_load(yaml_content.decode("utf-8"))
    except Exception as e:
        raise HTTPException(404, f"Could not load configuration file: {str(e)}")

    try:
        if "scene" in config and "uri" in config["scene"]:
            uri = config["scene"]["uri"]
            if uri:
                await open_stage(UsdStageModel(uri=uri))

        if "robots" in config:
            for key, val in config["robots"].items():
                robot_config = {"identifier": key, **val["configuration"]}
                await create_robot(ConfigurableRobot.from_dict(robot_config))

        if "tools" in config:
            for key, val in config["tools"].items():
                tool_config = {"identifier": key, **val["configuration"]}
                tool_cls = ConfigurableTool.tools_registry[tool_config["type"]]
                await create_tools([tool_cls.from_dict(tool_config)])

        if "streams" in config:
            for key, val in config["streams"].items():
                stream_config = {"identifier": key, **val["configuration"]}
                stream_cls = StreamingConnector.streams_registry[stream_config["type"]]
                await create_streams([stream_cls.from_dict(stream_config)])

    except Exception as e:
        raise HTTPException(404, f"Could not load configuration file: {str(e)}")


@configuration_router.post(
    path="/export_configuration",
    operation_id="export_configuration",
    response_model=None,
)
async def export_configuration():
    """
    Exports the configured scene setup as .yaml file
    Returns:
        .yaml file with configured setup

    """
    # Remove entries from the host database that should not be in the configuration
    keys_to_remove = {"instance", "identifier", "default_poses", "scene"}
    config_data = format_object_for_export(host_database.data, keys_to_remove)

    try:
        yaml_data = yaml.dump(config_data, sort_keys=False)
        return Response(
            content=yaml_data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": "attachment; filename=data.yaml"},
        )
    except Exception as e:
        raise HTTPException(500, f"Could not export configuration : {str(e)}")
