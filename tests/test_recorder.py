"""Tests for MCAP recording functionality."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from unittest.mock import patch, MagicMock

import pytest

from robotrace.recorder import McapRecorder, mcap_available, _TYPE_SCHEMAS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_mcap_path() -> str:
    """Return a temporary file path for MCAP output."""
    fd, path = tempfile.mkstemp(suffix=".mcap")
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# Tests for schema coverage
# ---------------------------------------------------------------------------

class TestTypeSchemas:
    """Verify all 17 RoboTrace types have JSON schemas."""

    EXPECTED_TYPES = [
        "scalar", "vector3", "vectorn", "pose3d", "transform3d",
        "pointcloud", "image", "depth_image", "laser_scan", "joint_state",
        "numeric_set", "bbox2d", "bbox3d", "geolocation", "path", "twist", "log",
    ]

    def test_all_types_have_schemas(self):
        for type_name in self.EXPECTED_TYPES:
            assert type_name in _TYPE_SCHEMAS, f"Missing schema for type: {type_name}"

    def test_schemas_are_valid_json_schema(self):
        for type_name, schema in _TYPE_SCHEMAS.items():
            assert schema.get("type") == "object", f"{type_name} schema is not an object"
            assert "properties" in schema, f"{type_name} schema has no properties"

    def test_schema_count(self):
        assert len(_TYPE_SCHEMAS) == 20


# ---------------------------------------------------------------------------
# Tests for McapRecorder (with mcap installed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not mcap_available(), reason="mcap package not installed")
class TestMcapRecorder:
    """Tests that require the mcap package to be installed."""

    def test_start_stop(self):
        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path)
            assert not recorder.is_recording
            recorder.start()
            assert recorder.is_recording
            assert recorder.message_count == 0
            recorder.stop()
            assert not recorder.is_recording
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0  # MCAP header written
        finally:
            os.unlink(path)

    def test_write_messages(self):
        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path, compression="")
            recorder.start()
            recorder.write("sensors/imu", {"type": "vector3", "x": 1.0, "y": 2.0, "z": 3.0})
            recorder.write("sensors/imu", {"type": "vector3", "x": 4.0, "y": 5.0, "z": 6.0})
            recorder.write("sensors/battery", {"type": "scalar", "value": 87.5})
            assert recorder.message_count == 3
            recorder.stop()
        finally:
            os.unlink(path)

    def test_write_with_explicit_timestamp(self):
        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path, compression="")
            recorder.start()
            recorder.write(
                "sensors/pose",
                {"type": "pose3d", "x": 1, "y": 2, "z": 0, "qx": 0, "qy": 0, "qz": 0, "qw": 1},
                timestamp_ns=1000000000,
            )
            assert recorder.message_count == 1
            recorder.stop()
        finally:
            os.unlink(path)

    def test_multiple_channels(self):
        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path, compression="")
            recorder.start()
            for i in range(5):
                recorder.write(f"stream/{i}", {"type": "scalar", "value": float(i)})
            assert recorder.message_count == 5
            # All 5 streams should have separate channels
            assert len(recorder._channels) == 5
            # But they share the same schema (scalar)
            assert len(recorder._schemas) == 1
            recorder.stop()
        finally:
            os.unlink(path)

    def test_write_before_start_is_noop(self):
        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path)
            recorder.write("sensors/imu", {"type": "vector3", "x": 0, "y": 0, "z": 0})
            assert recorder.message_count == 0
        finally:
            os.unlink(path)

    def test_write_after_stop_is_noop(self):
        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path, compression="")
            recorder.start()
            recorder.write("sensors/imu", {"type": "vector3", "x": 1, "y": 2, "z": 3})
            recorder.stop()
            recorder.write("sensors/imu", {"type": "vector3", "x": 4, "y": 5, "z": 6})
            assert recorder.message_count == 1
        finally:
            os.unlink(path)

    def test_double_start_warns(self):
        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path, compression="")
            recorder.start()
            # Second start should not crash (just warn)
            recorder.start()
            assert recorder.is_recording
            recorder.stop()
        finally:
            os.unlink(path)

    def test_double_stop_is_safe(self):
        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path, compression="")
            recorder.start()
            recorder.stop()
            recorder.stop()  # Should not crash
            assert not recorder.is_recording
        finally:
            os.unlink(path)

    def test_path_property(self):
        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path)
            assert recorder.path == path
        finally:
            os.unlink(path)

    def test_unknown_type_uses_default_schema(self):
        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path, compression="")
            recorder.start()
            recorder.write("custom/data", {"type": "my_custom_type", "foo": "bar"})
            assert recorder.message_count == 1
            assert "robotrace.my_custom_type" in recorder._schemas
            recorder.stop()
        finally:
            os.unlink(path)

    def test_read_back_mcap(self):
        """Write data and read it back using mcap.reader to verify file validity."""
        try:
            from mcap.reader import make_reader
        except ImportError:
            pytest.skip("mcap reader not available")

        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path, compression="")
            recorder.start()

            test_data = [
                ("sensors/imu", {"type": "vector3", "x": 1.0, "y": 2.0, "z": 9.81}),
                ("sensors/battery", {"type": "scalar", "value": 95.0}),
                ("robot/pose", {"type": "pose3d", "x": 10, "y": 20, "z": 0, "qx": 0, "qy": 0, "qz": 0, "qw": 1}),
            ]
            for stream, data in test_data:
                recorder.write(stream, data, timestamp_ns=1000000000)
            recorder.stop()

            # Read back and verify
            with open(path, "rb") as f:
                reader = make_reader(f)
                summary = reader.get_summary()
                assert summary is not None

                messages = list(reader.iter_messages())
                assert len(messages) == 3

                # Check first message content
                schema, channel, message = messages[0]
                decoded = json.loads(message.data)
                assert decoded["type"] == "vector3"
                assert decoded["x"] == 1.0
                assert channel.topic == "sensors/imu"
        finally:
            os.unlink(path)

    def test_thread_safety(self):
        """Verify concurrent writes do not corrupt the file."""
        import threading

        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path, compression="")
            recorder.start()

            errors = []

            def writer(stream_prefix: str, count: int):
                try:
                    for i in range(count):
                        recorder.write(
                            f"{stream_prefix}/data",
                            {"type": "scalar", "value": float(i)},
                        )
                except Exception as e:
                    errors.append(e)

            threads = [
                threading.Thread(target=writer, args=(f"thread_{t}", 100))
                for t in range(5)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors
            assert recorder.message_count == 500
            recorder.stop()

            # Verify the file is valid
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Tests for graceful fallback without mcap
# ---------------------------------------------------------------------------

class TestMcapGracefulFallback:
    """Test behavior when mcap is not installed."""

    def test_recorder_start_without_mcap(self):
        """start() should not crash when mcap is not installed."""
        path = _tmp_mcap_path()
        try:
            with patch("robotrace.recorder._MCAP_AVAILABLE", False):
                recorder = McapRecorder(path)
                recorder.start()  # Should log warning but not crash
                assert not recorder.is_recording
                recorder.write("sensors/imu", {"type": "vector3", "x": 0, "y": 0, "z": 0})
                assert recorder.message_count == 0
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_client_start_recording_without_mcap(self):
        """RoboTrace.start_recording() should not crash when mcap is missing."""
        from robotrace import RoboTrace

        with patch("robotrace.client.mcap_available", return_value=False):
            c = RoboTrace(
                public_key=f"test-pk-{uuid.uuid4()}",
                enabled=False,
            )
            try:
                c.start_recording("test.mcap")  # Should not crash
                assert not c.is_recording
            finally:
                c.shutdown()


# ---------------------------------------------------------------------------
# Tests for client integration
# ---------------------------------------------------------------------------

class TestClientRecordingIntegration:
    """Test recording methods on the RoboTrace client."""

    @pytest.fixture()
    def client(self):
        from robotrace import RoboTrace
        c = RoboTrace(
            host="http://localhost:9999",
            public_key=f"test-pk-{uuid.uuid4()}",
            secret_key="test-sk",
            device_id="urn:rosp:device:test:robot:001",
            enabled=False,
        )
        yield c
        c.shutdown()

    def test_is_recording_default_false(self, client):
        assert not client.is_recording

    def test_stop_recording_when_not_recording(self, client):
        result = client.stop_recording()
        assert result == ""

    @pytest.mark.skipif(not mcap_available(), reason="mcap package not installed")
    def test_start_stop_recording(self, client):
        path = _tmp_mcap_path()
        try:
            client.start_recording(path, compression="")
            assert client.is_recording
            result = client.stop_recording()
            assert result == path
            assert not client.is_recording
            assert os.path.exists(path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @pytest.mark.skipif(not mcap_available(), reason="mcap package not installed")
    def test_shutdown_finalizes_recording(self, client):
        """Recording should be finalized on shutdown even if stop was not called."""
        path = _tmp_mcap_path()
        try:
            client.start_recording(path, compression="")
            assert client.is_recording
            # Shutdown without calling stop_recording
            client.shutdown()
            assert not client.is_recording
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ---------------------------------------------------------------------------
# Tests for sensor pipeline dual-output
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not mcap_available(), reason="mcap package not installed")
class TestSensorPipelineDualOutput:
    """Test that sensor pipeline writes to both HTTP batch and MCAP."""

    def test_log_writes_to_recorder(self):
        """SensorPipeline.log() should write to the MCAP recorder when set."""
        from robotrace.sensor import SensorPipeline
        from robotrace.types import Vector3

        path = _tmp_mcap_path()
        try:
            recorder = McapRecorder(path, compression="")
            recorder.start()

            pipeline = SensorPipeline(
                host="http://localhost:9999",
                public_key="test-pk",
                secret_key="test-sk",
                device_id="test-device",
            )
            pipeline.set_recorder(recorder)

            # Log some data through the pipeline
            pipeline.log("sensors/imu", Vector3(x=1.0, y=2.0, z=9.81))
            pipeline.log("sensors/battery", 87.5)

            assert recorder.message_count == 2

            pipeline.set_recorder(None)
            recorder.stop()
            pipeline.shutdown()

            assert os.path.exists(path)
            assert os.path.getsize(path) > 0
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_log_continues_if_recorder_write_fails(self):
        """Sensor pipeline should not crash if MCAP write fails."""
        from robotrace.sensor import SensorPipeline
        from robotrace.types import Scalar

        mock_recorder = MagicMock()
        mock_recorder.is_recording = True
        mock_recorder.write.side_effect = RuntimeError("disk full")

        pipeline = SensorPipeline(
            host="http://localhost:9999",
            public_key="test-pk",
            secret_key="test-sk",
            device_id="test-device",
        )
        try:
            pipeline.set_recorder(mock_recorder)

            # This should not raise even though recorder.write() fails
            pipeline.log("sensors/battery", Scalar(value=95.0))

            # Verify write was attempted
            mock_recorder.write.assert_called_once()
        finally:
            pipeline.set_recorder(None)
            pipeline.shutdown()
