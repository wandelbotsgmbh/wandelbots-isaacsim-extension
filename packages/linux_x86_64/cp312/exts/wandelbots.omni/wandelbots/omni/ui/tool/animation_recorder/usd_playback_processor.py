import carb
from pxr import Sdf, Usd, UsdGeom, UsdPhysics

import wandelbots.usd as wb_schema  # type: ignore


class UsdPlaybackProcessor:
    @staticmethod
    def create_playback_stage(
        original_stage: Usd.Stage,
        original_stage_url: str,
        take_url: str,
        output_url: str,
    ) -> None:
        """
        Create a composite stage that layers the recorded take over the original scene.
        The take is the higher-priority sublayer so its time-sampled overrides win.
        Articulations/joints are disabled and rigid bodies set to kinematic for clean playback.
        Motion group APIs are removed so streaming is not attempted on timeline play.
        """
        if not original_stage or not original_stage.GetPseudoRoot().IsValid():
            raise RuntimeError(f"Cannot open original stage: {original_stage_url}")

        # Build the playback layer directly via Sdf (avoids composition issues with in-memory stages)
        layer = Sdf.Layer.CreateAnonymous(".usd")

        layer.subLayerPaths = [take_url, original_stage_url]
        layer.pseudoRoot.SetInfo("upAxis", UsdGeom.GetStageUpAxis(original_stage))
        layer.pseudoRoot.SetInfo(
            "metersPerUnit", UsdGeom.GetStageMetersPerUnit(original_stage)
        )

        _set_frame_range_from_take(layer, take_url)
        _prepare_for_playback(layer, original_stage)

        layer.Export(output_url)
        carb.log_info(f"Created playback stage: {output_url}")


def _set_frame_range_from_take(layer: Sdf.Layer, take_url: str):
    """Read the recorded take's time sample range and set it as the playback stage frame range."""
    take_layer = Sdf.Layer.FindOrOpen(take_url)
    if not take_layer:
        carb.log_warn(f"Could not open take layer to determine frame range: {take_url}")
        return

    start = take_layer.startTimeCode
    end = take_layer.endTimeCode
    if start >= end:
        carb.log_warn(f"Take layer has no valid time range: {start}-{end}")
        return

    layer.startTimeCode = start
    layer.endTimeCode = end
    carb.log_info(f"Set playback frame range: {start} - {end}")


def _prepare_for_playback(layer: Sdf.Layer, original_stage: Usd.Stage):
    """Disable articulations/joints, rigid bodies, motion groups, and action graphs for clean playback."""
    for prim in original_stage.Traverse():
        _disable_articulation(layer, prim)
        _disable_joint(layer, prim)
        _set_rigid_body_kinematic(layer, prim)
        _remove_api_schema(layer, prim, wb_schema.MotionGroupAPI, "MotionGroupAPI")
        _remove_api_schema(layer, prim, wb_schema.ToolAPI, "ToolAPI")
        _remove_api_schema(layer, prim, wb_schema.GhostObjectAPI, "GhostObjectAPI")
        _deactivate_omnigraph(layer, prim)


def _get_or_create_prim_spec(layer: Sdf.Layer, path: Sdf.Path) -> Sdf.PrimSpec:
    """Get or create a PrimSpec (and all ancestor specs) at the given path."""
    spec = layer.GetPrimAtPath(path)
    if spec:
        return spec
    # Ensure parent exists
    parent_path = path.GetParentPath()
    if parent_path != Sdf.Path.absoluteRootPath:
        _get_or_create_prim_spec(layer, parent_path)
    spec = Sdf.PrimSpec(
        layer.GetPrimAtPath(parent_path) or layer.pseudoRoot,
        path.name,
        Sdf.SpecifierOver,
    )
    return spec


def _disable_articulation(layer: Sdf.Layer, prim: Usd.Prim):
    attr = prim.GetAttribute("physxArticulation:articulationEnabled")
    if not attr or not attr.IsValid():
        attr = prim.GetAttribute("physics:articulationEnabled")
    if not attr or not attr.IsValid():
        return
    spec = _get_or_create_prim_spec(layer, prim.GetPath())
    attr_spec = Sdf.AttributeSpec(
        spec, "physxArticulation:articulationEnabled", Sdf.ValueTypeNames.Bool
    )
    attr_spec.default = False


def _disable_joint(layer: Sdf.Layer, prim: Usd.Prim):
    if not prim.IsA(UsdPhysics.Joint):
        return
    spec = _get_or_create_prim_spec(layer, prim.GetPath())
    spec.active = False


def _set_rigid_body_kinematic(layer: Sdf.Layer, prim: Usd.Prim):
    attr = prim.GetAttribute("physics:rigidBodyEnabled")
    if not attr or not attr.IsValid():
        return
    spec = _get_or_create_prim_spec(layer, prim.GetPath())
    attr_spec = Sdf.AttributeSpec(
        spec, "physics:rigidBodyEnabled", Sdf.ValueTypeNames.Bool
    )
    attr_spec.default = False


def _remove_api_schema(layer: Sdf.Layer, prim: Usd.Prim, api_class, token: str):
    if not prim.HasAPI(api_class):
        return
    spec = _get_or_create_prim_spec(layer, prim.GetPath())
    schemas = spec.GetInfo("apiSchemas") or Sdf.TokenListOp()
    schemas.deletedItems = list(schemas.deletedItems) + [token]
    spec.SetInfo("apiSchemas", schemas)


def _deactivate_omnigraph(layer: Sdf.Layer, prim: Usd.Prim):
    if "OmniGraph" not in prim.GetTypeName():
        return
    spec = _get_or_create_prim_spec(layer, prim.GetPath())
    spec.active = False
