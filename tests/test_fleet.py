"""Tests for RoboTraceFleet — fleet gateway for multiple robots."""

import threading
from unittest.mock import MagicMock, patch, call

import pytest


class TestRoboTraceFleet:
    """Tests for RoboTraceFleet class."""

    def _make_fleet(self, device_ids=None, **kwargs):
        """Create a fleet with enabled=False to avoid HTTP calls."""
        from robotrace.fleet import RoboTraceFleet

        with patch("robotrace.fleet.RoboTrace") as MockRT:
            # Each call to RoboTrace() returns a fresh mock
            MockRT.side_effect = lambda **kw: MagicMock(
                _device_id=kw.get("device_id", ""),
                _host=kw.get("host", ""),
            )
            fleet = RoboTraceFleet(
                host="http://fake:8080",
                public_key="pk-test",
                secret_key="sk-test",
                device_ids=device_ids,
                enabled=False,
                **kwargs,
            )
        return fleet, MockRT

    def test_fleet_creation_with_multiple_devices(self):
        fleet, _ = self._make_fleet(device_ids=["amr-001", "amr-002", "amr-003"])
        assert fleet.device_count == 3
        assert set(fleet.device_ids) == {"amr-001", "amr-002", "amr-003"}

    def test_fleet_creation_empty(self):
        fleet, _ = self._make_fleet(device_ids=None)
        assert fleet.device_count == 0
        assert fleet.device_ids == []

    def test_device_returns_correct_client(self):
        fleet, _ = self._make_fleet(device_ids=["amr-001", "amr-002"])
        client1 = fleet.device("amr-001")
        client2 = fleet.device("amr-002")
        assert client1 is not client2
        # Each client should be a mock (not None)
        assert client1 is not None
        assert client2 is not None

    def test_device_raises_keyerror_for_unknown(self):
        fleet, _ = self._make_fleet(device_ids=["amr-001"])
        with pytest.raises(KeyError, match="not-registered"):
            fleet.device("not-registered")

    def test_register_device_adds_new(self):
        fleet, MockRT = self._make_fleet(device_ids=["amr-001"])
        assert fleet.device_count == 1

        with patch("robotrace.fleet.RoboTrace") as MockRT2:
            MockRT2.return_value = MagicMock()
            new_client = fleet.register_device("amr-002")

        assert fleet.device_count == 2
        assert "amr-002" in fleet.device_ids
        assert new_client is not None

    def test_register_device_returns_existing_if_already_registered(self):
        fleet, _ = self._make_fleet(device_ids=["amr-001"])
        existing = fleet.device("amr-001")
        returned = fleet.register_device("amr-001")
        assert returned is existing

    def test_remove_device(self):
        fleet, _ = self._make_fleet(device_ids=["amr-001", "amr-002"])
        assert fleet.device_count == 2
        client = fleet.device("amr-001")

        fleet.remove_device("amr-001")

        assert fleet.device_count == 1
        assert "amr-001" not in fleet.device_ids
        client.shutdown.assert_called_once()

    def test_remove_device_nonexistent_is_noop(self):
        fleet, _ = self._make_fleet(device_ids=["amr-001"])
        fleet.remove_device("does-not-exist")  # Should not raise
        assert fleet.device_count == 1

    def test_device_count_property(self):
        fleet, _ = self._make_fleet(device_ids=["a", "b", "c"])
        assert fleet.device_count == 3

    def test_device_ids_property(self):
        fleet, _ = self._make_fleet(device_ids=["x", "y"])
        ids = fleet.device_ids
        assert isinstance(ids, list)
        assert set(ids) == {"x", "y"}

    def test_broadcast_log_calls_all_devices(self):
        fleet, _ = self._make_fleet(device_ids=["d1", "d2", "d3"])
        fleet.broadcast_log("fleet/heartbeat", 1.0)

        for did in ["d1", "d2", "d3"]:
            fleet._clients[did].log.assert_called_once_with("fleet/heartbeat", 1.0)

    def test_broadcast_event_calls_all_devices(self):
        fleet, _ = self._make_fleet(device_ids=["d1", "d2"])
        fleet.broadcast_event("alert", {"msg": "low battery"}, severity="warning")

        for did in ["d1", "d2"]:
            fleet._clients[did].event.assert_called_once_with(
                "alert", {"msg": "low battery"}, "warning"
            )

    def test_broadcast_event_error_isolation(self):
        """One device failing should not stop others from receiving the event."""
        fleet, _ = self._make_fleet(device_ids=["d1", "d2", "d3"])
        # Make d1 raise an exception
        fleet._clients["d1"].event.side_effect = RuntimeError("device offline")

        fleet.broadcast_event("test_event")

        # d1 raised, but d2 and d3 should still have been called
        fleet._clients["d2"].event.assert_called_once()
        fleet._clients["d3"].event.assert_called_once()

    def test_broadcast_log_error_isolation(self):
        fleet, _ = self._make_fleet(device_ids=["d1", "d2"])
        fleet._clients["d1"].log.side_effect = RuntimeError("fail")

        fleet.broadcast_log("path", 42)

        fleet._clients["d2"].log.assert_called_once()

    def test_health_all_returns_dict_keyed_by_device_id(self):
        fleet, _ = self._make_fleet(device_ids=["d1", "d2"])
        fleet._clients["d1"].health.return_value = {"buffered_count": 5}
        fleet._clients["d2"].health.return_value = {"buffered_count": 10}

        result = fleet.health_all()

        assert "d1" in result
        assert "d2" in result
        assert result["d1"]["buffered_count"] == 5
        assert result["d2"]["buffered_count"] == 10

    def test_flush_all_calls_flush_on_all(self):
        fleet, _ = self._make_fleet(device_ids=["d1", "d2"])
        fleet.flush_all()

        fleet._clients["d1"].flush.assert_called_once()
        fleet._clients["d2"].flush.assert_called_once()

    def test_shutdown_all_clears_clients(self):
        fleet, _ = self._make_fleet(device_ids=["d1", "d2"])
        fleet.shutdown_all()

        assert fleet.device_count == 0
        assert fleet.device_ids == []

    def test_shutdown_all_calls_shutdown_on_each(self):
        fleet, _ = self._make_fleet(device_ids=["d1", "d2"])
        c1 = fleet._clients["d1"]
        c2 = fleet._clients["d2"]

        fleet.shutdown_all()

        c1.shutdown.assert_called_once()
        c2.shutdown.assert_called_once()

    def test_context_manager_calls_shutdown_all(self):
        fleet, _ = self._make_fleet(device_ids=["d1"])
        c1 = fleet._clients["d1"]

        with fleet:
            pass

        c1.shutdown.assert_called_once()
        assert fleet.device_count == 0

    def test_thread_safety_concurrent_register_and_broadcast(self):
        """Simulate concurrent register and broadcast operations."""
        fleet, _ = self._make_fleet(device_ids=["d1"])
        errors = []

        def register_devices():
            for i in range(20):
                try:
                    with patch("robotrace.fleet.RoboTrace") as MockRT:
                        MockRT.return_value = MagicMock()
                        fleet.register_device(f"thread-dev-{i}")
                except Exception as e:
                    errors.append(e)

        def broadcast_logs():
            for _ in range(20):
                try:
                    fleet.broadcast_log("test/path", 1.0)
                except Exception as e:
                    errors.append(e)

        t1 = threading.Thread(target=register_devices)
        t2 = threading.Thread(target=broadcast_logs)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert errors == [], f"Thread safety errors: {errors}"

    def test_repr(self):
        fleet, _ = self._make_fleet(device_ids=["d1", "d2"])
        r = repr(fleet)
        assert "RoboTraceFleet" in r
        assert "http://fake:8080" in r
