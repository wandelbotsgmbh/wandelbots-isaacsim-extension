from __future__ import annotations

import re

from wandelbots.omni.instances.models import (
    NOVACustomInstance,
    NOVACellData,
    NOVAControllerData,
    NOVAMotionGroupData,
)
from wandelbots.omni.manipulators import MotionGroupConfiguration
from wandelbots.omni.utils.hosts import normalize_host
import isaacsim.core.utils.stage as stage_utils
from pxr import Sdf


def filter_unknown_host_instances(
    configs: list[MotionGroupConfiguration],
    known_hosts: set[str],
) -> list[NOVACustomInstance]:
    """Find unique hosts in *configs* that are not in *known_hosts*
    and return an unreachable ``NOVACustomInstance`` for each.

    Membership is tested on the normalized host - *known_hosts* comes from
    ``NOVAInstance.host``, which may carry a scheme the config host does not.
    """
    orphan_hosts: dict[str, bool] = {}  # normalized host -> is_secure
    known = {normalize_host(known_host) for known_host in known_hosts}

    for config in configs:
        host = normalize_host(config.motion_stream_configuration.host)
        if not host:
            continue
        if host in known:
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
    filtered to the given *host* (normalized on both sides).
    """
    # cell_name -> controller_name -> list[motion_group_name]
    cell_controller_tree: dict[str, dict[str, list[str]]] = {}
    # motion_group_name -> resolved model name (from the prim's custom data),
    # so the section title can show the robot model before the round brackets
    # exactly like reachable instances do (model name comes from the API there).
    model_name_by_motion_group: dict[str, str] = {}
    wanted_host = normalize_host(host)

    for config in configs:
        stream_config = config.motion_stream_configuration
        if normalize_host(stream_config.host) != wanted_host:
            continue
        cell_name = stream_config.cell or "unknown"
        controller_name = stream_config.controller or "unknown"
        motion_group_name = stream_config.motion_group or "unknown"
        cell_controller_tree.setdefault(cell_name, {}).setdefault(
            controller_name, []
        ).append(motion_group_name)
        model_name = _get_prim_model_name(config.prim_path)
        if model_name:
            model_name_by_motion_group.setdefault(motion_group_name, model_name)

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
                            motion_group_model_name=model_name_by_motion_group.get(
                                motion_group_name, motion_group_name
                            ),
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
    - motion_group_name (v2 custom data)
    - name (fallback)
    """

    stage = stage_utils.get_current_stage()
    if stage is None:
        return None
    prim = stage.GetPrimAtPath(Sdf.Path(prim_path))
    if not prim or not prim.IsValid():
        return None
    custom_data = prim.GetCustomData()
    return (
        custom_data.get("motionGroupModel")
        or custom_data.get("motion_group_name")
        or custom_data.get("name")
    )


# Controller names in plant notation: alpha prefix + station digits + robot
# token, e.g. "ir340r02" -> ("ir", "340", "r02").
_PLANT_CONTROLLER_PATTERN = re.compile(r"^([a-zA-Z]+)(\d+)(r\d+)$")


def _suffix_matched_prims(controller: str, scene_articulations: list[str]) -> list[str]:
    """Prims whose leaf name embeds the controller name in plant notation.

    Plant scenes commonly name robot prims with the full station number while
    the NOVA controller carries only its tail digits (prim
    ``ir_313340r02_hose`` vs controller ``ir340r02``). A leaf matches when it
    contains the controller's alpha prefix, digits ENDING in the controller's
    station digits, and the exact robot token — with optional ``_``/``-``
    separators and nothing but a digit boundary after the robot token (so
    ``r02`` cannot match inside ``r021``).
    """
    parsed = _PLANT_CONTROLLER_PATTERN.match(controller)
    if not parsed:
        return []
    prefix, station, robot = (group.lower() for group in parsed.groups())
    leaf_pattern = re.compile(
        rf"(?<![a-z0-9]){re.escape(prefix)}[_\-]?\d*{re.escape(station)}"
        rf"[_\-]?{re.escape(robot)}(?!\d)"
    )
    return [
        prim_path
        for prim_path in scene_articulations
        if leaf_pattern.search(prim_path.rsplit("/", 1)[-1].lower())
    ]


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
    - prim name embeds the controller name in plant notation (station-number
      suffix; primary 0@ motion group and single match only)
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

    # Plant-notation fallback. Restricted to the primary (0@) motion group:
    # secondary groups (1@..., typically external axes) share the controller
    # name, so this rule would suggest them the same robot prim. And only an
    # unambiguous single hit counts — duplicated stations stay manual.
    if not results and scene_articulations and motion_group.startswith("0@"):
        suffix_matches = _suffix_matched_prims(controller, scene_articulations)
        if len(suffix_matches) == 1:
            results = suffix_matches

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
