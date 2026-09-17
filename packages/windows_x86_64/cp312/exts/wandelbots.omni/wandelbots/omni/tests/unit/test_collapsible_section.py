import omni.kit.app
import omni.kit.test
import omni.ui as ui

from wandelbots.omni.ui.widgets.collapsible_section import CollapsibleSection


class TestCollapsibleSection(omni.kit.test.AsyncTestCase):
    async def setUp(self):
        self._window = ui.Window("TestCollapsibleSection", width=400, height=300)

    async def tearDown(self):
        self._window.destroy()
        self._window = None

    async def _build(self, **kwargs) -> CollapsibleSection:
        with self._window.frame:
            section = CollapsibleSection(title="UR10e", **kwargs)
        # Widgets are laid out on the next app update; a build error surfaces here.
        await omni.kit.app.get_app().next_update_async()
        return section

    async def test_builds_with_subtitle(self):
        section = await self._build(subtitle="/World/Robots/ur10e_01")
        self.assertEqual("UR10e", section.title)
        self.assertTrue(section.collapsed)
        section.collapsed = False
        self.assertFalse(section.collapsed)

    async def test_builds_without_subtitle(self):
        section = await self._build()
        self.assertEqual("UR10e", section.title)
        self.assertTrue(section.collapsed)

    async def test_builds_with_title_icon(self):
        section = await self._build(title_icon="warning.svg")
        self.assertTrue(section.collapsed)
