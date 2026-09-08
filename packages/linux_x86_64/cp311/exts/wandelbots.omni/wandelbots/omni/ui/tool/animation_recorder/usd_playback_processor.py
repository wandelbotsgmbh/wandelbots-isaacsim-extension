import carb
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

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

        playback_stage = Usd.Stage.Open(layer)
        if not playback_stage:
            raise RuntimeError("Failed to compose playback stage for flatten export")

        _fix_recorded_xform_op_order(layer, playback_stage, original_stage, take_url)
        _pin_ghost_objects_world_pose(playback_stage, original_stage)

        flattened_layer = playback_stage.Flatten()
        flattened_layer.Export(output_url)
        carb.log_info(f"Created flattened playback stage: {output_url}")
        _log_external_asset_dependencies(output_url)


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
    # The take's time samples are authored at its own recording rate. Without
    # this, the composed layer falls back to Sdf's default timeCodesPerSecond
    # (24), and USD auto-rescales the take's time samples to compensate,
    # pushing the recorded motion outside the start/end range set above.
    layer.timeCodesPerSecond = take_layer.timeCodesPerSecond
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


_RESET_XFORM_STACK_TOKEN = "!resetXformStack!"


def _fix_recorded_xform_op_order(
    layer: Sdf.Layer,
    playback_stage: Usd.Stage,
    original_stage: Usd.Stage,
    take_url: str,
):
    """Point xformOpOrder at the take's recorded matrix op.

    Stage Recorder authors the recorded motion into xformOp:transform.timeSamples
    but leaves xformOpOrder pointing at the original translate/orient/scale ops,
    so it is never applied. Only prims the take itself records (checked on the
    take opened alone) are touched.

    xformOpOrder composes as one array, so by composition time the take's order
    (missing any reset-stack token) has already overridden the original scene's -
    falls back to the original scene's own prim for that flag.

    Stage Recorder authors only `over` for a recorded prim's ancestors, so
    opening the take alone reads every prim as undefined and Traverse() visits
    none; Usd.PrimRange.AllPrims ignores definedness.
    """
    take_stage = Usd.Stage.Open(take_url)
    if not take_stage:
        carb.log_warn(
            f"Could not open take stage to check for recorded transforms: {take_url}"
        )
        return

    for take_prim in Usd.PrimRange.AllPrims(take_stage.GetPseudoRoot()):
        transform_attr = take_prim.GetAttribute("xformOp:transform")
        if not transform_attr.IsValid() or not transform_attr.HasAuthoredValueOpinion():
            continue
        target = playback_stage.GetPrimAtPath(take_prim.GetPath())
        if not target or not target.IsValid():
            continue

        reset_xform_stack = UsdGeom.Xformable(take_prim).GetResetXformStack()
        if not reset_xform_stack:
            source_prim = original_stage.GetPrimAtPath(take_prim.GetPath())
            if source_prim and source_prim.IsValid():
                reset_xform_stack = UsdGeom.Xformable(source_prim).GetResetXformStack()

        new_order = ["xformOp:transform"]
        if reset_xform_stack:
            new_order = [_RESET_XFORM_STACK_TOKEN, *new_order]

        order_attr = target.GetAttribute(UsdGeom.Tokens.xformOpOrder)
        current_order = list(order_attr.Get() or []) if order_attr.IsValid() else []
        if current_order == new_order:
            continue
        spec = _get_or_create_prim_spec(layer, target.GetPath())
        order_spec = Sdf.AttributeSpec(
            spec, UsdGeom.Tokens.xformOpOrder, Sdf.ValueTypeNames.TokenArray
        )
        order_spec.default = new_order


def _pin_ghost_objects_world_pose(playback_stage: Usd.Stage, original_stage: Usd.Stage):
    """Bake each ghost object's taught world pose into a reset-stack transform op.

    A ghost object's pose is authored as a local transform under an animated robot
    link, so its placement depends on parent composition and xform-op ordering.
    Isaac Sim composes this correctly, but Blender's USD import does not, leaving
    the ghost misplaced. Collapsing the taught world pose into a single
    ``xformOp:transform`` matrix with the xform stack reset makes the placement
    independent of the parent hierarchy and op ordering, so it renders correctly
    everywhere.
    """
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    for prim in original_stage.Traverse():
        if not prim.HasAPI(wb_schema.GhostObjectAPI):
            continue
        target = playback_stage.GetPrimAtPath(prim.GetPath())
        if not target or not target.IsValid():
            continue
        xformable = UsdGeom.Xformable(target)
        if not xformable:
            continue
        world_xform = cache.GetLocalToWorldTransform(prim)
        xformable.ClearXformOpOrder()
        xformable.SetResetXformStack(True)
        xformable.AddTransformOp().Set(Gf.Matrix4d(world_xform))


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


def _log_external_asset_dependencies(stage_url: str):
    """Report authored external asset paths that remain in the exported stage."""
    stage = Usd.Stage.Open(stage_url)
    if not stage:
        carb.log_warn(
            f"Could not reopen exported stage for dependency validation: {stage_url}"
        )
        return

    dependencies = _collect_external_asset_dependencies(stage)
    if not dependencies:
        carb.log_info("Export validation: no external asset dependencies found.")
        return

    preview_count = 12
    preview = "\n".join(f"  - {path}" for path in dependencies[:preview_count])
    extra = ""
    if len(dependencies) > preview_count:
        extra = f"\n  ... and {len(dependencies) - preview_count} more"

    carb.log_warn(
        "Export validation: flattened USD still contains external asset "
        f"dependencies ({len(dependencies)}).\n{preview}{extra}"
    )


def _collect_external_asset_dependencies(stage: Usd.Stage) -> list[str]:
    dependencies: set[str] = set()
    for prim in stage.Traverse():
        for attr in prim.GetAttributes():
            if not attr.IsValid() or not attr.HasAuthoredValueOpinion():
                continue

            _collect_asset_paths_from_value(attr.Get(), dependencies)
            for sample_time in attr.GetTimeSamples():
                _collect_asset_paths_from_value(attr.Get(sample_time), dependencies)

    return sorted(dependencies)


def _collect_asset_paths_from_value(value, out_paths: set[str]):
    if value is None:
        return

    if isinstance(value, Sdf.AssetPath):
        _add_if_external_asset_path(value.path, out_paths)
        _add_if_external_asset_path(value.resolvedPath, out_paths)
        return

    if isinstance(value, str):
        return

    if isinstance(value, tuple | list):
        for item in value:
            _collect_asset_paths_from_value(item, out_paths)
        return

    if hasattr(value, "__iter__"):
        for item in value:
            _collect_asset_paths_from_value(item, out_paths)


def _add_if_external_asset_path(path: str, out_paths: set[str]):
    if not path:
        return

    if path.startswith("anon:"):
        return

    out_paths.add(path)
