"""Unit tests for releasing a NATS connection that is not usable.

The service is built on nats-py with ``allow_reconnect`` and
``max_reconnect_attempts = -1``, so a client that reached the server once
retries every two seconds for the life of the process and logs through its
error callback. Dropping the reference does not stop that, it only removes the
last handle that could. Isaac Sim then reports connection errors for a server
nobody is using any more, which is what happens after an instance is
reprovisioned.
"""

import omni.kit.test

from wandelbots.omni.core.networks import nats_connector
from wandelbots.omni.core.networks.nats_connector import NatsSubscriptionService


class FakeSubscription:
    def __init__(self):
        self.unsubscribed = False

    async def unsubscribe(self):
        self.unsubscribed = True


class FakeConnection:
    """A nats-py client as far as the service is concerned."""

    def __init__(self, subscribes: bool = True):
        self._subscribes = subscribes
        self.is_connected = True
        self.closed = False
        self.subscription = None

    async def subscribe(self, subject, cb=None):
        if not self._subscribes:
            raise ConnectionError("subscribe refused")
        self.subscription = FakeSubscription()
        return self.subscription

    async def close(self):
        self.closed = True
        self.is_connected = False

    def lose_the_server(self):
        """The state nats-py sits in while it retries in the background."""
        self.is_connected = False


def _service(context_name: str = "test") -> NatsSubscriptionService:
    async def handler(_message):
        return None

    return NatsSubscriptionService(
        base_url="https://host",
        subject="fleet.>",
        message_handler=handler,
        context_name=context_name,
    )


class _ConnectionPatch:
    """Makes connect_to_nats hand out the given connections in order."""

    def __init__(self, test, connections):
        self._test = test
        self._connections = list(connections)
        self.handed_out = []

    def __enter__(self):
        self._original = nats_connector.connect_to_nats

        async def fake_connect_to_nats(**_kwargs):
            if not self._connections:
                return None
            connection = self._connections.pop(0)
            self.handed_out.append(connection)
            return connection

        nats_connector.connect_to_nats = fake_connect_to_nats
        return self

    def __exit__(self, *_exc):
        nats_connector.connect_to_nats = self._original
        return False


class TestConnectReleasesUnusableConnections(omni.kit.test.AsyncTestCase):
    async def test_a_failed_subscription_closes_the_connection(self):
        connection = FakeConnection(subscribes=False)
        service = _service()

        with _ConnectionPatch(self, [connection]):
            connected = await service.connect()

        self.assertFalse(connected, "a service with no subscription is not connected")
        self.assertTrue(connection.closed, "the live connection has to be closed")
        self.assertIsNone(service.connection)
        self.assertFalse(service.is_subscribed)

    async def test_a_half_open_service_does_not_leak_on_the_next_connect(self):
        first = FakeConnection(subscribes=False)
        second = FakeConnection()
        service = _service()

        with _ConnectionPatch(self, [first, second]):
            await service.connect()
            connected = await service.connect()

        self.assertTrue(connected)
        self.assertTrue(first.closed, "the earlier connection must not be orphaned")
        self.assertFalse(second.closed)
        self.assertTrue(service.is_connected)
        self.assertTrue(service.is_subscribed)

    async def test_a_successful_connect_keeps_the_connection(self):
        connection = FakeConnection()
        service = _service()

        with _ConnectionPatch(self, [connection]):
            connected = await service.connect()

        self.assertTrue(connected)
        self.assertFalse(connection.closed)
        self.assertTrue(service.is_subscribed)

    async def test_connecting_twice_reuses_the_live_connection(self):
        connection = FakeConnection()
        service = _service()

        with _ConnectionPatch(self, [connection]) as patch:
            await service.connect()
            connected = await service.connect()

        self.assertTrue(connected)
        self.assertEqual(len(patch.handed_out), 1, "the second call must not redial")
        self.assertFalse(connection.closed)

    async def test_no_connection_at_all_is_reported_as_failure(self):
        service = _service()

        with _ConnectionPatch(self, []):
            connected = await service.connect()

        self.assertFalse(connected)
        self.assertIsNone(service.connection)


class TestDisconnectReleasesEverything(omni.kit.test.AsyncTestCase):
    async def test_disconnect_unsubscribes_and_closes(self):
        connection = FakeConnection()
        service = _service()

        with _ConnectionPatch(self, [connection]):
            await service.connect()
        subscription = connection.subscription
        await service.disconnect()

        self.assertTrue(subscription.unsubscribed)
        self.assertTrue(connection.closed)
        self.assertIsNone(service.connection)
        self.assertFalse(service.is_subscribed)

    async def test_disconnect_is_safe_without_a_connection(self):
        service = _service()

        await service.disconnect()

        self.assertIsNone(service.connection)
        self.assertFalse(service.is_connected)


class TestReconnectingClientsAreClosed(omni.kit.test.AsyncTestCase):
    """nats-py reports is_connected False while it retries, and that client is
    exactly the one that keeps logging if it is dropped instead of closed."""

    async def test_disconnect_closes_a_client_that_is_retrying(self):
        connection = FakeConnection()
        service = _service()

        with _ConnectionPatch(self, [connection]):
            await service.connect()
        connection.lose_the_server()
        await service.disconnect()

        self.assertTrue(connection.closed, "a retrying client has to be closed")
        self.assertIsNone(service.connection)

    async def test_reconnecting_does_not_orphan_a_retrying_client(self):
        first = FakeConnection()
        second = FakeConnection()
        service = _service()

        with _ConnectionPatch(self, [first, second]):
            await service.connect()
            first.lose_the_server()
            # A dropped subscription is what the service sees when the server
            # goes away, so the guard in connect() does not short-circuit.
            service.subscription = None
            connected = await service.connect()

        self.assertTrue(connected)
        self.assertTrue(first.closed, "the retrying client must not be orphaned")
        self.assertFalse(second.closed)

    async def test_a_failed_connect_closes_a_retrying_client(self):
        first = FakeConnection()
        service = _service()

        with _ConnectionPatch(self, [first]):
            await service.connect()
            first.lose_the_server()
            service.subscription = None
            # No connection left to hand out, so this connect fails.
            connected = await service.connect()

        self.assertFalse(connected)
        self.assertTrue(first.closed)
        self.assertIsNone(service.connection)
