"""Shared utilities for creating trajectory pose prims."""

from __future__ import annotations

import carb
import omni.usd
from pxr import Sdf, UsdGeom

from wandelbots.omni.datatypes import GIZMO_USD_FILE


def get_pose_parent_path(stage) -> str:
    """Determine the parent path for a new pose prim based on the current selection."""
    selected = omni.usd.get_context().get_selection().get_selected_prim_paths()
    if selected:
        prim = stage.GetPrimAtPath(selected[0])
        while prim and prim.IsValid() and prim.GetPath() != Sdf.Path("/"):
            if not UsdGeom.Gprim(prim):
                return prim.GetPath().pathString
            prim = prim.GetParent()
    return "/World"


def next_pose_name(stage, parent_path: str) -> str:
    """Generate the next available Pose_XX name under the given parent."""
    parent_prim = stage.GetPrimAtPath(parent_path)
    if not parent_prim or not parent_prim.IsValid():
        return "Pose_01"
    existing = [child.GetName() for child in parent_prim.GetChildren()]
    idx = 1
    while f"Pose_{idx:02d}" in existing:
        idx += 1
    return f"Pose_{idx:02d}"


def embed_gizmo(stage, prim_path: str) -> None:
    """Copy the gizmo.usd content directly into the stage at prim_path."""
    gizmo_layer = Sdf.Layer.FindOrOpen(GIZMO_USD_FILE)
    if not gizmo_layer:
        carb.log_warn(f"Could not open gizmo layer: {GIZMO_USD_FILE}")
        return
    target_layer = stage.GetEditTarget().GetLayer()
    root_prim = gizmo_layer.rootPrims[0] if gizmo_layer.rootPrims else None
    if root_prim is None:
        carb.log_warn("Gizmo USD has no root prim to copy.")
        return
    Sdf.CopySpec(gizmo_layer, root_prim.path, target_layer, Sdf.Path(prim_path))


def create_pose_prim(stage, parent_path: str | None = None) -> str | None:
    """Create a new trajectory pose prim with embedded gizmo and return its path.

    If *parent_path* is ``None`` the parent is derived from the current selection.
    """
    if parent_path is None:
        parent_path = get_pose_parent_path(stage)
    parent_prim = stage.GetPrimAtPath(parent_path)
    if not parent_prim or not parent_prim.IsValid():
        UsdGeom.Xform.Define(stage, parent_path)

    pose_name = next_pose_name(stage, parent_path)
    prim_path = f"{parent_path}/{pose_name}"

    embed_gizmo(stage, prim_path)

    xform = UsdGeom.Xform.Get(stage, prim_path)
    if not xform:
        xform = UsdGeom.Xform.Define(stage, prim_path)
    prim = xform.GetPrim()
    if not prim.HasAttribute("xformOp:translate"):
        xform.AddTranslateOp()
    if not prim.HasAttribute("xformOp:orient"):
        xform.AddOrientOp()
    # An explicit scale op gives a reparent operation a place to write the
    # compensating local scale; without it, moving the pose into a scaled parent
    # cannot preserve its transform and the gizmo jumps/loses its appearance.
    if not prim.HasAttribute("xformOp:scale"):
        xform.AddScaleOp()

    prim.SetCustomDataByKey("wandelbots", {"type": "POSE"})
    prim.SetMetadata("kind", "assembly")
    return prim_path


def set_pose_motion_metadata(
    stage,
    prim_path: str,
    *,
    tcp_name: str | None = None,
) -> None:
    """Persist the user-selected TCP name onto the pose prim.

    Stored in the same ``wandelbots`` customData dict that holds ``type: POSE``
    (kept intact). The selected joint config is persisted separately as the
    ``preferredJointValues`` attribute, mirroring how a ghost object persists
    its choice (see ``GhostObjectUtils.set_preferred_joint_values``).
    """
    if not stage:
        return
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return
    custom_data = dict(prim.GetCustomDataByKey("wandelbots") or {})
    if tcp_name is None:
        custom_data.pop("tcp_name", None)
    else:
        custom_data["tcp_name"] = tcp_name
    prim.SetCustomDataByKey("wandelbots", custom_data)


def get_pose_motion_metadata(prim) -> str | None:
    """Read the stored TCP name from a pose prim, or ``None`` when not stored."""
    if not prim or not prim.IsValid():
        return None
    custom_data = prim.GetCustomDataByKey("wandelbots") or {}
    return custom_data.get("tcp_name")
