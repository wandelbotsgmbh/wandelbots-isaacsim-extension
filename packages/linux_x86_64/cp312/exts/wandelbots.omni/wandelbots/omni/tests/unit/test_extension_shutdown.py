"""Shutting the extension down must not swallow the timeline stop.

``on_shutdown`` drops the timeline subscription before it stops the timeline,
so that stop reaches no handler. Without handing it to the streams here, an
extension reload with the simulation running leaves the robot wherever the last
command left it.
"""

import omni.kit.test

from wandelbots.omni.extension import stop_timeline_with_the_streams


class FakeTimeline:
    def __init__(self, playing: bool):
        self._playing = playing
        self.stopped = False

    def is_playing(self) -> bool:
        return self._playing

    def stop(self) -> None:
        self.stopped = True


class RecordingService:
    def __init__(self, timeline: FakeTimeline):
        self._timeline = timeline
        self.stop_seen = False
        self.timeline_was_still_playing = None

    def on_timeline_stop(self) -> None:
        self.stop_seen = True
        # The streams only get a useful frame while the timeline still runs.
        self.timeline_was_still_playing = not self._timeline.stopped


class TestStopTimelineWithTheStreams(omni.kit.test.AsyncTestCase):
    async def test_a_running_timeline_is_stopped_and_the_streams_told(self):
        timeline = FakeTimeline(playing=True)
        service = RecordingService(timeline)

        stop_timeline_with_the_streams(timeline, service)

        self.assertTrue(service.stop_seen)
        self.assertTrue(timeline.stopped)

    async def test_the_streams_are_told_before_the_timeline_stops(self):
        """Order matters: stop_streams runs a frame later, so the hook needs
        the stage while it is still live."""
        timeline = FakeTimeline(playing=True)
        service = RecordingService(timeline)

        stop_timeline_with_the_streams(timeline, service)

        self.assertTrue(service.timeline_was_still_playing)

    async def test_a_stopped_timeline_is_left_alone(self):
        timeline = FakeTimeline(playing=False)
        service = RecordingService(timeline)

        stop_timeline_with_the_streams(timeline, service)

        self.assertFalse(service.stop_seen)
        self.assertFalse(timeline.stopped)
