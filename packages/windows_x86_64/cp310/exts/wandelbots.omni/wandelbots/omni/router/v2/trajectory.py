from fastapi.exceptions import HTTPException
from typing import Annotated
from fastapi import APIRouter, Depends, status, Body, Path

from wandelbots.omni.visualization import (
    get_trajectory_builder,
    TrajectoryBuilder,
)
from wandelbots.omni.visualization.models import (
    TrajectoryData,
    TrajectoryObject,
    PatchTrajectoryData,
    TrajectoryMarker,
)

trajectory_router = APIRouter(prefix="/trajectories", tags=["Trajectory"])

TrajectoryBuilderDep = Annotated[TrajectoryBuilder, Depends(get_trajectory_builder)]


@trajectory_router.post(
    path="/",
    operation_id="create_trajectory",
    status_code=status.HTTP_201_CREATED,
    response_model=TrajectoryObject,
    responses={
        status.HTTP_409_CONFLICT: {"description": "Trajectory already exists"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "Trajectory could not be created"
        },
    },
)
async def create_trajectory(
    trajectory_builder: TrajectoryBuilderDep,
    trajectory_data: TrajectoryData = Body(..., description="Trajectory data"),
) -> TrajectoryObject:
    """
    Creates a new trajectory in the scene.
    """
    try:
        trajectory_builder.create_trajectory(trajectory_data)
        return trajectory_builder.get_trajectory(trajectory_data.name)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@trajectory_router.patch(
    path="/{name}",
    operation_id="update_trajectory",
    status_code=status.HTTP_200_OK,
    response_model=TrajectoryObject,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Trajectory not found"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "Unable to update trajectory"
        },
    },
)
async def update_trajectory(
    trajectory_builder: TrajectoryBuilderDep,
    name: str = Path(..., description="Name of the trajectory to update"),
    trajectory_data: PatchTrajectoryData = Body(
        ..., description="Motion commands data"
    ),
) -> TrajectoryObject:
    """
    Updates an existing trajectory with new motion data.
    """
    try:
        trajectory_builder.update_trajectory(name, trajectory_data)
        return trajectory_builder.get_trajectory(name)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@trajectory_router.delete(
    path="/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="remove_trajectory",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Trajectory not found"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "Unable to delete trajectory"
        },
    },
)
async def remove_trajectory(
    trajectory_builder: TrajectoryBuilderDep,
    name: str = Path(..., description="Name of the trajectory to delete"),
) -> None:
    """
    Deletes a trajectory by name.
    """
    try:
        trajectory_builder.remove_trajectory(name)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@trajectory_router.get(
    path="/",
    operation_id="list_trajectories",
    response_model=list[TrajectoryObject],
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "Unable to list trajectories"
        },
    },
)
async def list_trajectories(
    trajectory_builder: TrajectoryBuilderDep,
) -> list[TrajectoryObject]:
    """
    Lists all available trajectories within the stage.
    """
    try:
        return trajectory_builder.list_trajectories()
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@trajectory_router.post(
    path="/{name}/markers",
    operation_id="create_trajectory_markers",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Trajectory not found"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "Unable to create trajectory markers"
        },
    },
)
async def create_markers(
    trajectory_builder: TrajectoryBuilderDep,
    name: str = Path(..., description="Name of the trajectory to add markers to"),
    marker_data: TrajectoryMarker = Body(..., description="Marker configuration"),
) -> None:
    """
    Adds visual markers along a trajectory.
    """
    try:
        trajectory_builder.create_marker(name=name, marker_data=marker_data)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@trajectory_router.delete(
    path="/{name}/markers",
    operation_id="remove_trajectory_markers",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Trajectory not found"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "description": "Unable to remove trajectory markers"
        },
    },
)
async def remove_markers(
    trajectory_builder: TrajectoryBuilderDep,
    name: str = Path(..., description="Name of the trajectory to delete markers from"),
) -> None:
    """
    Removes all markers associated with a trajectory.
    """
    try:
        trajectory_builder.remove_markers(name=name)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
