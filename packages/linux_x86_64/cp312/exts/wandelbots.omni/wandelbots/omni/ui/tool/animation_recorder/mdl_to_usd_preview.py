"""Convert a USD scene's materials from Omniverse MDL shaders to UsdPreviewSurface.

Adapted from the standalone USD_Preview_Converter tool so a recorded playback
stage renders correctly in Blender and other UsdPreviewSurface-aware renderers.
MDL is procedural and cannot be fully expressed by UsdPreviewSurface, so this
produces the closest faithful approximation (base colour, metallic, roughness,
opacity, emission). When an MDL shader carries no explicit colour constant the
colour is inferred from the material / MDL name.
"""

from __future__ import annotations

import carb
from pxr import Gf, Sdf, Usd, UsdShade

try:
    from pxr import Sdr
except ImportError:
    Sdr = None

PREVIEW_SHADER_NAME = "preview_Principled_BSDF"

# MDL input names that may carry the base/albedo colour, in priority order.
COLOR_INPUTS = (
    "diffuse_color_constant",
    "diffuse_reflection_color",
    "diffuse_tint",
    "base_color_constant",
    "Base_Color",
    "albedo_base_color",
    "base_color",
    "albedo",
    "tint_color",
    "color",
)

# Base/albedo colour inputs (excluding tint), in priority order.
BASE_COLOR_INPUTS = (
    "diffuse_color_constant",
    "diffuse_reflection_color",
    "base_color_constant",
    "Base_Color",
    "albedo_base_color",
    "base_color",
    "albedo",
    "color",
)

# Tint inputs are multiplied onto the base colour (OmniPBR "Color Tint"); a
# black-tinted surface only reads correctly when the tint is applied.
TINT_INPUTS = ("diffuse_tint", "albedo_tint", "tint_color")

# Alternative MDL input names for the remaining PBR scalars, in priority order.
METALLIC_INPUTS = ("metallic_constant", "metallic", "metalness")
ROUGHNESS_INPUTS = (
    "reflection_roughness_constant",
    "roughness",
    "reflection_roughness",
)
OPACITY_INPUTS = ("opacity_constant", "opacity", "geometry_opacity")

# Substrings used to recognise an unfamiliar colour-carrying input by name, and
# substrings that disqualify a name even when it contains "color" (e.g. an
# emissive or specular colour must not be mistaken for the base colour).
_COLOR_NAME_HINTS = ("diffuse", "albedo", "base_color", "basecolor", "tint", "color")
_COLOR_NAME_EXCLUDE = (
    "rough",
    "metal",
    "emiss",
    "spec",
    "sheen",
    "coat",
    "transmit",
    "sss",
    "subsurface",
    "scatter",
    "absorption",
    "opacity",
    "normal",
    "displacement",
    "occlusion",
)


def to_vec3(value) -> Gf.Vec3f | None:
    if value is None:
        return None
    try:
        if len(value) >= 3:
            return Gf.Vec3f(float(value[0]), float(value[1]), float(value[2]))
    except TypeError:
        pass
    return None


def color_from_name(name: str) -> Gf.Vec3f | None:
    """Infer a diffuse colour from a material / MDL identifier name."""
    n = (name or "").lower()
    table = [
        (("dark_blue", "darkblue", "navy"), (0.02, 0.03, 0.18)),
        (("blue",), (0.05, 0.10, 0.45)),
        (("dark_red", "darkred"), (0.25, 0.02, 0.02)),
        (("anodized_red", "_red", "red"), (0.50, 0.04, 0.04)),
        (("orange",), (0.85, 0.35, 0.02)),
        (("yellow", "solder_mask"), (0.82, 0.68, 0.05)),
        (("green",), (0.05, 0.40, 0.10)),
        (("darkgrey", "dark_grey", "dark_gray"), (0.18, 0.18, 0.18)),
        (("black",), (0.02, 0.02, 0.02)),
        (("white", "porcelain"), (0.90, 0.90, 0.88)),
        (("concrete",), (0.58, 0.56, 0.53)),
        (("marble",), (0.88, 0.87, 0.84)),
        (("slate",), (0.20, 0.21, 0.23)),
        (("rubber",), (0.04, 0.04, 0.04)),
        (("steel", "stainless", "chrome"), (0.55, 0.56, 0.57)),
        (("iron",), (0.35, 0.35, 0.36)),
        (("aluminum", "aluminium"), (0.60, 0.60, 0.62)),
    ]
    for keys, rgb in table:
        if any(k in n for k in keys):
            return Gf.Vec3f(*rgb)
    return None


def iter_shaders(material_prim: Usd.Prim):
    """Yield every Shader prim beneath a material (any nesting depth)."""
    for prim in Usd.PrimRange(material_prim):
        if prim == material_prim:
            continue
        if prim.IsA(UsdShade.Shader):
            yield prim


def find_existing_preview(material_prim: Usd.Prim) -> Usd.Prim | None:
    for prim in iter_shaders(material_prim):
        shd = UsdShade.Shader(prim)
        sid = shd.GetIdAttr().Get() if shd.GetIdAttr() else None
        if sid == "UsdPreviewSurface":
            return prim
    return None


def get_input_value(shd: UsdShade.Shader, name: str):
    inp = shd.GetInput(name)
    if inp:
        return inp.Get()
    return None


def get_float(shd: UsdShade.Shader, name: str) -> float | None:
    v = get_input_value(shd, name)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _looks_like_color_name(name: str) -> bool:
    n = (name or "").lower()
    if any(x in n for x in _COLOR_NAME_EXCLUDE):
        return False
    return any(x in n for x in _COLOR_NAME_HINTS)


def _sdr_node_for_shader(shd: UsdShade.Shader):
    """Resolve the SdrShaderNode for an MDL shader so preset defaults are readable.

    Omniverse/vMaterials preset materials (e.g. ``Plastic_ABS_02``) bake their
    colour and roughness as defaults inside the ``.mdl`` preset function rather
    than authoring them as USD shader inputs. The SDR registry parses the MDL and
    exposes those defaults, letting us recover the real appearance without an MDL
    renderer. Returns None when SDR or the MDL parser is unavailable.
    """
    if Sdr is None:
        return None
    asset = shd.GetSourceAsset("mdl")
    if not asset:
        return None
    sub = shd.GetSourceAssetSubIdentifier("mdl") or ""
    try:
        return Sdr.Registry().GetShaderNodeFromAsset(asset, {}, sub, "mdl") or None
    except Exception as e:
        carb.log_verbose(f"SDR lookup failed for MDL shader: {e}")
        return None


def _sdr_default(node, name: str):
    if node is None:
        return None
    try:
        prop = node.GetShaderInput(name)
    except Exception:
        return None
    if prop is None:
        return None
    try:
        return prop.GetDefaultValue()
    except Exception:
        return None


def _resolve_color(shd: UsdShade.Shader, node) -> Gf.Vec3f | None:
    """Resolve effective albedo as base colour x tint (OmniPBR "Color Tint").

    OmniPBR multiplies a base/albedo colour by a separate tint, so a surface
    whose colour comes from a black tint only reads correctly when the tint is
    applied. Authored USD inputs win over MDL preset (SDR) defaults per name.
    """

    def lookup(name: str) -> Gf.Vec3f | None:
        c = to_vec3(get_input_value(shd, name))
        if c is not None:
            return c
        return to_vec3(_sdr_default(node, name))

    base = next(
        (c for c in (lookup(n) for n in BASE_COLOR_INPUTS) if c is not None), None
    )
    tint = next((c for c in (lookup(n) for n in TINT_INPUTS) if c is not None), None)

    if base is not None and tint is not None:
        return Gf.Vec3f(base[0] * tint[0], base[1] * tint[1], base[2] * tint[2])
    if base is not None:
        return base
    if tint is not None:
        return tint

    # Last resort: any colour-like authored input, then any SDR colour input.
    for inp in shd.GetInputs():
        if _looks_like_color_name(inp.GetBaseName()):
            c = to_vec3(inp.Get())
            if c is not None:
                return c
    if node is not None:
        try:
            input_names = [str(n) for n in node.GetInputNames()]
        except Exception:
            input_names = []
        for name in input_names:
            if _looks_like_color_name(name):
                c = to_vec3(_sdr_default(node, name))
                if c is not None:
                    return c
    return None


def _authored_float(shd: UsdShade.Shader, names: tuple[str, ...]) -> float | None:
    for name in names:
        v = get_float(shd, name)
        if v is not None:
            return v
    return None


def _sdr_float(node, names: tuple[str, ...]) -> float | None:
    if node is None:
        return None
    for name in names:
        v = _sdr_default(node, name)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


# Sub-identifier that identifies the Wandelbots ghost material MDL.
# The MDL lives in a local project file whose relative path breaks after stage
# flatten, so the SDR registry cannot parse its defaults.  The known parameters
# are pre-computed below and injected directly by convert_material.
_GHOST_TEACHING_SUB_ID = "GhostTeaching"


def _params_for_ghost_teaching() -> PreviewParams:
    """Return UsdPreviewSurface params matching the GhostTeaching MDL preset.

    Derived from wb_ghost.mdl (SimPBR_Translucent):
      transmittance_color  → diffuseColor
      ior_constant         → ior
      reflection_roughness → roughness
      emissive_color       → emissiveColor (intensity not multiplied in to avoid
                             extreme HDR values outside Omniverse)
    """
    p = PreviewParams()
    p.diffuse = Gf.Vec3f(0.2492, 0.0884, 0.4672)  # transmittance_color
    p.metallic = 0.0
    p.roughness = 1.0  # reflection_roughness_constant
    p.opacity = 0.3  # SimPBR_Translucent
    p.ior = 1.1  # ior_constant
    p.specular = 0.0
    p.emissive = Gf.Vec3f(0.3707, 0.0286, 0.1158)  # emissive_color
    return p


def mdl_source_info(material_prim: Usd.Prim) -> tuple[str, str]:
    """Return (sub_identifier, source_asset_path_lower) from the first MDL shader."""
    for prim in iter_shaders(material_prim):
        shd = UsdShade.Shader(prim)
        sub = shd.GetSourceAssetSubIdentifier("mdl")
        asset = shd.GetSourceAsset("mdl")
        asset_path = ""
        if asset:
            asset_path = (asset.path or "").lower()
        if sub or asset_path:
            return (sub or "", asset_path)
    return ("", "")


def _debug_dump_enabled() -> bool:
    """Whether to log full per-material input/SDR diagnostics for this run.

    Opt-in via the setting ``/exts/wandelbots.omni/mdl_to_usd_preview/debug_dump``
    so a single conversion run reveals exactly what each MDL shader exposes,
    without spamming logs by default.
    """
    try:
        import carb.settings

        return bool(
            carb.settings.get_settings().get(
                "/exts/wandelbots.omni/mdl_to_usd_preview/debug_dump"
            )
        )
    except Exception:
        return False


def _dump_material_inputs(material_prim: Usd.Prim) -> str:
    """Build a human-readable dump of every shader's authored inputs and SDR defaults."""
    lines: list[str] = []
    for prim in iter_shaders(material_prim):
        shd = UsdShade.Shader(prim)
        sid = shd.GetIdAttr().Get() if shd.GetIdAttr() else None
        sub = shd.GetSourceAssetSubIdentifier("mdl")
        asset = shd.GetSourceAsset("mdl")
        asset_path = (asset.resolvedPath or asset.path) if asset else ""
        lines.append(
            f"  shader '{prim.GetName()}' id={sid} sub='{sub}' asset='{asset_path}'"
        )
        for inp in shd.GetInputs():
            connected = ""
            try:
                if inp.HasConnectedSource():
                    connected = " [connected]"
            except Exception:
                pass
            lines.append(f"    in {inp.GetBaseName()} = {inp.Get()!r}{connected}")
        node = _sdr_node_for_shader(shd)
        if node is None:
            lines.append("    sdr <no node>")
            continue
        try:
            names = [str(n) for n in node.GetInputNames()]
        except Exception:
            names = []
        for name in names:
            low = name.lower()
            if _looks_like_color_name(name) or any(
                k in low for k in ("rough", "metal", "opacity", "emiss")
            ):
                lines.append(f"    sdr {name} = {_sdr_default(node, name)!r}")
    return "\n".join(lines)


class PreviewParams:
    __slots__ = (
        "diffuse",
        "metallic",
        "roughness",
        "opacity",
        "ior",
        "specular",
        "clearcoat",
        "clearcoat_roughness",
        "emissive",
    )

    def __init__(self):
        self.diffuse: Gf.Vec3f | None = None
        self.metallic: float | None = None
        self.roughness: float | None = None
        self.opacity: float | None = None
        self.ior = 1.5
        self.specular = 0.5
        self.clearcoat = 0.0
        self.clearcoat_roughness = 0.03
        self.emissive: Gf.Vec3f | None = None


def params_from_existing_preview(shd: UsdShade.Shader) -> PreviewParams:
    """Read values from an existing UsdPreviewSurface shader so they are preserved."""
    p = PreviewParams()
    p.diffuse = to_vec3(get_input_value(shd, "diffuseColor"))
    p.emissive = to_vec3(get_input_value(shd, "emissiveColor"))
    p.metallic = get_float(shd, "metallic")
    p.roughness = get_float(shd, "roughness")
    p.opacity = get_float(shd, "opacity")
    ior = get_float(shd, "ior")
    if ior is not None:
        p.ior = ior
    spec = get_float(shd, "specular")
    if spec is not None:
        p.specular = spec
    cc = get_float(shd, "clearcoat")
    if cc is not None:
        p.clearcoat = cc
    ccr = get_float(shd, "clearcoatRoughness")
    if ccr is not None:
        p.clearcoat_roughness = ccr
    return p


def params_from_mdl(material_prim: Usd.Prim) -> PreviewParams:
    """Pull PBR values out of the MDL shader inputs, then MDL preset defaults.

    For each scalar/colour the authored USD input wins; when it is absent (the
    common case for vMaterials presets) the value is read from the MDL preset
    default via the SDR registry.
    """
    p = PreviewParams()
    for prim in iter_shaders(material_prim):
        shd = UsdShade.Shader(prim)
        node = _sdr_node_for_shader(shd)

        if p.diffuse is None:
            p.diffuse = _resolve_color(shd, node)

        if p.metallic is None:
            p.metallic = _authored_float(shd, METALLIC_INPUTS)
            if p.metallic is None:
                p.metallic = _sdr_float(node, METALLIC_INPUTS)
        if p.roughness is None:
            p.roughness = _authored_float(shd, ROUGHNESS_INPUTS)
            if p.roughness is None:
                p.roughness = _sdr_float(node, ROUGHNESS_INPUTS)
        if p.opacity is None:
            p.opacity = _authored_float(shd, OPACITY_INPUTS)
            if p.opacity is None:
                p.opacity = _sdr_float(node, OPACITY_INPUTS)

        if p.emissive is None:
            enable = get_input_value(shd, "enable_emission")
            if enable is None:
                enable = _sdr_default(node, "enable_emission")
            em = to_vec3(get_input_value(shd, "emissive_color"))
            if em is None:
                em = to_vec3(_sdr_default(node, "emissive_color"))
            if enable and em is not None and tuple(em) != (0.0, 0.0, 0.0):
                p.emissive = em
    return p


def finalize_params(
    p: PreviewParams, mat_name: str, sub_id: str, asset_path: str
) -> PreviewParams:
    """Apply name-based inference and sensible defaults for anything still missing."""
    n = mat_name.lower()
    is_metal = (
        any(
            k in n
            for k in (
                "metal",
                "steel",
                "iron",
                "aluminum",
                "chrome",
                "brass",
                "copper",
                "gold",
                "silver",
            )
        )
        or "/metals/" in asset_path
    )
    is_plastic = any(k in n for k in ("plastic", "abs", "rubber", "paint"))
    is_light = (
        any(k in n for k in ("light", "led", "lamp", "emiss"))
        or "/emissives/" in asset_path
    )
    is_glass = any(k in n for k in ("glass", "ghost"))

    if p.diffuse is None:
        p.diffuse = color_from_name(n) or color_from_name(sub_id)
    if p.diffuse is None:
        p.diffuse = Gf.Vec3f(0.7, 0.7, 0.7) if is_metal else Gf.Vec3f(0.8, 0.8, 0.8)

    if p.metallic is None:
        p.metallic = 1.0 if is_metal else 0.0
    if p.roughness is None:
        p.roughness = 0.35 if is_metal else (0.45 if is_plastic else 0.5)
    if p.opacity is None:
        p.opacity = 0.25 if is_glass else 1.0

    if is_light and p.emissive is None:
        p.emissive = Gf.Vec3f(p.diffuse)

    return p


def write_preview_shader(
    stage: Usd.Stage, path: Sdf.Path, p: PreviewParams
) -> UsdShade.Shader:
    shd = UsdShade.Shader.Define(stage, path)
    shd.CreateIdAttr("UsdPreviewSurface")
    shd.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(p.diffuse)
    if p.emissive is not None:
        shd.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(p.emissive)
    shd.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(p.metallic))
    shd.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(p.roughness))
    shd.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(float(p.ior))
    shd.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(float(p.specular))
    shd.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(p.opacity))
    shd.CreateInput("clearcoat", Sdf.ValueTypeNames.Float).Set(float(p.clearcoat))
    shd.CreateInput("clearcoatRoughness", Sdf.ValueTypeNames.Float).Set(
        float(p.clearcoat_roughness)
    )
    shd.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    return shd


def convert_material(stage: Usd.Stage, material_prim: Usd.Prim) -> None:
    mat = UsdShade.Material(material_prim)
    mat_name = material_prim.GetName()
    sub_id, asset_path = mdl_source_info(material_prim)

    existing = find_existing_preview(material_prim)
    has_direct_existing = existing is not None and existing.GetParent() == material_prim

    if sub_id == _GHOST_TEACHING_SUB_ID:
        # The MDL lives in a local project file whose path breaks after flatten;
        # SDR lookup always fails for it.  Use the known params directly.
        p = _params_for_ghost_teaching()
        keep_path = (
            existing.GetPath()
            if has_direct_existing
            else material_prim.GetPath().AppendChild(PREVIEW_SHADER_NAME)
        )
        write_preview_shader(stage, keep_path, p)
    elif has_direct_existing:
        # A direct-child UsdPreviewSurface (e.g. hand-tuned KUKA materials) is
        # preserved; otherwise a fresh preview shader is built from MDL data.
        p = params_from_existing_preview(UsdShade.Shader(existing))
        # An asset may ship an incomplete preview shader (e.g. no colour); fill any
        # gaps from the MDL/SDR data before falling back to name-based guesses.
        if p.diffuse is None or p.metallic is None or p.roughness is None:
            mdl_p = params_from_mdl(material_prim)
            if p.diffuse is None:
                p.diffuse = mdl_p.diffuse
            if p.metallic is None:
                p.metallic = mdl_p.metallic
            if p.roughness is None:
                p.roughness = mdl_p.roughness
            if p.opacity is None:
                p.opacity = mdl_p.opacity
            if p.emissive is None:
                p.emissive = mdl_p.emissive
        p = finalize_params(p, mat_name, sub_id, asset_path)
        keep_path = existing.GetPath()
        write_preview_shader(stage, keep_path, p)
    else:
        p = params_from_mdl(material_prim)
        resolved_color = p.diffuse is not None
        p = finalize_params(p, mat_name, sub_id, asset_path)
        if not resolved_color:
            carb.log_warn(
                f"MDL->Preview: no colour found for material '{mat_name}' "
                f"(sub_id='{sub_id}', asset='{asset_path}'); approximated from "
                f"name/defaults. Install the MDL SDR parser or check the shader "
                f"inputs if this material looks wrong."
            )
        keep_path = material_prim.GetPath().AppendChild(PREVIEW_SHADER_NAME)
        write_preview_shader(stage, keep_path, p)

    for child in list(material_prim.GetChildren()):
        if child.GetPath() == keep_path:
            continue
        if child.IsA(UsdShade.Shader) or child.IsA(UsdShade.NodeGraph):
            stage.RemovePrim(child.GetPath())

    # Drop the MDL surface/displacement/volume outputs before rewiring.
    for prop_name in list(material_prim.GetPropertyNames()):
        if prop_name.startswith("outputs:"):
            material_prim.RemoveProperty(prop_name)

    preview_shd = UsdShade.Shader(stage.GetPrimAtPath(keep_path))
    surface_out = preview_shd.GetOutput("surface") or preview_shd.CreateOutput(
        "surface", Sdf.ValueTypeNames.Token
    )
    mat.CreateSurfaceOutput().ConnectToSource(surface_out)

    if _debug_dump_enabled():
        carb.log_warn(
            f"MDL->Preview result: '{mat_name}' diffuse={p.diffuse} "
            f"metallic={p.metallic} roughness={p.roughness} opacity={p.opacity}"
        )


def convert_stage(stage: Usd.Stage) -> int:
    """Convert every material on the stage to UsdPreviewSurface; returns the count."""
    material_paths = [
        prim.GetPath() for prim in stage.Traverse() if prim.IsA(UsdShade.Material)
    ]

    count = 0
    for path in material_paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            continue
        if _debug_dump_enabled():
            carb.log_warn(
                f"MDL->Preview dump: material '{prim.GetName()}'\n"
                + _dump_material_inputs(prim)
            )
        convert_material(stage, prim)
        count += 1
    return count


def convert_file(usd_path: str) -> int:
    """Open a USD file, convert its materials in place, and re-export it."""
    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        raise RuntimeError(f"Could not open USD for material conversion: {usd_path}")

    count = convert_stage(stage)
    stage.GetRootLayer().Export(usd_path)
    carb.log_info(f"Converted {count} material(s) to UsdPreviewSurface: {usd_path}")
    return count
