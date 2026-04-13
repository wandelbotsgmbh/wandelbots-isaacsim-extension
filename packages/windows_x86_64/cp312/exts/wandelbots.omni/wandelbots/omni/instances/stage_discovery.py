from __future__ import annotations

from wandelbots.omni.instances.models import (
    NOVACustomInstance,
    NOVACellData,
    NOVAControllerData,
    NOVAMotionGroupData,
)
from wandelbots.omni.manipulators import MotionGroupConfiguration
import isaacsim.core.utils.stage as stage_utils
from pxr import Sdf


def filter_unknown_host_instances(
    configs: list[MotionGroupConfiguration],
    known_hosts: set[str],
) -> list[NOVACustomInstance]:
    """Find unique hosts in *configs* that are not in *known_hosts*
    and return an unreachable ``NOVACustomInstance`` for each.
    """
    orphan_hosts: dict[str, bool] = {}  # host -> is_secure

    for config in configs:
        host = config.motion_stream_configuration.host
        if not host:
            continue
        if host in known_hosts:
            continue
        orphan_hosts.setdefault(
            host, config.motion_stream_configuration.secure_connection
        )

    return [
        NOVACustomInstance(
            host=host,
            name=host,
            is_secure_connection=is_secure,
            is_reachable=False,
        )
        for host, is_secure in orphan_hosts.items()
    ]


def list_cells_for_host(
    configs: list[MotionGroupConfiguration],
    host: str,
) -> list[NOVACellData]:
    """Build a cell / controller / motion-group hierarchy from *configs*
    filtered to the given *host*.
    """
    # cell_name -> controller_name -> list[motion_group_name]
    cell_controller_tree: dict[str, dict[str, list[str]]] = {}

    for config in configs:
        stream_config = config.motion_stream_configuration
        if stream_config.host != host:
            continue
        cell_name = stream_config.cell or "unknown"
        controller_name = stream_config.controller or "unknown"
        motion_group_name = stream_config.motion_group or "unknown"
        cell_controller_tree.setdefault(cell_name, {}).setdefault(
            controller_name, []
        ).append(motion_group_name)

    cells: list[NOVACellData] = []
    for cell_name, controllers_by_name in cell_controller_tree.items():
        controller_data_list: list[NOVAControllerData] = []
        for controller_name, motion_group_names in controllers_by_name.items():
            unique_motion_groups = list(dict.fromkeys(motion_group_names))
            controller_data_list.append(
                NOVAControllerData(
                    name=controller_name,
                    cell_name=cell_name,
                    description=None,
                    motion_groups=[
                        NOVAMotionGroupData(
                            name=motion_group_name,
                            motion_group_model_name=motion_group_name,
                        )
                        for motion_group_name in unique_motion_groups
                    ],
                )
            )
        cells.append(NOVACellData(name=cell_name, controllers=controller_data_list))
    return cells


def _normalize_model_name(name: str) -> str:
    """Lowercase and collapse underscores/spaces for comparison."""
    return name.lower().replace("_", " ").strip()


def _get_prim_model_name(prim_path: str) -> str | None:
    """Read the model identifier from a prim's custom data.

    Checks (in order):
    - motionGroupModel (v1 custom data)
    - name (v2 custom data)
    """

    stage = stage_utils.get_current_stage()
    if stage is None:
        return None
    prim = stage.GetPrimAtPath(Sdf.Path(prim_path))
    if not prim or not prim.IsValid():
        return None
    custom_data = prim.GetCustomData()
    return custom_data.get("motionGroupModel") or custom_data.get("name")


def list_motion_group_prim_suggestions(
    configs: list[MotionGroupConfiguration],
    cell: str,
    controller: str,
    motion_group: str,
    scene_articulations: list[str] | None = None,
    motion_group_model_name: str | None = None,
) -> list[str]:
    """Return prim paths that are likely matches for the given motion group.

    Matching rules (first non-empty result wins):
    - exact config match (cell + controller + motion group)
    - prim name matches controller name
    - custom-data model name matches motion_group_model_name (single match only)
    """
    results: list[str] = []
    for config in configs:
        sc = config.motion_stream_configuration
        if (
            sc.cell == cell
            and sc.controller == controller
            and sc.motion_group == motion_group
        ):
            results.append(config.prim_path)

    if not results and scene_articulations:
        for prim_path in scene_articulations:
            prim_name = prim_path.rsplit("/", 1)[-1]
            if prim_name == controller:
                results.append(prim_path)

    if not results and scene_articulations and motion_group_model_name:
        norm_model = _normalize_model_name(motion_group_model_name)
        model_matches: list[str] = []
        for prim_path in scene_articulations:
            prim_model = _get_prim_model_name(prim_path)
            if prim_model and _normalize_model_name(prim_model) == norm_model:
                model_matches.append(prim_path)
        if len(model_matches) == 1:
            results = model_matches

    return results
