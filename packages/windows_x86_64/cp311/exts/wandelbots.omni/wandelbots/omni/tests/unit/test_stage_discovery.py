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
    find_robot_prim,
    get_prim_model_name,
    list_cells_for_host,
    list_motion_group_prim_suggestions,
    model_name_from_prim,
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

    # ------------------------------------------------------------------
    # Plant-notation suffix matching
    # ------------------------------------------------------------------

    async def test_prim_suggestions_plant_notation_suffix_match(self):
        """A prim named with the full station number matches a controller
        carrying only the station's tail digits (ir_313340r02_hose for
        ir340r02, station 13340)."""
        suggestions = list_motion_group_prim_suggestions(
            configs=[],
            cell="cell",
            controller="ir340r02",
            motion_group="0@ir340r02",
            scene_articulations=[
                "/World/ir_313340r01_hose",
                "/World/ir_313340r02_hose",
                "/World/ir_313350r01_hose",
            ],
        )
        self.assertEqual(suggestions, ["/World/ir_313340r02_hose"])

    async def test_prim_suggestions_plant_notation_requires_primary_group(self):
        """Secondary motion groups (1@..., typically external axes) share the
        controller name, so the suffix rule must not claim the robot prim for
        them."""
        suggestions = list_motion_group_prim_suggestions(
            configs=[],
            cell="cell",
            controller="ir350r01",
            motion_group="1@ir350r01",
            scene_articulations=["/World/ir_313350r01_hose"],
        )
        self.assertEqual(suggestions, [])

    async def test_prim_suggestions_plant_notation_ambiguous_returns_empty(self):
        """Two stations whose numbers both end in the controller's digits are
        ambiguous — better no suggestion than a wrong one."""
        suggestions = list_motion_group_prim_suggestions(
            configs=[],
            cell="cell",
            controller="ir340r01",
            motion_group="0@ir340r01",
            scene_articulations=[
                "/World/ir_313340r01_hose",
                "/World/ir_312340r01_hose",
            ],
        )
        self.assertEqual(suggestions, [])

    async def test_prim_suggestions_plant_notation_robot_token_boundary(self):
        """The robot token must end at a digit boundary (r02 must not match
        inside r021)."""
        suggestions = list_motion_group_prim_suggestions(
            configs=[],
            cell="cell",
            controller="ir340r02",
            motion_group="0@ir340r02",
            scene_articulations=["/World/ir_313340r021_hose"],
        )
        self.assertEqual(suggestions, [])

    async def test_prim_suggestions_plant_notation_ignores_other_name_shapes(self):
        """Controller names outside the alpha+digits+rNN shape skip the rule
        entirely."""
        suggestions = list_motion_group_prim_suggestions(
            configs=[],
            cell="cell",
            controller="k8urw1313340r02",
            motion_group="0@k8urw1313340r02",
            scene_articulations=["/World/ir_313340r02_hose"],
        )
        self.assertEqual(suggestions, [])

    async def test_prim_suggestions_exact_name_beats_plant_notation(self):
        """An exact prim-name match outranks the suffix rule (which would be
        ambiguous here, since both prims embed the controller name)."""
        suggestions = list_motion_group_prim_suggestions(
            configs=[],
            cell="cell",
            controller="ir340r02",
            motion_group="0@ir340r02",
            scene_articulations=["/World/ir340r02", "/World/ir_313340r02_hose"],
        )
        self.assertEqual(suggestions, ["/World/ir340r02"])

    async def test_prim_suggestions_plant_notation_beats_model_match(self):
        """The suffix rule sits above the model-name fallback: a unique
        name-derived hit wins over a prim whose custom-data model matches."""
        with self._create_stage() as stage:
            prim = UsdGeom.Xform.Define(stage, "/World/other_robot").GetPrim()
            prim.SetCustomData({"motionGroupModel": "KUKA_KR240_R2900"})

            suggestions = list_motion_group_prim_suggestions(
                configs=[],
                cell="cell",
                controller="ir340r02",
                motion_group="0@ir340r02",
                scene_articulations=[
                    "/World/other_robot",
                    "/World/ir_313340r02_hose",
                ],
                motion_group_model_name="KUKA_KR240_R2900",
            )
            self.assertEqual(suggestions, ["/World/ir_313340r02_hose"])

    async def test_prim_discovery_tracks_stage_switches(self):
        """Discovery must re-traverse after a stage switch, even without a
        stage event. In-memory stages reuse anonymous root-layer identifiers,
        so a cache keyed on the identifier can serve the previous stage's
        prims. The cycle repeats because the reuse depends on the allocator.
        """
        for index in range(10):
            with self._create_stage() as stage:
                config = _make_robot_config(
                    prim_path=f"/World/Robot_{index}",
                    host=NOVA_HOST_1,
                    cell="cell",
                    controller=f"bot{index}",
                    motion_group=f"0@bot{index}",
                )
                _apply_robots_to_stage(stage, [config])
                paths = get_scene_motion_group_prim_paths(
                    include_prims_without_api=False
                )
                self.assertEqual(paths, [f"/World/Robot_{index}"])


class TestRobotPrimResolution(omni.kit.test.AsyncTestCase):
    """A connected motion group can sit on a descendant of the robot prim."""

    @contextmanager
    def _create_stage(self):
        stage = Usd.Stage.CreateInMemory("TestRobotPrimResolution")
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        with use_stage(stage):
            yield stage

    async def test_model_name_comes_from_the_robot_ancestor(self):
        with self._create_stage() as stage:
            robot = UsdGeom.Xform.Define(stage, "/World/UR10e").GetPrim()
            robot.SetCustomData({"motionGroupModel": "UR10e"})
            joint = UsdGeom.Xform.Define(stage, "/World/UR10e/root_joint").GetPrim()

            self.assertEqual(robot, find_robot_prim(joint))
            self.assertEqual("UR10e", model_name_from_prim(joint))
            self.assertEqual("UR10e", get_prim_model_name("/World/UR10e/root_joint"))

    async def test_prim_with_own_custom_data_wins(self):
        with self._create_stage() as stage:
            robot = UsdGeom.Xform.Define(stage, "/World/UR10e").GetPrim()
            robot.SetCustomData({"motionGroupModel": "UR10e"})
            nested = UsdGeom.Xform.Define(stage, "/World/UR10e/UR5e").GetPrim()
            nested.SetCustomData({"motion_group_name": "UR5e"})

            self.assertEqual(nested, find_robot_prim(nested))
            self.assertEqual("UR5e", model_name_from_prim(nested))

    async def test_model_name_comes_from_a_v2_robot_ancestor(self):
        # Downloaded robots carry the model under name, next to
        # robot-configuration, instead of motionGroupModel.
        with self._create_stage() as stage:
            robot = UsdGeom.Xform.Define(stage, "/World/abb").GetPrim()
            robot.SetCustomData(
                {
                    "name": "ABB_4600_255_40",
                    "robot-configuration": {"id": 0, "name": "abb-irb4600_255_40"},
                }
            )
            joint = UsdGeom.Xform.Define(stage, "/World/abb/root_joint").GetPrim()

            self.assertEqual(robot, find_robot_prim(joint))
            self.assertEqual("ABB_4600_255_40", model_name_from_prim(joint))
            self.assertEqual(
                "ABB_4600_255_40", get_prim_model_name("/World/abb/root_joint")
            )

    async def test_prim_without_robot_data_anywhere_keeps_itself(self):
        with self._create_stage() as stage:
            joint = UsdGeom.Xform.Define(stage, "/World/Rig/root_joint").GetPrim()

            self.assertEqual(joint, find_robot_prim(joint))
            self.assertIsNone(model_name_from_prim(joint))


class TestStalePrimPaths(omni.kit.test.AsyncTestCase):
    """A prim path that does not resolve must not raise.

    The stage-config readers feed get_motion_group_configuration_from_prim from
    the cached path list of get_scene_motion_group_prim_paths, which can name
    prims that no longer exist. HasAPI raises "Accessed invalid null prim" for
    those rather than returning False.
    """

    async def test_null_prim_yields_no_configuration(self):
        # No use_stage() needed: the prim is handed in directly, and the guard has
        # to return before it touches the stage at all.
        stage = Usd.Stage.CreateInMemory("TestStalePrimPaths")
        missing = stage.GetPrimAtPath("/World/does_not_exist")
        self.assertFalse(missing.IsValid())
        self.assertIsNone(get_motion_group_configuration_from_prim(missing))

    async def test_none_yields_no_configuration(self):
        self.assertIsNone(get_motion_group_configuration_from_prim(None))


class TestStageDiscoveryHostNormalization(omni.kit.test.AsyncTestCase):
    """Host classification must not care about the scheme.

    known_hosts and the *host* filter come from ``NOVAInstance.host`` (a cloud
    instance keeps its scheme) while the stored config host was stripped of its
    scheme by ``apply_to_prim``. Comparing verbatim made a known cloud instance
    look like an orphan and hid its cells.
    """

    async def test_known_cloud_host_with_scheme_is_not_an_orphan(self):
        configs = [
            _make_robot_config(
                "/World/robot", "nova.example.io", "cell", "ctrl", "0@ctrl"
            )
        ]
        orphans = filter_unknown_host_instances(configs, {"https://nova.example.io"})
        self.assertEqual(orphans, [])

    async def test_genuinely_unknown_host_still_reported(self):
        configs = [
            _make_robot_config(
                "/World/robot", "other.example.io", "cell", "ctrl", "0@ctrl"
            )
        ]
        orphans = filter_unknown_host_instances(configs, {"https://nova.example.io"})
        self.assertEqual([o.host for o in orphans], ["other.example.io"])

    async def test_cells_found_for_scheme_carrying_host(self):
        configs = [
            _make_robot_config(
                "/World/robot", "nova.example.io", "cell", "ctrl", "0@ctrl"
            )
        ]
        cells = list_cells_for_host(configs, "https://nova.example.io")
        self.assertEqual([c.name for c in cells], ["cell"])
