from typing import Annotated, Union, TypeVar
import omni.usd
from pxr import Usd, UsdGeom, Gf, Sdf
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

# Type variable for generic helper methods
DataT = TypeVar("DataT")


class TrajectoryBuilder:
    def _draw_per_vertex_data_curve(
        self,
        stage: Usd.Stage,
        curve_path: str,
        waypoints: list[tuple[float, float, float]],
        width: Union[float, list[float]],
        color: Union[Gf.Vec3f, list[Gf.Vec3f]],
    ):
        # Remove any existing curves at this path first (including segments)
        if stage.GetPrimAtPath(curve_path):
            stage.RemovePrim(curve_path)

        # Get the trajectory Xform (parent of the curve_path)
        trajectory_path = "/".join(curve_path.split("/")[:-1])
        trajectory_xform_prim = stage.GetPrimAtPath(trajectory_path)

        # Create a single continuous curve with per-vertex properties
        curve: UsdGeom.BasisCurves = UsdGeom.BasisCurves.Define(stage, curve_path)
        curve.CreatePointsAttr(waypoints)
        curve.CreateCurveVertexCountsAttr([len(waypoints)])
        curve.CreateTypeAttr(UsdGeom.Tokens.linear)

        # Set per-vertex colors using PrimvarsAPI with vertex interpolation
        if isinstance(color, list):
            vertex_colors = self._map_segments_to_vertices(color, len(waypoints))

            curve_prim = curve.GetPrim()
            primvars_api = UsdGeom.PrimvarsAPI(curve_prim)
            color_primvar = primvars_api.CreatePrimvar(
                "displayColor", Sdf.ValueTypeNames.Color3fArray
            )
            color_primvar.Set(vertex_colors)
            color_primvar.SetInterpolation("vertex")  # per-vertex color interpolation
        else:
            curve.CreateDisplayColorAttr([color])

        # Calculate per-vertex widths from per-segment widths
        if isinstance(width, list):
            vertex_widths = self._map_segments_to_vertices(width, len(waypoints))
            # Convert mm to meters
            vertex_widths = [w / 1000 for w in vertex_widths]
            curve.CreateWidthsAttr(vertex_widths)
        else:
            curve.CreateWidthsAttr([width / 1000] * len(waypoints))

        # Group the curve under the trajectory Xform for unified selection
        if trajectory_xform_prim.IsValid():
            # Set the trajectory Xform as the "group" for selection
            trajectory_xform_prim.SetMetadata("kind", "group")

    def draw_curve(
        self,
        curve_path: str,
        waypoints: list[tuple[float, float, float]],
        width: Union[float, list[float]],
        color: Union[Gf.Vec3f, list[Gf.Vec3f]],
    ):
        stage = omni.usd.get_context().get_stage()

        # If at least one properties provides a list of values which are applied along the curve,
        # set the curve structure to one property per vertex instead of on property for the whole curve.
        has_per_segment_color = isinstance(color, list)
        has_per_segment_width = isinstance(width, list)

        if has_per_segment_color or has_per_segment_width:
            self._draw_per_vertex_data_curve(stage, curve_path, waypoints, width, color)
        else:
            # Single curve with uniform color (original behavior)

            # Remove any existing curve at this path first
            if stage.GetPrimAtPath(curve_path):
                stage.RemovePrim(curve_path)

            curve = UsdGeom.BasisCurves.Define(stage, curve_path)
            curve.CreatePointsAttr(waypoints)
            curve.CreateCurveVertexCountsAttr([len(waypoints)])
            curve.CreateTypeAttr(UsdGeom.Tokens.linear)
            curve.CreateWidthsAttr([width / 1000] * len(waypoints))
            curve.CreateDisplayColorAttr([color])

    def rgb_to_vec3f(self, rgb: tuple[int, int, int]) -> Gf.Vec3f:
        return Gf.Vec3f(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)

    def _map_segments_to_vertices(self, segment_data: list, num_vertices: int) -> list:
        """Map per-segment data to per-vertex data for USD curves.

        OpenUSD BasisCurves require per-vertex data for properties like colors and widths,
        but trajectory data is naturally per-segment (the line between two points).
        This method converts N-1 segment values to N vertex values for USD compatibility.

        Example: Triangle with 4 vertices [A,B,C,A] has 3 segments [A→B, B→C, C→A]
        - Input: 3 segment colors [red, green, blue]
        - Output: 4 vertex colors [red, red, green, blue]
        - Result: USD interpolates smoothly between vertices creating gradient effects

        Args:
            segment_data: list of per-segment values (colors, widths, etc.)
                        Length should be num_vertices - 1 for closed curves
            num_vertices: Number of vertices in the curve (points in trajectory)

        Returns:
            list of per-vertex values mapped from segment data
            Length equals num_vertices for direct USD consumption

        USD Technical Note:
            - Uses vertex interpolation (not constant per-segment)
            - First vertex gets first segment data
            - Last vertex gets last segment data
            - Middle vertices get data from the segment they initiate
            - This creates smooth transitions when USD interpolates between vertices
        """
        vertex_data = []
        for i in range(num_vertices):
            if i == 0:
                # First vertex uses first segment data
                vertex_data.append(segment_data[0])
            elif i == num_vertices - 1:
                # Last vertex uses last segment data
                vertex_data.append(
                    segment_data[min(len(segment_data) - 1, num_vertices - 2)]
                )
            else:
                # Middle vertices use the data of the segment they start
                vertex_data.append(segment_data[min(i - 1, len(segment_data) - 1)])
        return vertex_data

    def _normalize_segment_list(
        self,
        data: Union[DataT, list[DataT]],
        target_length: int,
        default_value: DataT,
    ) -> Union[DataT, list[DataT]]:
        """Normalize segment data to match the required number of segments.

        Handles the common pattern of ensuring a list has exactly the right number
        of elements by padding with defaults or truncating excess items.

        Args:
            data: Single value or list of values
            target_length: Required number of segments (list length)
            default_value: Value to use for padding if too few elements

        Returns:
            Single value (if input was single) or list with exactly target_length items

        Examples:
            _normalize_segment_list([1, 2], 4, 0) -> [1, 2, 0, 0]  # pad with defaults
            _normalize_segment_list([1, 2, 3, 4, 5], 3, 0) -> [1, 2, 3]  # truncate excess
            _normalize_segment_list([1, 2, 3], 3, 0) -> [1, 2, 3]  # exact match
            _normalize_segment_list(42, 3, 0) -> 42  # single value unchanged
        """
        # Single value case - return as-is
        if not isinstance(data, list):
            return data

        # list case - normalize length
        if len(data) < target_length:
            # Too few values - pad with defaults
            return data + [default_value] * (target_length - len(data))
        elif len(data) > target_length:
            # Too many values - truncate to required length
            return data[:target_length]
        else:
            # Exact match - return as-is
            return data

    def _process_trajectory_options(
        self, options: TrajectoryOptions, num_segments: int
    ) -> tuple[Union[float, list[float]], Union[Gf.Vec3f, list[Gf.Vec3f]]]:
        """Process trajectory options to handle both single values and per-segment lists"""

        # Process width using the extracted helper method
        width = self._normalize_segment_list(options.width, num_segments, 10.0)

        # Process color using the same helper method, then convert to Vec3f
        if isinstance(options.color, list):
            # Normalize the color list first, then convert each RGB to Vec3f
            normalized_colors = self._normalize_segment_list(
                options.color, num_segments, (255, 255, 255)
            )
            color = [self.rgb_to_vec3f(rgb) for rgb in normalized_colors]
        else:
            # Single color case - convert directly
            color = self.rgb_to_vec3f(options.color)

        return width, color

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

        # Store color - handle both single tuple and list of tuples
        if isinstance(trajectory_options.color, list):
            # Convert RGB tuples to Vec3f and store as array
            color_vec3fs = [
                Gf.Vec3f(r / 255.0, g / 255.0, b / 255.0)
                for r, g, b in trajectory_options.color
            ]
            trajectory_prim.CreateAttribute(
                "trajectory:color", Sdf.ValueTypeNames.Float3Array
            ).Set(color_vec3fs)
            # Store flag to indicate this is per-segment data
            trajectory_prim.CreateAttribute(
                "trajectory:color_per_segment", Sdf.ValueTypeNames.Bool
            ).Set(True)
        else:
            # Store as single color
            r, g, b = trajectory_options.color
            color_vec3f = Gf.Vec3f(r / 255.0, g / 255.0, b / 255.0)
            trajectory_prim.CreateAttribute(
                "trajectory:color", Sdf.ValueTypeNames.Float3
            ).Set(color_vec3f)
            trajectory_prim.CreateAttribute(
                "trajectory:color_per_segment", Sdf.ValueTypeNames.Bool
            ).Set(False)

        # Store width - handle both single value and list of values
        if isinstance(trajectory_options.width, list):
            # Store as array for per-segment widths
            trajectory_prim.CreateAttribute(
                "trajectory:width", Sdf.ValueTypeNames.FloatArray
            ).Set(trajectory_options.width)
            # Store flag to indicate this is per-segment data
            trajectory_prim.CreateAttribute(
                "trajectory:width_per_segment", Sdf.ValueTypeNames.Bool
            ).Set(True)
        else:
            # Store as single width
            trajectory_prim.CreateAttribute(
                "trajectory:width", Sdf.ValueTypeNames.Float
            ).Set(trajectory_options.width)
            trajectory_prim.CreateAttribute(
                "trajectory:width_per_segment", Sdf.ValueTypeNames.Bool
            ).Set(False)

    def create_trajectory(self, trajectory_data: TrajectoryData):
        stage = omni.usd.get_context().get_stage()
        parent_prim_path = trajectory_data.parent_prim_path

        name = trajectory_data.name
        trajectory_path = f"{parent_prim_path}/trajectories/{name}"
        curve_path = f"{trajectory_path}/curve"
        if stage.GetPrimAtPath(curve_path):
            raise ValueError(f"Trajectory '{name}' already exists")

        # create trajectory scope
        stage.DefinePrim(f"{parent_prim_path}/trajectories", "Scope")
        if not stage.GetPrimAtPath(trajectory_path):
            UsdGeom.Xform.Define(stage, trajectory_path)

        # Calculate number of segments
        num_segments = len(trajectory_data.poses) - 1
        if num_segments < 1:
            raise ValueError("Trajectory must have at least 2 poses")

        # Process trajectory options (handles both single values and per-segment lists)
        width, color = self._process_trajectory_options(
            trajectory_data.options, num_segments
        )

        # create curve
        curve_waypoints = [
            (x / 1000, y / 1000, z / 1000) for x, y, z, *_ in trajectory_data.poses
        ]

        self.draw_curve(
            curve_path=curve_path,
            waypoints=curve_waypoints,
            width=width,
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
        is_valid, error_msg = self.is_trajectory_valid(name)
        if not is_valid:
            raise KeyError(error_msg or f"Trajectory '{name}' not created yet")

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

        # Calculate number of segments
        num_segments = len(poses) - 1
        if num_segments < 1:
            raise ValueError("Trajectory must have at least 2 poses")

        # Create updated options object for processing
        updated_options = TrajectoryOptions(color=color_rgb, width=width)

        # Process trajectory options (handles both single values and per-segment lists)
        processed_width, processed_color = self._process_trajectory_options(
            updated_options, num_segments
        )

        # draw curve
        curve_waypoints = [(x / 1000, y / 1000, z / 1000) for x, y, z, *_ in poses]
        curve_path = f"{trajectory_path}/curve"
        self.draw_curve(
            curve_path=curve_path,
            waypoints=curve_waypoints,
            width=processed_width,
            color=processed_color,
        )

        self._register_trajectory(
            name=name,
            trajectory_path=trajectory_path,
            poses=poses,
            trajectory_options=updated_options,
        )

    def list_trajectories(self) -> list[TrajectoryObject]:
        stage = omni.usd.get_context().get_stage()
        trajectories = []
        processed_names = set()  # Track processed trajectory names to avoid duplicates

        for prim in stage.Traverse():
            if prim.GetTypeName() != "BasisCurves":
                continue

            # Check if this is a trajectory curve (should be direct child of trajectory Xform)
            trajectory_prim = prim.GetParent()

            # Check if this prim has trajectory attributes
            if not trajectory_prim.IsValid():
                continue

            name_attr = trajectory_prim.GetAttribute("trajectory:name")
            if not name_attr.IsValid():
                continue

            name = name_attr.Get()
            if not name or name in processed_names:
                continue

            processed_names.add(name)

            poses_attr = trajectory_prim.GetAttribute("trajectory:poses")
            raw_vals = poses_attr.Get() if poses_attr.IsValid() else []
            poses = list(zip(*[iter(raw_vals)] * 6)) if raw_vals else []

            # Read color data - handle both single and per-segment formats
            color_per_segment_attr = trajectory_prim.GetAttribute(
                "trajectory:color_per_segment"
            )
            color_per_segment = (
                color_per_segment_attr.Get()
                if color_per_segment_attr.IsValid()
                else False
            )

            if color_per_segment:
                # Read per-segment colors as Vec3f array and convert back to RGB tuples
                color_attr = trajectory_prim.GetAttribute("trajectory:color")
                color_vec3fs = color_attr.Get() if color_attr.IsValid() else []
                # Convert Vec3f back to RGB tuples (0-255 range)
                color = (
                    [
                        (int(vec3f[0] * 255), int(vec3f[1] * 255), int(vec3f[2] * 255))
                        for vec3f in color_vec3fs
                    ]
                    if color_vec3fs
                    else [(255, 255, 255)]
                )
            else:
                # Read single color as Vec3f and convert back to RGB tuple
                color_attr = trajectory_prim.GetAttribute("trajectory:color")
                color_vec3f = (
                    color_attr.Get()
                    if color_attr.IsValid()
                    else Gf.Vec3f(1.0, 1.0, 1.0)
                )
                color = (
                    int(color_vec3f[0] * 255),
                    int(color_vec3f[1] * 255),
                    int(color_vec3f[2] * 255),
                )

            # Read width data - handle both single and per-segment formats
            width_per_segment_attr = trajectory_prim.GetAttribute(
                "trajectory:width_per_segment"
            )
            width_per_segment = (
                width_per_segment_attr.Get()
                if width_per_segment_attr.IsValid()
                else False
            )

            if width_per_segment:
                # Read per-segment widths
                width_attr = trajectory_prim.GetAttribute("trajectory:width")
                width = width_attr.Get() if width_attr.IsValid() else [20.0]
            else:
                # Read single width
                width_attr = trajectory_prim.GetAttribute("trajectory:width")
                width = width_attr.Get() if width_attr.IsValid() else 20.0

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
        is_valid, error_msg = self.is_trajectory_valid(name)
        if not is_valid:
            raise KeyError(error_msg or f"Trajectory '{name}' not created yet")
        trajectory = self.get_trajectory(name)
        stage = omni.usd.get_context().get_stage()
        stage.RemovePrim(trajectory.path)

    def get_trajectory(self, name: str) -> TrajectoryObject:
        trajectories = self.list_trajectories()
        trajectory = next((t for t in trajectories if t.name == name), None)
        if trajectory is None:
            raise KeyError(f"Trajectory '{name}' not found")
        return trajectory

    def create_marker(self, name: str, marker_data: TrajectoryMarker):
        is_valid, error_msg = self.is_trajectory_valid(name)
        if not is_valid:
            raise KeyError(error_msg or f"Trajectory '{name}' not created yet")
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
        is_valid, error_msg = self.is_trajectory_valid(name)
        if not is_valid:
            raise KeyError(error_msg or f"Trajectory '{name}' not created yet")
        trajectory = self.get_trajectory(name)
        stage = omni.usd.get_context().get_stage()
        stage.RemovePrim(f"{trajectory.path}/markers")

    def is_trajectory_valid(self, name: str) -> tuple[bool, str | None]:
        """Check if a trajectory exists and is valid.

        Args:
            name: Name of the trajectory to check

        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if trajectory exists and is valid, False otherwise
            - error_message: None if valid, error description if invalid
        """
        try:
            self.get_trajectory(name)
            return True, None
        except KeyError as e:
            return False, str(e)


def get_trajectory_builder():
    return TrajectoryBuilder()
