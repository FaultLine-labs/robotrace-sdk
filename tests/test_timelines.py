"""Tests for multi-timeline support."""

import threading
import time

from robotrace.timelines import TimelineContext, get_timeline_context


class TestTimelineContext:
    def test_set_sequence(self):
        ctx = TimelineContext()
        ctx.set_time("frame_idx", sequence=42)
        assert ctx.timelines["frame_idx"] == ("sequence", 42)

    def test_set_timestamp_int(self):
        ctx = TimelineContext()
        ctx.set_time("sensor_clock", timestamp=1711536000000000000)
        assert ctx.timelines["sensor_clock"] == ("timestamp", 1711536000000000000)

    def test_set_timestamp_float(self):
        ctx = TimelineContext()
        ctx.set_time("sensor_clock", timestamp=1711536000.5)
        assert ctx.timelines["sensor_clock"] == ("timestamp", 1711536000500000000)

    def test_reset(self):
        ctx = TimelineContext()
        ctx.set_time("a", sequence=1)
        ctx.set_time("b", timestamp=2)
        ctx.reset()
        assert len(ctx.timelines) == 0

    def test_snapshot_includes_auto_timelines(self):
        ctx = TimelineContext()
        snap = ctx.snapshot()
        assert "_tl_log_tick" in snap
        assert "_tl_log_tick_type" in snap
        assert snap["_tl_log_tick_type"] == "sequence"
        assert "_tl_log_time" in snap
        assert snap["_tl_log_time_type"] == "timestamp"

    def test_snapshot_includes_custom_timelines(self):
        ctx = TimelineContext()
        ctx.set_time("frame", sequence=10)
        ctx.set_time("sensor_ts", timestamp=999)
        snap = ctx.snapshot()
        assert snap["_tl_frame"] == "10"
        assert snap["_tl_frame_type"] == "sequence"
        assert snap["_tl_sensor_ts"] == "999"
        assert snap["_tl_sensor_ts_type"] == "timestamp"

    def test_log_tick_increments(self):
        ctx = TimelineContext()
        snap1 = ctx.snapshot()
        snap2 = ctx.snapshot()
        assert int(snap2["_tl_log_tick"]) == int(snap1["_tl_log_tick"]) + 1

    def test_has_custom_timelines(self):
        ctx = TimelineContext()
        assert not ctx.has_custom_timelines()
        ctx.set_time("x", sequence=1)
        assert ctx.has_custom_timelines()
        ctx.reset()
        assert not ctx.has_custom_timelines()

    def test_get_timeline_names(self):
        ctx = TimelineContext()
        ctx.set_time("a", sequence=1)
        ctx.set_time("b", timestamp=2)
        names = ctx.get_timeline_names()
        assert set(names) == {"a", "b"}


class TestThreadIsolation:
    def test_threads_are_independent(self):
        """Each thread should have its own timeline context."""
        results = {}

        def thread_fn(name, seq):
            ctx = get_timeline_context()
            ctx.set_time("thread_test", sequence=seq)
            time.sleep(0.05)  # Let other thread set its value
            snap = ctx.snapshot()
            results[name] = int(snap["_tl_thread_test"])
            ctx.reset()

        t1 = threading.Thread(target=thread_fn, args=("t1", 100))
        t2 = threading.Thread(target=thread_fn, args=("t2", 200))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Each thread should see its own value, not the other's
        assert results["t1"] == 100
        assert results["t2"] == 200
