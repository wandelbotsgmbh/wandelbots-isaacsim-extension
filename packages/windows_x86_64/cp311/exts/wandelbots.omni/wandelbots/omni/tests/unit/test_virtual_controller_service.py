import omni.kit.test
from pxr import Usd

from wandelbots.omni.instances.models import NOVACustomInstance
from wandelbots.omni.ui.instances.articulations.virtual_controller_service import (
    connect_motion_group_with_retry,
    preset_configuration_from_prim,
    preset_manufacturer_from_prim,
    resolve_robot_configuration,
)

_PRESETS = {"manufacturer": "kuka", "robot_configuration_name": "kuka-kr10-r900"}


class _ScriptedInstancesService:
    """Answers each connect attempt from a scripted list of (success, message)."""

    def __init__(self, outcomes: list[tuple[bool, str]]):
        self._outcomes = list(outcomes)
        self.attempts = 0
        self.joint_stream_arguments: list = []

    def create_motion_group_from_nova(
        self,
        instance,
        controller,
        motion_group_name,
        prim_path,
        use_external_joint_stream=None,
        callback=None,
    ):
        self.attempts += 1
        self.joint_stream_arguments.append(use_external_joint_stream)
        success, message = self._outcomes.pop(0)
        callback(success, message)


def _instance() -> NOVACustomInstance:
    return NOVACustomInstance(
        host="nova.invalid", name="nova.invalid", is_reachable=False
    )


class TestPresetConfiguration(omni.kit.test.AsyncTestCase):
    def _prim_with_custom_data(self, custom_data: dict) -> Usd.Prim:
        self._stage = Usd.Stage.CreateInMemory()
        prim = self._stage.DefinePrim("/World/robot", "Xform")
        for key, value in custom_data.items():
            prim.SetCustomDataByKey(key, value)
        return prim

    async def test_both_keys_present(self):
        prim = self._prim_with_custom_data(_PRESETS)
        self.assertEqual(
            ("kuka", "kuka-kr10-r900"), preset_configuration_from_prim(prim)
        )

    async def test_missing_key_yields_none(self):
        prim = self._prim_with_custom_data({"manufacturer": "kuka"})
        self.assertIsNone(preset_configuration_from_prim(prim))

    async def test_presets_come_from_the_robot_ancestor(self):
        # The connected motion group can sit on a descendant of the robot prim.
        self._prim_with_custom_data({**_PRESETS, "motionGroupModel": "KUKA_KR10_R900"})
        joint = self._stage.DefinePrim("/World/robot/root_joint", "Xform")

        self.assertEqual(
            ("kuka", "kuka-kr10-r900"), preset_configuration_from_prim(joint)
        )
        self.assertEqual("kuka", preset_manufacturer_from_prim(joint))

    async def test_resolve_prefers_presets_without_asking_nova(self):
        prim = self._prim_with_custom_data(_PRESETS)
        self.assertEqual(
            ("kuka", "kuka-kr10-r900"),
            await resolve_robot_configuration(_instance(), prim, "KUKA_KR10_R900"),
        )


class TestConnectMotionGroupWithRetry(omni.kit.test.AsyncTestCase):
    async def _connect(self, service, attempts: int) -> tuple[bool, str]:
        return await connect_motion_group_with_retry(
            service,
            _instance(),
            cell="cell",
            controller_name="kuka",
            motion_group_name="0@kuka",
            prim_path="/World/kuka",
            attempts=attempts,
            delay=0.0,
        )

    async def test_succeeds_after_a_failed_attempt(self):
        service = _ScriptedInstancesService([(False, "not yet"), (True, "")])
        success, message = await self._connect(service, attempts=3)
        self.assertTrue(success)
        self.assertEqual("", message)
        self.assertEqual(2, service.attempts)

    async def test_keeps_the_joint_stream_source_of_the_prim(self):
        # Recreating a controller must not reset the setting on the prim, so no
        # joint stream source is passed down.
        service = _ScriptedInstancesService([(True, "")])
        await self._connect(service, attempts=1)
        self.assertEqual([None], service.joint_stream_arguments)

    async def test_reports_last_message_when_attempts_are_exhausted(self):
        service = _ScriptedInstancesService([(False, "first"), (False, "second")])
        success, message = await self._connect(service, attempts=2)
        self.assertFalse(success)
        self.assertEqual("second", message)
        self.assertEqual(2, service.attempts)
