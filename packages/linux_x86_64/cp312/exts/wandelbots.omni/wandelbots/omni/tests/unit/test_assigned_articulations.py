import omni.kit.test

from wandelbots.omni.instances.instances_service import NOVAInstancesService
from wandelbots.omni.instances.models import (
    NOVACellData,
    NOVAControllerData,
    NOVACustomInstance,
    NOVAMotionGroupData,
)
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    MotionStreamConfiguration,
)
from wandelbots.omni.ui.instances.articulations.assigned_articulations import (
    AssignedArticulations,
)

_HOST = "172.31.13.206"
_LIVE_CONTROLLER = "fanuc-m900ib400l"
_MISSING_CONTROLLER = "kuka-kr10-r900-2"


def _connected_config(prim_path: str, controller: str) -> MotionGroupConfiguration:
    return MotionGroupConfiguration(
        name=f"0@{controller}",
        prim_path=prim_path,
        motion_stream_configuration=MotionStreamConfiguration(
            host=_HOST,
            secure_connection=False,
            cell="cell",
            controller=controller,
            motion_group=f"0@{controller}",
            use_external_joint_stream=False,
        ),
    )


def _reachable_instance() -> NOVACustomInstance:
    motion_group = NOVAMotionGroupData(
        name=f"0@{_LIVE_CONTROLLER}", motion_group_model_name="FANUC_M900iB400L"
    )
    controller = NOVAControllerData(
        name=_LIVE_CONTROLLER, cell_name="cell", motion_groups=[motion_group]
    )
    return NOVACustomInstance(
        host=_HOST,
        name=_HOST,
        is_reachable=True,
        cells=[NOVACellData(name="cell", controllers=[controller])],
    )


class TestCollectAllGroups(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._service = NOVAInstancesService()

    def _group_names(self, instance) -> list[tuple[str, str]]:
        groups = AssignedArticulations.collect_all_groups(self._service, instance)
        return [(controller.name, mg.name) for controller, mg in groups]

    async def test_live_controller_gets_one_section(self):
        self._service.add_to_connected_motion_groups(
            "/World/fanuc", _connected_config("/World/fanuc", _LIVE_CONTROLLER)
        )
        self.assertEqual(
            [(_LIVE_CONTROLLER, f"0@{_LIVE_CONTROLLER}")],
            self._group_names(_reachable_instance()),
        )

    async def test_missing_controller_keeps_a_section_on_reachable_instance(self):
        self._service.add_to_connected_motion_groups(
            "/World/kuka", _connected_config("/World/kuka", _MISSING_CONTROLLER)
        )
        self.assertIn(
            (_MISSING_CONTROLLER, f"0@{_MISSING_CONTROLLER}"),
            self._group_names(_reachable_instance()),
        )

    async def test_missing_controller_model_name_falls_back_to_motion_group(self):
        # No prim exists at that path, so no custom data can supply the model.
        self._service.add_to_connected_motion_groups(
            "/World/kuka", _connected_config("/World/kuka", _MISSING_CONTROLLER)
        )
        groups = AssignedArticulations.collect_all_groups(
            self._service, _reachable_instance()
        )
        motion_group = next(
            mg for controller, mg in groups if controller.name == _MISSING_CONTROLLER
        )
        self.assertEqual(
            f"0@{_MISSING_CONTROLLER}", motion_group.motion_group_model_name
        )

    async def test_unreachable_instance_keeps_stored_connection(self):
        instance = NOVACustomInstance(host=_HOST, name=_HOST, is_reachable=False)
        self._service.add_to_connected_motion_groups(
            "/World/kuka", _connected_config("/World/kuka", _MISSING_CONTROLLER)
        )
        self.assertEqual(
            [(_MISSING_CONTROLLER, f"0@{_MISSING_CONTROLLER}")],
            self._group_names(instance),
        )
