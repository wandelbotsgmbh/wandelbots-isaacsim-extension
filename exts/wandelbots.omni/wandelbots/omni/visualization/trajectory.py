from typing import Annotated

import omni.usd
from pxr import UsdGeom, Gf, Sdf
from pydantic import conlist
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.datatypes import GIZMO_USD_FILE, WSPose
from wandelbots.omni.visualization.models import (
    TrajectoryData,
    TrajectoryObject,
    TrajectoryOptions,
    PatchTrajectoryData,
    TrajectoryMarker,
)


class TrajectoryBuilder:
    def draw_curve(
        self,
        curve_path: str,
        waypoints: list[tuple[float, float, float]],
        width: float,
        color: Gf.Vec3f,
    ):
        stage = omni.usd.get_context().get_stage()
        curve = UsdGeom.BasisCurves.Define(stage, curve_path)
        curve.CreatePointsAttr(waypoints)
        curve.CreateCurveVertexCountsAttr([len(waypoints)])
        curve.CreateTypeAttr(UsdGeom.Tokens.linear)
        curve.CreateBasisAttr(UsdGeom.Tokens.bspline)
        curve.CreateWrapAttr(UsdGeom.Tokens.pinned)
        curve.CreateWidthsAttr([width / 1000] * len(waypoints))
        curve.CreateDisplayColorAttr([color])

    def rgb_to_vec3f(self, rgb: tuple[int, int, int]) -> Gf.Vec3f:
        return Gf.Vec3f(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)

    def _register_trajectory(
        self,
        name: str,
        trajectory_path: str,
        poses: list[Annotated[list[float], conlist(float, min_length=6, max_length=6)]],
        trajectory_options: TrajectoryOptions,
    ):
        stage = omni.usd.get_context().get_stage()
        trajectory_prim = stage.GetPrimAtPath(trajectory_path)

        trajectory_prim.CreateAttribute(
            "trajectory:name", Sdf.ValueTypeNames.String
        ).Set(name)

        flat_poses = [val for pose in poses for val in pose]
        trajectory_prim.CreateAttribute(
            "trajectory:poses", Sdf.ValueTypeNames.FloatArray
        ).Set(flat_poses)

        trajectory_prim.CreateAttribute(
            "trajectory:color", Sdf.ValueTypeNames.Float3
        ).Set(trajectory_options.color)

        trajectory_prim.CreateAttribute(
            "trajectory:width", Sdf.ValueTypeNames.Float
        ).Set(trajectory_options.width)

    def create_trajectory(self, trajectory_data: TrajectoryData):
        stage = omni.usd.get_context().get_stage()
        parent_prim_path = trajectory_data.parent_prim_path

        name = trajectory_data.name
        trajectory_path = f"{parent_prim_path}/trajectories/{name}"
        if stage.GetPrimAtPath(trajectory_path):
            raise ValueError(f"Trajectory '{name}' already exists")

        # create trajectory scope
        stage.DefinePrim(f"{parent_prim_path}/trajectories", "Scope")
        UsdGeom.Xform.Define(stage, trajectory_path)

        # create curve
        color = self.rgb_to_vec3f(trajectory_data.options.color)
        curve_waypoints = [
            (x / 1000, y / 1000, z / 1000) for x, y, z, *_ in trajectory_data.poses
        ]
        curve_path = f"{trajectory_path}/curve"
        self.draw_curve(
            curve_path=curve_path,
            waypoints=curve_waypoints,
            width=trajectory_data.options.width,
            color=color,
        )

        # register trajectory data
        self._register_trajectory(
            name=name,
            trajectory_path=trajectory_path,
            poses=trajectory_data.poses,
            trajectory_options=trajectory_data.options,
        )

    def update_trajectory(self, name: str, trajectory_data: PatchTrajectoryData):
        if not self.is_trajectory_valid(name):
            raise KeyError(f"Trajectory '{name}' not created yet")

        trajectory = self.get_trajectory(name)
        trajectory_path = trajectory.path

        options = trajectory_data.options
        color_rgb = (
            options.color
            if options and options.color is not None
            else trajectory.options.color
        )
        width = (
            options.width
            if options and options.width is not None
            else trajectory.options.width
        )
        poses = (
            trajectory_data.poses
            if trajectory_data.poses is not None
            else trajectory.poses
        )

        color = self.rgb_to_vec3f(color_rgb)

        # draw curve
        curve_waypoints = [(x / 1000, y / 1000, z / 1000) for x, y, z, *_ in poses]
        curve_path = f"{trajectory_path}/curve"
        self.draw_curve(
            curve_path=curve_path, waypoints=curve_waypoints, width=width, color=color
        )

        self._register_trajectory(
            name=name,
            trajectory_path=trajectory_path,
            poses=poses,
            trajectory_options=TrajectoryOptions(
                color=color_rgb,
                width=width,
            ),
        )

    def list_trajectories(self) -> list[TrajectoryObject]:
        stage = omni.usd.get_context().get_stage()
        trajectories = []
        for prim in stage.Traverse():
            if prim.GetTypeName() != "BasisCurves":
                continue
            trajectory_prim = prim.GetParent()
            name = trajectory_prim.GetAttribute("trajectory:name").Get()

            poses_attr = trajectory_prim.GetAttribute("trajectory:poses")
            raw_vals = poses_attr.Get() if poses_attr.IsValid() else []
            poses = list(zip(*[iter(raw_vals)] * 6)) if raw_vals else []

            color = trajectory_prim.GetAttribute("trajectory:color").Get()
            width = trajectory_prim.GetAttribute("trajectory:width").Get()

            trajectories.append(
                TrajectoryObject(
                    name=name,
                    path=trajectory_prim.GetPath().pathString,
                    poses=poses,
                    options=TrajectoryOptions(color=color, width=width),
                )
            )
        return trajectories

    def remove_trajectory(self, name: str):
        if not self.is_trajectory_valid(name):
            raise KeyError(f"Trajectory '{name}' not created yet")
        trajectory = self.get_trajectory(name)
        stage = omni.usd.get_context().get_stage()
        stage.RemovePrim(trajectory.path)

    def get_trajectory(self, name: str) -> TrajectoryObject:
        trajectories = self.list_trajectories()
        trajectory = next((t for t in trajectories if t.name == name), None)
        return trajectory

    def create_marker(self, name: str, marker_data: TrajectoryMarker):
        if not self.is_trajectory_valid(name):
            raise KeyError(f"Trajectory '{name}' not created yet")
        trajectory = self.get_trajectory(name)
        trajectory_path = trajectory.path

        stage = omni.usd.get_context().get_stage()
        markers_base_path = f"{trajectory_path}/markers"
        UsdGeom.Xform.Define(stage, markers_base_path)
        for i, pose in enumerate(marker_data.poses):
            marker_path = f"{markers_base_path}/marker_{i}"
            xform = UsdGeom.Xform.Define(stage, marker_path)
            ops = {op.GetOpName() for op in xform.GetOrderedXformOps()}
            if "xformOp:translate" not in ops:
                xform.AddTranslateOp()
            if "xformOp:orient" not in ops:
                xform.AddOrientOp()

            PrimUtils.set_prim_pose(marker_path, WSPose(pose=list(pose)))
            if marker_data.prim.type == "gizmo":
                xform.GetPrim().GetReferences().AddReference(GIZMO_USD_FILE)
            elif marker_data.prim.type == "custom":
                xform.GetPrim().GetReferences().AddInternalReference(
                    Sdf.Path(marker_data.prim.custom_prim_path)
                )

    def remove_markers(self, name: str):
        if not self.is_trajectory_valid(name):
            raise KeyError(f"Trajectory '{name}' not created yet")
        trajectory = self.get_trajectory(name)
        stage = omni.usd.get_context().get_stage()
        stage.RemovePrim(f"{trajectory.path}/markers")

    def is_trajectory_valid(self, name: str) -> bool:
        trajectory = self.get_trajectory(name)
        if trajectory is None:
            raise KeyError(f"Trajectory '{name}' not created yet")
        return True


def get_trajectory_builder():
    return TrajectoryBuilder()
