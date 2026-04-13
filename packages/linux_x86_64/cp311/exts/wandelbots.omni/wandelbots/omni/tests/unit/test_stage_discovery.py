"""Unit tests for stage discovery utilities.

Creates an in-memory USD stage with robots that have ``MotionGroupAPI``
applied, then verifies discovery, cell hierarchy building, and prim
suggestion functions against the real stage tree.
"""

import omni.kit.test
from contextlib import contextmanager
from pxr import UsdGeom, Usd

from wandelbots.omni.tests.stage_utils import use_stage
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    MotionStreamConfiguration,
    get_scene_motion_group_prim_paths,
    get_motion_group_configuration_from_prim,
)
from wandelbots.omni.instances.stage_discovery import (
    filter_unknown_host_instances,
    list_cells_for_host,
    list_motion_group_prim_suggestions,
)

NOVA_HOST_1 = "nova-1.example.com"
NOVA_HOST_2 = "nova-2.example.com"


def _make_robot_config(
    prim_path: str,
    host: str,
    cell: str,
    controller: str,
    motion_group: str,
    secure: bool = False,
) -> MotionGroupConfiguration:
    return MotionGroupConfiguration(
        name=motion_group,
        prim_path=prim_path,
        enabled=True,
        motion_stream_configuration=MotionStreamConfiguration(
            host=host,
            secure_connection=secure,
            cell=cell,
            controller=controller,
            motion_group=motion_group,
        ),
    )


def _apply_robots_to_stage(
    stage: Usd.Stage,
    configs: list[MotionGroupConfiguration],
) -> None:
    for cfg in configs:
        UsdGeom.Xform.Define(stage, cfg.prim_path)
        cfg.apply_to_prim(stage)


def _collect_stage_configs(stage: Usd.Stage) -> list[MotionGroupConfiguration]:
    configs = []
    for prim_path in get_scene_motion_group_prim_paths(include_prims_without_api=False):
        prim = stage.GetPrimAtPath(prim_path)
        config = get_motion_group_configuration_from_prim(prim)
        if config is not None:
            configs.append(config)
    return configs


class TestStageDiscovery(omni.kit.test.AsyncTestCase):
    @contextmanager
    def _create_stage(self):
        stage = Usd.Stage.CreateInMemory("TestStageDiscovery")
        self.assertIsNotNone(stage)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        with use_stage(stage):
            yield stage

    def _build_two_robot_stage(
        self, stage: Usd.Stage
    ) -> list[MotionGroupConfiguration]:
        """Create two robots on different hosts and apply MotionGroupAPI."""
        robot_configs = [
            _make_robot_config(
                prim_path="/World/UR10e",
                host=NOVA_HOST_1,
                cell="cell",
                controller="ur10e",
                motion_group="0@ur10e",
            ),
            _make_robot_config(
                prim_path="/World/UR5e",
                host=NOVA_HOST_2,
                cell="cell2",
                controller="ur5e",
                motion_group="0@ur5e",
                secure=True,
            ),
        ]
        _apply_robots_to_stage(stage, robot_configs)
        return robot_configs

    async def test_filter_unknown_host_instances_returns_both_hosts(self):
        with self._create_stage() as stage:
            self._build_two_robot_stage(stage)
            configs = _collect_stage_configs(stage)

            orphans = filter_unknown_host_instances(configs, known_hosts=set())

            orphan_hosts = {inst.host for inst in orphans}
            self.assertEqual(orphan_hosts, {NOVA_HOST_1, NOVA_HOST_2})

            by_host = {inst.host: inst for inst in orphans}
            self.assertFalse(by_host[NOVA_HOST_1].is_secure_connection)
            self.assertTrue(by_host[NOVA_HOST_2].is_secure_connection)
            for inst in orphans:
                self.assertFalse(inst.is_reachable)

    async def test_filter_unknown_host_instances_excludes_known_host(self):
        with self._create_stage() as stage:
            self._build_two_robot_stage(stage)
            configs = _collect_stage_configs(stage)

            orphans = filter_unknown_host_instances(configs, known_hosts={NOVA_HOST_1})

            self.assertEqual(len(orphans), 1)
            self.assertEqual(orphans[0].host, NOVA_HOST_2)

    async def test_list_cells_for_host_builds_hierarchy(self):
        with self._create_stage() as stage:
            self._build_two_robot_stage(stage)
            configs = _collect_stage_configs(stage)

            cells = list_cells_for_host(configs, NOVA_HOST_1)

            self.assertEqual(len(cells), 1)
            self.assertEqual(cells[0].name, "cell")
            self.assertEqual(len(cells[0].controllers), 1)
            self.assertEqual(cells[0].controllers[0].name, "ur10e")
            self.assertEqual(cells[0].controllers[0].motion_groups[0].name, "0@ur10e")

    async def test_prim_suggestions_returns_matching_prim(self):
        with self._create_stage() as stage:
            self._build_two_robot_stage(stage)
            configs = _collect_stage_configs(stage)

            suggestions = list_motion_group_prim_suggestions(
                configs,
                cell="cell",
                controller="ur10e",
                motion_group="0@ur10e",
            )

            self.assertEqual(suggestions, ["/World/UR10e"])

    async def test_prim_suggestions_matches_across_hosts(self):
        with self._create_stage() as stage:
            self._build_two_robot_stage(stage)
            # Add a third robot on a different host but same cell/controller/mg
            extra = _make_robot_config(
                prim_path="/World/UR10e_copy",
                host=NOVA_HOST_2,
                cell="cell",
                controller="ur10e",
                motion_group="0@ur10e",
            )
            _apply_robots_to_stage(stage, [extra])
            configs = _collect_stage_configs(stage)

            suggestions = list_motion_group_prim_suggestions(
                configs,
                cell="cell",
                controller="ur10e",
                motion_group="0@ur10e",
            )

            self.assertEqual(sorted(suggestions), ["/World/UR10e", "/World/UR10e_copy"])

    async def test_prim_suggestions_falls_back_to_controller_name_match(self):
        """When no exact config match exists, suggest articulations whose
        prim name equals the controller name."""
        with self._create_stage() as stage:
            self._build_two_robot_stage(stage)
            configs = _collect_stage_configs(stage)

            # No config has cell="other_cell" — but prim "/World/UR10e"'s
            # name doesn't match controller "fanuc" either, so empty.
            suggestions = list_motion_group_prim_suggestions(
                configs,
                cell="other_cell",
                controller="fanuc",
                motion_group="0@fanuc",
                scene_articulations=["/World/UR10e", "/World/UR5e"],
            )
            self.assertEqual(suggestions, [])

            # Prim name "ur10e" matches controller "ur10e"
            suggestions = list_motion_group_prim_suggestions(
                configs,
                cell="other_cell",
                controller="ur10e",
                motion_group="0@ur10e",
                scene_articulations=["/World/ur10e", "/World/UR5e"],
            )
            self.assertEqual(suggestions, ["/World/ur10e"])

    async def test_prim_suggestions_prefers_exact_config_over_name_match(self):
        """Exact cell+controller+motion_group config match takes priority
        over controller-name fallback."""
        with self._create_stage() as stage:
            self._build_two_robot_stage(stage)
            configs = _collect_stage_configs(stage)

            suggestions = list_motion_group_prim_suggestions(
                configs,
                cell="cell",
                controller="ur10e",
                motion_group="0@ur10e",
                scene_articulations=["/World/ur10e_other"],
            )
            # Should return the config match, not the name match
            self.assertEqual(suggestions, ["/World/UR10e"])

    async def test_prim_suggestions_no_scene_articulations_returns_empty(self):
        """Without scene_articulations the fallback is skipped."""
        suggestions = list_motion_group_prim_suggestions(
            configs=[],
            cell="cell",
            controller="ur10e",
            motion_group="0@ur10e",
        )
        self.assertEqual(suggestions, [])

    async def test_prim_suggestions_matches_by_motion_group_model(self):
        """When exactly one articulation's model name matches, it is
        suggested."""
        with self._create_stage() as stage:
            # Create two articulation prims with custom data
            prim_abb = UsdGeom.Xform.Define(stage, "/World/robot_a").GetPrim()
            prim_abb.SetCustomData({"motionGroupModel": "ABB_2600ID_200_8"})
            prim_ur = UsdGeom.Xform.Define(stage, "/World/robot_b").GetPrim()
            prim_ur.SetCustomData({"motionGroupModel": "UR10e"})

            articulations = ["/World/robot_a", "/World/robot_b"]

            # Only one prim matches "ABB 2600ID 200 8"
            suggestions = list_motion_group_prim_suggestions(
                configs=[],
                cell="cell",
                controller="ctrl",
                motion_group="0@mg",
                scene_articulations=articulations,
                motion_group_model_name="ABB 2600ID 200 8",
            )
            self.assertEqual(suggestions, ["/World/robot_a"])

    async def test_prim_suggestions_model_match_skipped_when_multiple(self):
        """When multiple articulations match motionGroupModel, no suggestion
        is made (ambiguous)."""
        with self._create_stage() as stage:
            prim_a = UsdGeom.Xform.Define(stage, "/World/abb_1").GetPrim()
            prim_a.SetCustomData({"motionGroupModel": "ABB_2600ID_200_8"})
            prim_b = UsdGeom.Xform.Define(stage, "/World/abb_2").GetPrim()
            prim_b.SetCustomData({"motionGroupModel": "ABB_2600ID_200_8"})

            suggestions = list_motion_group_prim_suggestions(
                configs=[],
                cell="cell",
                controller="ctrl",
                motion_group="0@mg",
                scene_articulations=["/World/abb_1", "/World/abb_2"],
                motion_group_model_name="ABB 2600ID 200 8",
            )
            self.assertEqual(suggestions, [])

    async def test_prim_suggestions_model_match_case_insensitive(self):
        """Model name comparison is case-insensitive."""
        with self._create_stage() as stage:
            prim = UsdGeom.Xform.Define(stage, "/World/robot").GetPrim()
            prim.SetCustomData({"motionGroupModel": "abb_2600id_200_8"})

            suggestions = list_motion_group_prim_suggestions(
                configs=[],
                cell="cell",
                controller="ctrl",
                motion_group="0@mg",
                scene_articulations=["/World/robot"],
                motion_group_model_name="ABB 2600ID 200 8",
            )
            self.assertEqual(suggestions, ["/World/robot"])

    async def test_prim_suggestions_single_articulation_name_match(self):
        """A single articulation whose prim name matches the controller
        is still suggested."""
        suggestions = list_motion_group_prim_suggestions(
            configs=[],
            cell="cell",
            controller="ur10e",
            motion_group="0@ur10e",
            scene_articulations=["/World/ur10e"],
        )
        self.assertEqual(suggestions, ["/World/ur10e"])

    async def test_prim_suggestions_single_articulation_model_match(self):
        """A single articulation whose model name matches is still suggested."""
        with self._create_stage() as stage:
            prim = UsdGeom.Xform.Define(stage, "/World/robot").GetPrim()
            prim.SetCustomData({"motionGroupModel": "UR10e"})

            suggestions = list_motion_group_prim_suggestions(
                configs=[],
                cell="cell",
                controller="ctrl",
                motion_group="0@mg",
                scene_articulations=["/World/robot"],
                motion_group_model_name="UR10e",
            )
            self.assertEqual(suggestions, ["/World/robot"])

    async def test_prim_suggestions_v2_custom_data_name(self):
        """The v2 custom data format uses a top-level ``name`` key instead
        of ``motionGroupModel``."""
        with self._create_stage() as stage:
            prim = UsdGeom.Xform.Define(stage, "/World/abb").GetPrim()
            prim.SetCustomData(
                {
                    "name": "ABB_4600_255_40",
                    "robot-configuration": {"id": 0, "name": "abb-irb4600_255_40"},
                }
            )

            suggestions = list_motion_group_prim_suggestions(
                configs=[],
                cell="cell",
                controller="ctrl",
                motion_group="0@mg",
                scene_articulations=["/World/abb"],
                motion_group_model_name="ABB 4600 255 40",
            )
            self.assertEqual(suggestions, ["/World/abb"])

    async def test_prim_suggestions_v1_takes_precedence_over_v2(self):
        """When both ``motionGroupModel`` and ``name`` are present, the v1
        key is used."""
        with self._create_stage() as stage:
            prim = UsdGeom.Xform.Define(stage, "/World/robot").GetPrim()
            prim.SetCustomData(
                {
                    "motionGroupModel": "UR10e",
                    "name": "SomethingElse",
                }
            )

            suggestions = list_motion_group_prim_suggestions(
                configs=[],
                cell="cell",
                controller="ctrl",
                motion_group="0@mg",
                scene_articulations=["/World/robot"],
                motion_group_model_name="UR10e",
            )
            self.assertEqual(suggestions, ["/World/robot"])

    async def test_prim_suggestions_exact_config_preferred_over_model(self):
        """Exact config match takes priority so model match is never reached."""
        with self._create_stage() as stage:
            robot_configs = [
                _make_robot_config(
                    prim_path="/World/UR10e",
                    host=NOVA_HOST_1,
                    cell="cell",
                    controller="ur10e",
                    motion_group="0@ur10e",
                ),
            ]
            _apply_robots_to_stage(stage, robot_configs)

            prim = stage.GetPrimAtPath("/World/UR10e")
            prim.SetCustomData({"motionGroupModel": "UR10e"})

            configs = _collect_stage_configs(stage)

            # Also create a second prim that matches by model
            prim_b = UsdGeom.Xform.Define(stage, "/World/other_ur").GetPrim()
            prim_b.SetCustomData({"motionGroupModel": "UR10e"})

            suggestions = list_motion_group_prim_suggestions(
                configs,
                cell="cell",
                controller="ur10e",
                motion_group="0@ur10e",
                scene_articulations=["/World/UR10e", "/World/other_ur"],
                motion_group_model_name="UR10e",
            )
            # Exact config match wins
            self.assertEqual(suggestions, ["/World/UR10e"])
