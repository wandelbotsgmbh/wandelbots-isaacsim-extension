from __future__ import annotations

import os
import re

import carb
import omni.client
import omni.usd
from pxr import Gf, Sdf, Usd, UsdGeom
import wandelbots_api_client.v2 as wb_v2

from wandelbots.omni.instances.instances_api import get_instances_api
from wandelbots.omni.instances.models import NOVACloudInstance, NOVAInstance

from .model_base_offsets import MODEL_BASE_OFFSETS


def _normalize_model_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def make_robot_api_client(instance: NOVAInstance) -> wb_v2.ApiClient | None:
    if isinstance(instance, NOVACloudInstance):
        token = get_instances_api().get_auth_token_from_host(instance.host)
        return instance.create_api_client(token=token)
    return instance.create_api_client()


async def resolve_model_id(api_client: wb_v2.ApiClient, model_name: str) -> str:
    try:
        all_models: list[str] = await wb_v2.MotionGroupModelsApi(
            api_client
        ).get_motion_group_models()
    except Exception as exc:
        carb.log_warn(f"Could not list motion group models: {exc}")
        return model_name

    norm = _normalize_model_name(model_name)
    return next((m for m in all_models if _normalize_model_name(m) == norm), model_name)


async def download_and_add_robot(
    instance: NOVAInstance,
    model_name: str,
    download_path: str,
    location_prim: Usd.Prim | None = None,
    resolve_name: bool = False,
) -> str | None:
    """Download the USD model for ``model_name`` from ``instance`` to
    ``download_path`` and add it as a payload under ``location_prim`` (or
    ``/World``). When ``resolve_name`` is set the (possibly display-formatted)
    ``model_name`` is matched against the instance's available models first.

    Returns the created prim path, or ``None`` on failure.
    """
    api_client = make_robot_api_client(instance)
    if api_client is None:
        carb.log_error("Could not create API client for downloading robot")
        return None

    try:
        model = (
            await resolve_model_id(api_client, model_name)
            if resolve_name
            else model_name
        )

        usd_bytes: bytearray = await wb_v2.MotionGroupModelsApi(
            api_client
        ).get_motion_group_usd_model(motion_group_model=model)

        is_nucleus = "://" in download_path

        if is_nucleus:
            usd_file_path = (
                download_path
                if download_path.lower().endswith(".usd")
                else download_path.rstrip("/") + "/" + model + ".usd"
            )
        elif os.path.isdir(download_path):
            usd_file_path = os.path.join(download_path, f"{model}.usd")
        elif not download_path.lower().endswith(".usd"):
            usd_file_path = download_path + ".usd"
        else:
            usd_file_path = download_path

        if is_nucleus:
            write_result = await omni.client.write_file_async(
                usd_file_path, bytes(usd_bytes)
            )
            if write_result != omni.client.Result.OK:
                raise RuntimeError(
                    f"omni.client.write_file_async failed with: {write_result}"
                )
        else:
            parent_dir = os.path.dirname(usd_file_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(usd_file_path, "wb") as f:
                f.write(usd_bytes)

        carb.log_info(f"Saved USD to '{usd_file_path}'")

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            carb.log_error("No active stage found")
            return None

        parent_path = location_prim.GetPath().pathString if location_prim else "/World"

        safe_name = (
            model
            if Sdf.Path.IsValidIdentifier(model)
            else model.replace("-", "_").replace(" ", "_")
        )
        robot_prim_path = Sdf.Path(parent_path).AppendChild(safe_name)

        xform = UsdGeom.Xform.Define(stage, robot_prim_path)
        xform.GetPrim().GetPayloads().AddPayload(usd_file_path)

        stage_units = UsdGeom.GetStageMetersPerUnit(stage)
        base_offset_m = MODEL_BASE_OFFSETS.get(model, 0.0)
        z_offset = base_offset_m / stage_units if base_offset_m != 0.0 else 0.0
        if z_offset != 0.0:
            ordered_ops = xform.GetOrderedXformOps()
            translate_op = next(
                (
                    op
                    for op in ordered_ops
                    if op.GetOpType() == UsdGeom.XformOp.TypeTranslate
                ),
                None,
            )
            if translate_op is None:
                translate_op = xform.AddTranslateOp(
                    precision=UsdGeom.XformOp.PrecisionDouble
                )
            translate_op.Set(Gf.Vec3d(0.0, 0.0, z_offset))

        carb.log_info(f"Added payload at '{robot_prim_path}' -> '{usd_file_path}'")
        return robot_prim_path.pathString

    except Exception as exc:
        carb.log_error(f"Failed to download / import model '{model_name}': {exc}")
        return None
    finally:
        try:
            await api_client.close()
        except Exception:
            pass
