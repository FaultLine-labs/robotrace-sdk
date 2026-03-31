"""Tests for SensorPipeline — data routing and buffering (no HTTP)."""

import json
import time
from unittest.mock import patch, MagicMock

import pytest

from robotrace.types import Scalar, VectorN, Pose3D, Vector3


class TestSensorPipeline:
    """Tests for SensorPipeline data routing and buffering."""

    def _make_pipeline(self, **kwargs):
        """Create a SensorPipeline with no HTTP client and no offline queue."""
        from robotrace.sensor import SensorPipeline

        defaults = dict(
            host="http://fake",
            public_key="pk",
            secret_key="sk",
            device_id="test-device",
            http_client=None,
        )
        defaults.update(kwargs)

        with patch("robotrace.sensor.OfflineQueue") as MockOQ:
            MockOQ.return_value = MagicMock()
            pipeline = SensorPipeline(**defaults)

        return pipeline

    def _shutdown(self, pipeline):
        pipeline._shutdown_event.set()
        if pipeline._flush_thread.is_alive():
            pipeline._flush_thread.join(timeout=2)

    def test_construction_creates_batch_and_buffers(self):
        p = self._make_pipeline()
        assert isinstance(p._batch, list)
        assert len(p._batch) == 0
        self._shutdown(p)

    def test_log_adds_sample_to_batch(self):
        p = self._make_pipeline()
        p.log(stream="test/stream", value=42.0)

        with p._lock:
            assert len(p._batch) == 1
            assert p._batch[0]["stream"] == "test/stream"
            assert p._batch[0]["device_id"] == "test-device"

        self._shutdown(p)

    def test_log_scalar_routes_to_value_float(self):
        p = self._make_pipeline()
        p.log(stream="test", value=Scalar(42.0))

        with p._lock:
            assert len(p._batch) == 1
            sample = p._batch[0]
            assert sample["value_float"] == 42.0
            assert "value_json" not in sample
            assert "value_array" not in sample

        self._shutdown(p)

    def test_log_vectorn_routes_to_value_array(self):
        p = self._make_pipeline()
        p.log(stream="joints", value=VectorN(values=[1.0, 2.0, 3.0]))

        with p._lock:
            sample = p._batch[0]
            assert sample["value_array"] == [1.0, 2.0, 3.0]
            assert "value_float" not in sample
            assert "value_json" not in sample

        self._shutdown(p)

    def test_log_pose3d_routes_to_value_json(self):
        p = self._make_pipeline()
        p.log(stream="pose", value=Pose3D(x=1.0, y=2.0, z=3.0))

        with p._lock:
            sample = p._batch[0]
            assert "value_json" in sample
            parsed = json.loads(sample["value_json"])
            assert parsed["type"] == "pose3d"
            assert parsed["x"] == 1.0
            assert "value_float" not in sample

        self._shutdown(p)

    def test_log_raw_float_routes_to_value_float(self):
        p = self._make_pipeline()
        p.log(stream="voltage", value=12.6)

        with p._lock:
            sample = p._batch[0]
            assert sample["value_float"] == 12.6

        self._shutdown(p)

    def test_log_raw_int_routes_to_value_float(self):
        p = self._make_pipeline()
        p.log(stream="count", value=42)

        with p._lock:
            sample = p._batch[0]
            assert sample["value_float"] == 42.0

        self._shutdown(p)

    def test_log_dict_routes_to_value_json(self):
        p = self._make_pipeline()
        p.log(stream="custom", value={"temperature": 25.0, "humidity": 60})

        with p._lock:
            sample = p._batch[0]
            assert "value_json" in sample
            parsed = json.loads(sample["value_json"])
            assert parsed["temperature"] == 25.0

        self._shutdown(p)

    def test_get_buffer_returns_snapshot(self):
        p = self._make_pipeline()
        p.log(stream="s1", value=Scalar(1.0))
        p.log(stream="s1", value=Scalar(2.0))
        p.log(stream="s2", value=Scalar(3.0))

        buf = p.get_buffer("s1")
        assert len(buf) == 2
        assert buf[0]["value_float"] == 1.0
        assert buf[1]["value_float"] == 2.0

        # Different stream
        buf2 = p.get_buffer("s2")
        assert len(buf2) == 1

        # Non-existent stream
        buf3 = p.get_buffer("nonexistent")
        assert buf3 == []

        self._shutdown(p)

    def test_health_snapshot_returns_counts(self):
        p = self._make_pipeline()
        p.log(stream="test", value=Scalar(1.0))
        p.log(stream="test", value=Scalar(2.0))

        health = p.health_snapshot()
        assert health["buffered_count"] == 2
        assert "offline_count" in health
        assert "offline_size_bytes" in health

        self._shutdown(p)

    def test_flush_clears_batch(self):
        p = self._make_pipeline()
        p.log(stream="test", value=Scalar(1.0))

        with p._lock:
            assert len(p._batch) == 1

        # _do_flush will fail to send (no HTTP client) but should clear the batch
        # and route to offline queue. We just test the batch is cleared.
        p._do_flush()

        with p._lock:
            assert len(p._batch) == 0

        self._shutdown(p)

    def test_set_recorder_sets_and_clears(self):
        p = self._make_pipeline()
        assert p._recorder is None

        mock_recorder = MagicMock()
        p.set_recorder(mock_recorder)
        assert p._recorder is mock_recorder

        p.set_recorder(None)
        assert p._recorder is None

        self._shutdown(p)

    def test_timestamp_is_iso8601(self):
        p = self._make_pipeline()
        p.log(stream="test", value=Scalar(1.0))

        with p._lock:
            ts = p._batch[0]["timestamp"]

        # ISO 8601 format check
        assert "T" in ts
        assert "+" in ts or "Z" in ts or ts.endswith("+00:00")

        self._shutdown(p)

    def test_mission_id_included_when_provided(self):
        p = self._make_pipeline()
        p.log(stream="test", value=Scalar(1.0), mission_id="mission-123")

        with p._lock:
            assert p._batch[0]["mission_id"] == "mission-123"

        self._shutdown(p)

    def test_sensor_id_included_when_provided(self):
        p = self._make_pipeline()
        p.log(stream="test", value=Scalar(1.0), sensor_id="lidar-front")

        with p._lock:
            assert p._batch[0]["sensor_id"] == "lidar-front"

        self._shutdown(p)

    def test_mission_id_absent_when_not_provided(self):
        p = self._make_pipeline()
        p.log(stream="test", value=Scalar(1.0))

        with p._lock:
            assert "mission_id" not in p._batch[0]

        self._shutdown(p)

    def test_timeline_tags_included(self):
        """Timeline tags from set_time() should appear in sample tags."""
        from robotrace.timelines import get_timeline_context

        ctx = get_timeline_context()
        ctx.set_time("frame_idx", sequence=42)

        p = self._make_pipeline()
        p.log(stream="test", value=Scalar(1.0))

        with p._lock:
            tags = p._batch[0]["tags"]
            assert "_tl_frame_idx" in tags
            assert tags["_tl_frame_idx"] == "42"

        # Cleanup
        ctx.reset()
        self._shutdown(p)

    def test_vector3_routes_to_value_json(self):
        """Vector3 has x/y/z in dict, not 'value' or 'values', so should go to value_json."""
        p = self._make_pipeline()
        p.log(stream="vel", value=Vector3(x=1.0, y=2.0, z=3.0))

        with p._lock:
            sample = p._batch[0]
            assert "value_json" in sample
            parsed = json.loads(sample["value_json"])
            assert parsed["x"] == 1.0

        self._shutdown(p)

    def test_string_value_routes_to_value_json(self):
        p = self._make_pipeline()
        p.log(stream="status", value="active")

        with p._lock:
            sample = p._batch[0]
            assert "value_json" in sample
            parsed = json.loads(sample["value_json"])
            assert parsed["value"] == "active"

        self._shutdown(p)
