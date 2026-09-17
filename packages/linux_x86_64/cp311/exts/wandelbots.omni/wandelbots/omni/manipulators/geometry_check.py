"""Check a connected robot's geometry against the model NOVA has configured.

The wrong robot variant still connects and streams, so the only symptom is that
every pose sits a fixed distance from where the scene has it. A KUKA R2700 in
place of an R2900 is 200 mm at the flange.

Two comparisons run, because a pose can be wrong for two reasons. The scene's
robot is compared against NOVA's model, which catches the wrong variant in the
scene. NOVA's model is then compared against NOVA's own kinematics, which
catches an asset whose flange frame disagrees with the arm it describes: the
FANUC M-900iB/400L ships with its flange rotated 180 degrees about the tool
axis, and everything the extension derives from that prim inherits the turn.

Only authored kinematics are compared, plus one forward-kinematics call with
every joint at zero. Nothing is read from the live transforms or the joint
state, so the answer does not depend on the timeline, on physics having run, or
on whether the stage is backed by Fabric.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass

import carb
import wandelbots_api_client.v2 as wb_v2
from pxr import Gf, Usd, UsdGeom, UsdPhysics
from wandelbots_api_client.v2.exceptions import ApiException

from wandelbots.omni.manipulators.motion_group import MotionGroupConfiguration
from wandelbots.omni.manipulators.utils import (
    get_flange_from_motion_group_prim,
    get_link_0_from_motion_group_prim,
)
from wandelbots.omni.utils.api import get_api_client_from_config
from wandelbots.omni.utils.math import (
    matrix_to_rotvec,
    quat_to_rotvec,
    rotvec_to_matrix,
)
from wandelbots.omni.utils.prims import PrimUtils
from wandelbots.omni.utils.scene import SceneUtils

#: Above rounding in the authored values, far below a variant step, which is
#: 200 mm between the KUKA QUANTEC reaches.
GEOMETRY_TOLERANCE_MILLIMETERS = 1.0

#: A turned flange turns every taught pose, so this only has to clear the
#: rounding in the authored orientation.
FLANGE_ROTATION_TOLERANCE_DEGREES = 0.5

#: Both mismatches have the same consequence, so they say it in the same
#: words. What differs is which two things disagree, not how bad it is.
_CONSEQUENCE = "Poses taught in the scene will not match the robot."

#: Model summaries are a few numbers but cost a multi-megabyte download, and a
#: model's geometry does not change while Isaac Sim runs. Keyed by (host, model)
#: because the asset comes from the connected instance, so two hosts can serve
#: different revisions under the same model name.
_model_geometries: dict[tuple[str, str], "ModelGeometry | None"] = {}


@dataclass(frozen=True)
class Chain:
    """The authored kinematics of one arm, in millimetres.

    ``pivots`` is the joint rotation centre per joint, in ``joint_<n>`` order.
    ``flange`` is the flange offset inside the last link. Together they are what
    makes an R2700 a different robot from an R2900.
    """

    pivots: tuple[tuple[float, float, float], ...]
    flange: tuple[float, float, float]


@dataclass(frozen=True)
class FlangeFrame:
    """The flange in the robot's own base frame, with every joint at zero.

    ``position_millimeters`` and ``rotation``, a rotation vector in radians,
    are the two halves NOVA needs to place a taught pose. An asset that
    disagrees with the kinematics in either half turns or shifts every pose the
    extension derives from the scene.
    """

    position_millimeters: tuple[float, float, float]
    rotation: tuple[float, float, float]


@dataclass(frozen=True)
class ModelGeometry:
    """What one model download tells us: its chain and its flange frame."""

    chain: Chain
    flange_frame: FlangeFrame | None


def read_chain(stage: Usd.Stage, root_path: str) -> Chain | None:
    """Summarise the arm rooted at *root_path*, or ``None`` if it has no joints.

    Lengths are converted with the stage's own units, because the scene and the
    downloaded model are separate stages that need not be authored at the same
    scale, and the two chains are compared against each other.
    """
    root = stage.GetPrimAtPath(root_path)
    if not root or not root.IsValid():
        return None

    pivots: dict[int, tuple[float, float, float]] = {}
    flange: tuple[float, float, float] | None = None
    for prim in Usd.PrimRange(root):
        if prim.GetName() == "tcp_flange":
            for op in UsdGeom.Xformable(prim).GetOrderedXformOps():
                if op.GetOpName() == "xformOp:translate":
                    flange = tuple(
                        SceneUtils.value_to_millimeters(float(value), stage)
                        for value in op.Get()
                    )
        if not (
            prim.IsA(UsdPhysics.RevoluteJoint) or prim.IsA(UsdPhysics.PrismaticJoint)
        ):
            continue
        # An exact joint_<n>, so the hose package's support_joint_0 is not read
        # as joint_0 and does not collide with the arm's own numbering.
        name = prim.GetName()
        suffix = name[len("joint_") :] if name.startswith("joint_") else ""
        if not suffix.isdigit():
            continue
        position = UsdPhysics.Joint(prim).GetLocalPos0Attr().Get()
        if position is not None:
            pivots[int(suffix)] = tuple(
                SceneUtils.value_to_millimeters(float(value), stage)
                for value in position
            )

    if not pivots or flange is None:
        return None
    return Chain(pivots=tuple(pivots[index] for index in sorted(pivots)), flange=flange)


def read_flange_frame(stage: Usd.Stage, root_path: str) -> FlangeFrame | None:
    """The flange frame authored under *root_path*, relative to that root.

    Read straight from the authored transforms rather than through
    ``PrimUtils``, so it also works on a model stage that is not the one Isaac
    Sim has open, and so physics can never answer instead of USD.
    """
    root = stage.GetPrimAtPath(root_path)
    if not root or not root.IsValid():
        return None

    flange = None
    for prim in Usd.PrimRange(root):
        if prim.GetName() == "tcp_flange":
            flange = prim
            break
    if flange is None:
        return None

    time = Usd.TimeCode.Default()
    root_transform = UsdGeom.Xformable(root).ComputeLocalToWorldTransform(time)
    flange_transform = UsdGeom.Xformable(flange).ComputeLocalToWorldTransform(time)
    relative: Gf.Matrix4d = flange_transform * root_transform.GetInverse()
    if not relative.Orthonormalize():
        carb.log_warn(f"Flange transform for {flange.GetPath()} is not orthonormal.")

    quaternion = relative.ExtractRotation().GetQuaternion()
    real, (x, y, z) = quaternion.GetReal(), quaternion.GetImaginary()
    return FlangeFrame(
        position_millimeters=tuple(
            SceneUtils.value_to_millimeters(value, stage)
            for value in relative.ExtractTranslation()
        ),
        rotation=tuple(quat_to_rotvec(x, y, z, real)),
    )


def flange_offset_millimeters(
    asset: FlangeFrame, kinematics: FlangeFrame
) -> tuple[float, float, float]:
    """Where the kinematics put the flange relative to where the asset does."""
    return tuple(
        k - a
        for a, k in zip(asset.position_millimeters, kinematics.position_millimeters)
    )


def flange_rotation_degrees(asset: FlangeFrame, kinematics: FlangeFrame) -> float:
    """The angle between the two flange frames."""
    asset_rotation = rotvec_to_matrix(*asset.rotation)
    kinematics_rotation = rotvec_to_matrix(*kinematics.rotation)
    difference = matrix_to_rotvec(asset_rotation.T @ kinematics_rotation)
    return math.degrees(math.hypot(*difference))


def chain_offset_millimeters(scene: Chain, nova: Chain) -> tuple[float, float, float]:
    """Where NOVA's flange sits relative to the scene's, with the arms at rest.

    At rest every link sits at the arm's origin, so the flange's offset inside
    the last link is already its position from the base. That is the number a
    wrong variant moves, and it is what every pose downstream inherits.
    """
    return tuple(n - s for s, n in zip(scene.flange, nova.flange))


def pivots_differ(scene: Chain, nova: Chain) -> bool:
    """Whether any joint sits somewhere else, even if the flange happens to agree."""
    return any(
        abs(n - s) > GEOMETRY_TOLERANCE_MILLIMETERS
        for scene_pivot, nova_pivot in zip(scene.pivots, nova.pivots)
        for s, n in zip(scene_pivot, nova_pivot)
    )


@dataclass(frozen=True)
class GeometryCheck:
    """How far NOVA's arm is from the scene's, for one motion group."""

    model_name: str
    offset_millimeters: tuple[float, float, float]
    joint_count_differs: bool = False
    joints_moved: bool = False
    kinematics_offset_millimeters: tuple[float, float, float] = (0.0, 0.0, 0.0)
    kinematics_rotation_degrees: float = 0.0

    @property
    def scene_matches(self) -> bool:
        """Whether the scene holds the robot NOVA has configured."""
        if self.joint_count_differs or self.joints_moved:
            return False
        distance = math.dist((0.0, 0.0, 0.0), self.offset_millimeters)
        return distance <= GEOMETRY_TOLERANCE_MILLIMETERS

    @property
    def kinematics_match(self) -> bool:
        """Whether NOVA's model agrees with NOVA's own kinematics."""
        if self.kinematics_rotation_degrees > FLANGE_ROTATION_TOLERANCE_DEGREES:
            return False
        distance = math.dist((0.0, 0.0, 0.0), self.kinematics_offset_millimeters)
        return distance <= GEOMETRY_TOLERANCE_MILLIMETERS

    @property
    def matches(self) -> bool:
        return self.scene_matches and self.kinematics_match

    def warning(self) -> str:
        """Message for a mismatch, empty when the geometry agrees."""
        if self.matches:
            return ""
        # The scene comes first: it is the half the user can put right.
        return self._scene_warning() or self._kinematics_warning()

    def _kinematics_warning(self) -> str:
        if self.kinematics_match:
            return ""
        if self.kinematics_rotation_degrees > FLANGE_ROTATION_TOLERANCE_DEGREES:
            turn = int(round(self.kinematics_rotation_degrees))
            difference = f"flange rotated {turn} deg"
        else:
            x, y, z = (
                int(round(value)) for value in self.kinematics_offset_millimeters
            )
            difference = f"flange off by ({x}, {y}, {z}) mm"
        return (
            f"{self.model_name} asset does not match NOVA kinematics: "
            f"{difference}. {_CONSEQUENCE}"
        )

    def _scene_warning(self) -> str:
        if self.scene_matches:
            return ""
        if self.joint_count_differs:
            difference = "it has a different number of joints"
        else:
            # int() so a rounded -0.0 does not print as "-0".
            x, y, z = (int(round(value)) for value in self.offset_millimeters)
            difference = (
                "its joints sit in different places"
                if (x, y, z) == (0, 0, 0)
                else f"flange off by ({x}, {y}, {z}) mm"
            )
        return (
            f"Scene robot does not match {self.model_name}: "
            f"{difference}. {_CONSEQUENCE}"
        )


async def _nova_model_geometry(
    api_client, host: str, model_name: str
) -> ModelGeometry | None:
    """The model's authored geometry, downloaded once per host and model."""
    cache_key = (host, model_name)
    if cache_key in _model_geometries:
        return _model_geometries[cache_key]

    usd_bytes = await wb_v2.MotionGroupModelsApi(api_client).get_motion_group_usd_model(
        motion_group_model=model_name
    )
    # Written out because the model ships as a binary crate, which cannot be
    # parsed from a string.
    handle, path = tempfile.mkstemp(suffix=".usd")
    geometry = None
    try:
        with os.fdopen(handle, "wb") as model_file:
            model_file.write(bytes(usd_bytes))
        stage = Usd.Stage.Open(path)
        if stage and stage.GetDefaultPrim():
            root_path = stage.GetDefaultPrim().GetPath().pathString
            chain = read_chain(stage, root_path)
            if chain is not None:
                geometry = ModelGeometry(
                    chain=chain, flange_frame=read_flange_frame(stage, root_path)
                )
    finally:
        os.unlink(path)

    _model_geometries[cache_key] = geometry
    return geometry


async def _nova_flange_frame(
    api_client, cell: str, model_name: str, joint_count: int
) -> FlangeFrame | None:
    """Where NOVA's kinematics put the flange with every joint at zero.

    No mounting and no TCP offset, so the answer is in the robot's own base
    frame, which is the frame the asset authors its flange in.
    """
    try:
        response = await wb_v2.KinematicsApi(api_client).forward_kinematics(
            cell=cell,
            forward_kinematics_request=wb_v2.models.ForwardKinematicsRequest(
                motion_group_model=model_name,
                joint_positions=[[0.0] * joint_count],
            ),
        )
    except ApiException as exc:
        carb.log_info(f"Flange kinematics unavailable for {model_name}: {exc}")
        return None

    poses = response.tcp_poses or []
    if not poses:
        return None
    return FlangeFrame(
        position_millimeters=tuple(float(value) for value in poses[0].position),
        rotation=tuple(float(value) for value in poses[0].orientation),
    )


async def check_motion_group_geometry(
    config: MotionGroupConfiguration,
) -> GeometryCheck | None:
    """Compare the connected robot against NOVA's model and its kinematics.

    ``None`` when the check cannot run - no flange, no joints, or the instance
    did not answer. Not knowing is not a warning.
    """
    prim = PrimUtils.get_prim(config.prim_path)
    if prim is None or not prim.IsValid():
        return None
    if get_link_0_from_motion_group_prim(prim, fallback_to_motion_group=False) is None:
        return None
    if get_flange_from_motion_group_prim(prim) is None:
        return None

    scene_chain = read_chain(prim.GetStage(), config.prim_path)
    if scene_chain is None:
        return None

    stream = config.motion_stream_configuration
    try:
        async with get_api_client_from_config(
            stream.get_api_configuration()
        ) as api_client:
            description = await wb_v2.MotionGroupApi(
                api_client
            ).get_motion_group_description(
                cell=stream.cell,
                controller=stream.controller,
                motion_group=stream.motion_group,
            )
            model_name = description.motion_group_model
            if not model_name:
                return None
            model = await _nova_model_geometry(api_client, stream.host, model_name)
            if model is None:
                return None
            kinematics_frame = await _nova_flange_frame(
                api_client, stream.cell, model_name, len(model.chain.pivots)
            )
    except ApiException as exc:
        carb.log_info(f"Robot geometry check skipped, NOVA did not answer: {exc}")
        return None

    kinematics_offset = (0.0, 0.0, 0.0)
    kinematics_rotation = 0.0
    if model.flange_frame is not None and kinematics_frame is not None:
        kinematics_offset = flange_offset_millimeters(
            model.flange_frame, kinematics_frame
        )
        kinematics_rotation = flange_rotation_degrees(
            model.flange_frame, kinematics_frame
        )

    nova_chain = model.chain
    if len(nova_chain.pivots) != len(scene_chain.pivots):
        return GeometryCheck(
            model_name=model_name,
            offset_millimeters=(0.0, 0.0, 0.0),
            joint_count_differs=True,
            kinematics_offset_millimeters=kinematics_offset,
            kinematics_rotation_degrees=kinematics_rotation,
        )
    return GeometryCheck(
        model_name=model_name,
        offset_millimeters=chain_offset_millimeters(scene_chain, nova_chain),
        joints_moved=pivots_differ(scene_chain, nova_chain),
        kinematics_offset_millimeters=kinematics_offset,
        kinematics_rotation_degrees=kinematics_rotation,
    )
