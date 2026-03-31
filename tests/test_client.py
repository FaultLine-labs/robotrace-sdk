"""Tests for the RoboTrace v2 client."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from robotrace import RoboTrace


@pytest.fixture()
def client():
    """Return a disabled RoboTrace client (no network I/O)."""
    # Use unique public_key per test to avoid singleton caching
    import uuid
    c = RoboTrace(
        host="http://localhost:9999",
        public_key=f"test-pk-{uuid.uuid4()}",
        secret_key="test-sk",
        device_id="urn:rosp:device:test:robot:001",
        enabled=False,
    )
    yield c
    c.shutdown()


class TestInitialization:
    def test_creates_client(self, client: RoboTrace):
        assert client._host == "http://localhost:9999"
        assert client._device_id == "urn:rosp:device:test:robot:001"
        assert client._enabled is False

    def test_defaults(self):
        import uuid
        c = RoboTrace(public_key=f"test-{uuid.uuid4()}", enabled=False)
        assert c._host == ""
        assert c._environment == "production"
        assert c._sample_rate == 1.0
        c.shutdown()

    def test_context_manager(self):
        import uuid
        with RoboTrace(public_key=f"test-{uuid.uuid4()}", enabled=False) as c:
            assert isinstance(c, RoboTrace)

    def test_repr(self, client: RoboTrace):
        r = repr(client)
        assert "localhost:9999" in r
        assert "test:robot:001" in r


class TestSingleton:
    def test_same_key_returns_same_instance(self):
        key = "singleton-test-key-1"
        try:
            c1 = RoboTrace(public_key=key, enabled=False)
            c2 = RoboTrace(public_key=key, enabled=False)
            assert c1 is c2
        finally:
            c1.shutdown()

    def test_different_key_returns_different_instance(self):
        try:
            c1 = RoboTrace(public_key="key-a", enabled=False)
            c2 = RoboTrace(public_key="key-b", enabled=False)
            assert c1 is not c2
        finally:
            c1.shutdown()
            c2.shutdown()

    def test_shutdown_removes_from_cache(self):
        key = "singleton-test-key-2"
        c1 = RoboTrace(public_key=key, enabled=False)
        c1.shutdown()
        c2 = RoboTrace(public_key=key, enabled=False)
        assert c1 is not c2
        c2.shutdown()


class TestMission:
    def test_creates_mission(self, client: RoboTrace):
        m = client.mission("test_mission")
        assert m.mission_id is not None
        assert len(m.mission_id) == 32  # hex trace id
        m.end()

    def test_mission_context_manager(self, client: RoboTrace):
        with client.mission("test_mission") as m:
            assert m.mission_id is not None

    def test_mission_with_metadata(self, client: RoboTrace):
        with client.mission("test", metadata={"zone": "A"}, tags=["priority-high"]) as m:
            pass


class TestLog:
    def test_log_disabled_client(self, client: RoboTrace):
        # Should not raise even with no sensor pipeline
        client.log("sensors/battery", 87.5)

    def test_log_with_mission(self, client: RoboTrace):
        with client.mission("m") as m:
            m.log("sensors/temp", {"value": 42.0})


class TestEvent:
    def test_event(self, client: RoboTrace):
        client.event("e_stop", data={"reason": "obstacle"}, severity="warning")


class TestDecision:
    def test_decision(self, client: RoboTrace):
        client.decision(
            name="path_planner",
            model="nav2",
            input={"goal": [1, 2]},
            output={"path_length": 5.0},
            confidence=0.95,
        )


class TestFlushShutdown:
    def test_flush_disabled(self, client: RoboTrace):
        client.flush()  # should not raise

    def test_shutdown(self, client: RoboTrace):
        client.shutdown()  # should not raise
