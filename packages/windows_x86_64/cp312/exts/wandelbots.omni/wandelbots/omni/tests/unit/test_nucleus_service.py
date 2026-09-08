"""Unit tests for the Nucleus server API flow.

Regression coverage for POST /nucleus/server: writing a plain omni.client
bookmark is not enough on Isaac Sim 6.0, because the filepicker 3.x omniverse
collection is fed by omni.kit.widget.connection_manager instead of client
bookmarks. add_nucleus_server() therefore has to go through the connection
manager (6.0) or the filepicker global event (5.1) so that the server shows
up in list_nucleus_servers() and the token endpoints can resolve its name.
"""

import asyncio
from unittest import mock

import omni.client
import omni.kit.app
import omni.kit.test
from omni.kit.window.content_browser import get_content_instance

from wandelbots.omni.core.nucleus import nucleus_service
from wandelbots.omni.core.nucleus.nucleus_service import (
    NucleusServerModel,
    NucleusService,
)

_TEST_SERVER = NucleusServerModel(
    name="wandelbots-unit-test-server",
    url="omniverse://unit-test-nucleus.invalid",
)


class TestAddNucleusServerWiring(omni.kit.test.AsyncTestCase):
    """Pin add_nucleus_server() to the version-appropriate connection path."""

    async def test_uses_connection_manager_when_available(self):
        if not nucleus_service._HAS_CONNECTION_MANAGER:
            self.skipTest("connection manager not available on this Isaac Sim version")
        connection_manager = mock.Mock()
        with mock.patch.object(
            nucleus_service, "get_connection_manager", return_value=connection_manager
        ):
            NucleusService().add_nucleus_server(_TEST_SERVER)
        connection_manager.registry.add_manual_server.assert_called_once_with(
            _TEST_SERVER.name, _TEST_SERVER.url
        )

    async def test_raises_when_connection_manager_is_not_running(self):
        if not nucleus_service._HAS_CONNECTION_MANAGER:
            self.skipTest("connection manager not available on this Isaac Sim version")
        with mock.patch.object(
            nucleus_service, "get_connection_manager", return_value=None
        ):
            with self.assertRaises(RuntimeError):
                NucleusService().add_nucleus_server(_TEST_SERVER)

    async def test_queues_filepicker_event_without_connection_manager(self):
        if nucleus_service._HAS_CONNECTION_MANAGER:
            self.skipTest("connection manager available on this Isaac Sim version")
        with mock.patch.object(omni.kit.app, "queue_event") as queue_event:
            NucleusService().add_nucleus_server(_TEST_SERVER)
        queue_event.assert_called_once_with(
            nucleus_service.NUCLEUS_SERVER_ADDED_GLOBAL_EVENT,
            {"name": _TEST_SERVER.name, "url": _TEST_SERVER.url},
        )


class TestNucleusServerRoundTrip(omni.kit.test.AsyncTestCase):
    """Add + list round-trip through the real filepicker collections.

    This is the flow that regressed when add_nucleus_server() wrote a plain
    bookmark: the server never appeared in GET /nucleus/servers, so the token
    endpoints returned 404 for its name.
    """

    async def test_added_server_is_listed(self):
        if not self._content_browser_view_available():
            self.skipTest("content browser view is not available")

        service = NucleusService()
        service.add_nucleus_server(_TEST_SERVER)
        try:
            app = omni.kit.app.get_app()
            # The server lands in the content browser view asynchronously: via
            # a queued event on 6.0, via the omni.client bookmark-changed
            # callback on 5.1. Poll with a wall-clock bound, frames alone pass
            # too quickly in headless test runs.
            for _ in range(100):
                if _TEST_SERVER.name in service.list_nucleus_servers():
                    break
                await app.next_update_async()
                await asyncio.sleep(0.05)
            self.assertIn(_TEST_SERVER.name, service.list_nucleus_servers())
        finally:
            self._remove_test_server()
            for _ in range(5):
                await omni.kit.app.get_app().next_update_async()

    @staticmethod
    def _content_browser_view_available() -> bool:
        content_instance = get_content_instance()
        if content_instance is None:
            return False
        try:
            return content_instance._window._widget._view is not None
        except AttributeError:
            return False

    @staticmethod
    def _remove_test_server():
        if nucleus_service._HAS_CONNECTION_MANAGER:
            connection_manager = nucleus_service.get_connection_manager()
            if connection_manager is not None:
                connection_manager.registry.remove_manual_server(_TEST_SERVER.url)
            return
        omni.client.remove_bookmark(_TEST_SERVER.name)
