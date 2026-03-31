"""Tests for Component — hardware component telemetry attribution."""

from unittest.mock import MagicMock, call

import pytest

from robotrace.component import Component


class MockRoboTrace:
    """Mock RoboTrace client for Component tests."""

    def __init__(self):
        self.logged = []
        self.scored = []
        self._http = None  # No HTTP — prevents _register from calling server
        self._host = "http://fake"
        self._device_id = "test-device"
        self._sensor_pipeline = None

    def log(self, path, data, **kwargs):
        self.logged.append((path, data, kwargs))

    def score(self, name, value, **kwargs):
        self.scored.append((name, value, kwargs))


class TestComponent:
    """Tests for Component class."""

    def test_construction_stores_component_id(self):
        client = MockRoboTrace()
        comp = Component(client, "lidar-front", component_type="lidar")
        assert comp.id == "lidar-front"

    def test_construction_stores_type(self):
        client = MockRoboTrace()
        comp = Component(client, "imu-001", component_type="imu")
        assert comp.type == "imu"

    def test_construction_stores_kwargs(self):
        client = MockRoboTrace()
        comp = Component(
            client, "lidar-front",
            component_type="lidar",
            manufacturer="Velodyne",
            model="VLP-16",
            serial_number="SN-12345",
            firmware_version="1.2.3",
        )
        assert comp.manufacturer == "Velodyne"
        assert comp.model == "VLP-16"
        assert comp.serial_number == "SN-12345"
        assert comp.firmware_version == "1.2.3"

    def test_construction_default_type_is_sensor(self):
        client = MockRoboTrace()
        comp = Component(client, "temp-sensor")
        assert comp.type == "sensor"

    def test_log_calls_client_log_with_sensor_id(self):
        client = MockRoboTrace()
        comp = Component(client, "lidar-front", component_type="lidar")

        comp.log({"ranges": [1.0, 2.0, 3.0]})

        assert len(client.logged) == 1
        path, data, kwargs = client.logged[0]
        assert path == "lidar-front"
        assert data == {"ranges": [1.0, 2.0, 3.0]}
        assert kwargs.get("sensor_id") == "lidar-front"

    def test_log_multiple_values(self):
        client = MockRoboTrace()
        comp = Component(client, "imu")

        comp.log({"accel": [0, 0, 9.8]})
        comp.log({"gyro": [0.1, 0.2, 0.3]})

        assert len(client.logged) == 2

    def test_score_calls_client_score(self):
        client = MockRoboTrace()
        comp = Component(client, "battery-monitor")

        comp.score("soc_accuracy", 0.95)

        assert len(client.scored) == 1
        name, value, kwargs = client.scored[0]
        assert name == "soc_accuracy"
        assert value == 0.95

    def test_score_passes_kwargs(self):
        client = MockRoboTrace()
        comp = Component(client, "nav-system")

        comp.score("path_efficiency", 0.87, mission_id="m-001", comment="good path")

        assert len(client.scored) == 1
        _, _, kwargs = client.scored[0]
        assert kwargs["mission_id"] == "m-001"
        assert kwargs["comment"] == "good path"

    def test_id_property_returns_component_id(self):
        client = MockRoboTrace()
        comp = Component(client, "motor-left")
        assert comp.id == "motor-left"

    def test_name_stored(self):
        client = MockRoboTrace()
        comp = Component(client, "cam-front", name="Front Camera")
        assert comp.name == "Front Camera"

    def test_register_skipped_when_no_http(self):
        """Registration should not crash when client has no HTTP."""
        client = MockRoboTrace()
        # This should not raise
        comp = Component(client, "test-comp")
        assert comp.id == "test-comp"

    def test_register_skipped_when_no_device_id(self):
        client = MockRoboTrace()
        client._device_id = ""
        # Should not raise
        comp = Component(client, "test-comp")
        assert comp.id == "test-comp"

    def test_log_with_timestamp_uses_sensor_pipeline_directly(self):
        """When timestamp is provided and sensor_pipeline exists, log goes directly to pipeline."""
        client = MockRoboTrace()
        mock_pipeline = MagicMock()
        client._sensor_pipeline = mock_pipeline

        comp = Component(client, "imu-001")
        comp.log({"accel": [0, 0, 9.8]}, timestamp=1234567890.0)

        mock_pipeline.log.assert_called_once_with(
            stream="imu-001",
            value={"accel": [0, 0, 9.8]},
            timestamp=1234567890.0,
            sensor_id="imu-001",
        )
        # Should NOT have called client.log
        assert len(client.logged) == 0

    def test_log_without_timestamp_uses_client_log(self):
        """When no timestamp, log goes through client.log."""
        client = MockRoboTrace()
        comp = Component(client, "sensor-1")
        comp.log(42.0)

        assert len(client.logged) == 1
        assert client.logged[0][0] == "sensor-1"
        assert client.logged[0][1] == 42.0
