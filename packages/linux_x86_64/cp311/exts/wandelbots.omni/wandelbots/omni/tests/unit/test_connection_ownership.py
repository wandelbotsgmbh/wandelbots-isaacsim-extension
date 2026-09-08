"""Unit tests for host normalization and connection ownership.

The connector used to decide "is this motion group connected to *this*
instance?" with a verbatim string comparison. That never matched for cloud
instances (the portal reports ``https://x.wandelbots.io`` while the prim stores
the scheme-less ``x.wandelbots.io``), so the collapsed row rendered as connected
while the expanded body still offered "Connect".
"""

import omni.kit.test

from wandelbots.omni.instances.models import NOVACloudInstance, NOVACustomInstance
from wandelbots.omni.manipulators import MotionGroupConfiguration
from wandelbots.omni.manipulators.motion_stream_configuration import (
    MotionStreamConfiguration,
)
from wandelbots.omni.utils.hosts import normalize_host


def _config(host: str, secure: bool = False) -> MotionGroupConfiguration:
    return MotionGroupConfiguration(
        name="0@ur10e",
        prim_path="/World/robot",
        motion_stream_configuration=MotionStreamConfiguration(
            host=host,
            secure_connection=secure,
            cell="cell",
            controller="ur10e",
            motion_group="0@ur10e",
        ),
    )


def _custom(host: str) -> NOVACustomInstance:
    return NOVACustomInstance(host=host, name=host)


def _cloud(host: str) -> NOVACloudInstance:
    return NOVACloudInstance(
        host=host,
        auth_config_id="auth",
        expires_at=1,
        instance_id="id",
        obsolete_at=1,
        sandbox_name="sandbox",
        status="running",
    )


class TestNormalizeHost(omni.kit.test.AsyncTestCase):
    async def test_strips_scheme(self):
        self.assertEqual(normalize_host("https://nova.example.com"), "nova.example.com")
        self.assertEqual(normalize_host("http://nova.example.com"), "nova.example.com")

    async def test_keeps_port(self):
        self.assertEqual(normalize_host("http://10.0.0.1:8080"), "10.0.0.1:8080")

    async def test_strips_trailing_slash_and_case(self):
        self.assertEqual(
            normalize_host("HTTPS://Nova.Example.COM/"), "nova.example.com"
        )

    async def test_scheme_less_host_untouched(self):
        self.assertEqual(normalize_host("10.20.30.40"), "10.20.30.40")

    async def test_empty(self):
        self.assertEqual(normalize_host(""), "")
        self.assertEqual(normalize_host(None), "")


class TestOwnsConnection(omni.kit.test.AsyncTestCase):
    async def test_scheme_only_difference_still_owned(self):
        # The regression: the prim stores the scheme-less host, the cloud
        # instance keeps the scheme.
        instance = _cloud("https://xyz.instance.wandelbots.io")
        self.assertTrue(
            instance.owns_connection(_config("xyz.instance.wandelbots.io", secure=True))
        )

    async def test_secure_flag_mismatch_does_not_disown(self):
        instance = _cloud("https://xyz.instance.wandelbots.io")
        self.assertTrue(
            instance.owns_connection(
                _config("xyz.instance.wandelbots.io", secure=False)
            )
        )

    async def test_plain_on_prem_host_owned(self):
        self.assertTrue(_custom("10.20.30.40").owns_connection(_config("10.20.30.40")))

    async def test_different_host_not_owned(self):
        # Hostname vs. IP of the same machine are still different instances as
        # far as the panel can tell - it must offer Connect, not claim connected.
        self.assertFalse(
            _custom("10.20.30.40").owns_connection(_config("nova-cell.example.local"))
        )

    async def test_empty_stored_host_not_owned(self):
        # Robots ship with MotionGroupAPI applied but empty attributes.
        self.assertFalse(_custom("10.20.30.40").owns_connection(_config("")))

    async def test_none_config_not_owned(self):
        self.assertFalse(_custom("10.20.30.40").owns_connection(None))
