"""Unit tests for pointing a prim's motion group configuration at a controller."""

import omni.kit.test
from pxr import Usd, UsdGeom

from wandelbots.omni.instances.instances_service import (
    retarget_motion_group_configuration,
)
from wandelbots.omni.instances.models import NOVAControllerData, NOVACustomInstance
from wandelbots.omni.manipulators import (
    MotionGroupConfiguration,
    MotionStreamConfiguration,
)
from wandelbots.omni.usd.schema_utils import SchemaUtils

_PRIM_PATH = "/World/robot_01/KUKA_KR10_R900"
_NEW_HOST = "172.31.13.206"


def _instance() -> NOVACustomInstance:
    return NOVACustomInstance(
        host=_NEW_HOST, name=_NEW_HOST, is_secure_connection=False
    )


def _controller() -> NOVAControllerData:
    return NOVAControllerData(name="kuka-kr10-r900-2", cell_name="cell")


class TestRetargetMotionGroupConfiguration(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._stage = Usd.Stage.CreateInMemory("TestRetarget")
        UsdGeom.Xform.Define(self._stage, _PRIM_PATH)

    def _prim(self) -> Usd.Prim:
        return self._stage.GetPrimAtPath(_PRIM_PATH)

    def _connect_prim(self, **overrides) -> None:
        """Author a previous connection with non-default user settings."""
        stream = MotionStreamConfiguration(
            host="old-host.example.com",
            secure_connection=True,
            cell="old-cell",
            controller="old-controller",
            motion_group="0@old-controller",
            use_external_joint_stream=True,
            response_rate=64,
        )
        for name, value in overrides.items():
            setattr(stream, name, value)
        MotionGroupConfiguration(
            name="0@old-controller",
            prim_path=_PRIM_PATH,
            enabled=False,
            motion_stream_configuration=stream,
        ).apply_to_prim(self._stage)

    def _retarget(self, **kwargs) -> MotionGroupConfiguration:
        return retarget_motion_group_configuration(
            prim=self._prim(),
            prim_path=_PRIM_PATH,
            instance=_instance(),
            controller=_controller(),
            motion_group_name="0@kuka-kr10-r900-2",
            **kwargs,
        )

    async def test_connected_prim_keeps_its_user_settings(self):
        self._connect_prim()

        configuration = self._retarget()

        self.assertFalse(configuration.enabled)
        self.assertEqual(64, configuration.motion_stream_configuration.response_rate)
        self.assertTrue(
            configuration.motion_stream_configuration.use_external_joint_stream
        )

    async def test_connected_prim_points_at_the_new_motion_group(self):
        self._connect_prim()

        stream = self._retarget().motion_stream_configuration

        self.assertEqual(_NEW_HOST, stream.host)
        self.assertFalse(stream.secure_connection)
        self.assertEqual("cell", stream.cell)
        self.assertEqual("kuka-kr10-r900-2", stream.controller)
        self.assertEqual("0@kuka-kr10-r900-2", stream.motion_group)

    async def test_explicit_joint_stream_source_wins(self):
        self._connect_prim()

        configuration = self._retarget(use_external_joint_stream=False)

        self.assertFalse(
            configuration.motion_stream_configuration.use_external_joint_stream
        )

    async def test_prim_without_motion_group_api_starts_from_defaults(self):
        configuration = self._retarget()

        self.assertTrue(configuration.enabled)
        self.assertEqual(_NEW_HOST, configuration.motion_stream_configuration.host)
        self.assertFalse(
            configuration.motion_stream_configuration.use_external_joint_stream
        )

    async def test_unconnected_prim_with_api_starts_from_defaults(self):
        # Robots ship with MotionGroupAPI applied but no host authored.
        SchemaUtils.ensure_motion_group_api(self._prim())

        configuration = self._retarget(use_external_joint_stream=True)

        self.assertTrue(configuration.enabled)
        self.assertEqual(_NEW_HOST, configuration.motion_stream_configuration.host)
        self.assertTrue(
            configuration.motion_stream_configuration.use_external_joint_stream
        )
