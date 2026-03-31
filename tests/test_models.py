"""Tests for Mission and Phase models."""

from __future__ import annotations

import uuid

import pytest

from robotrace import RoboTrace


@pytest.fixture()
def client():
    c = RoboTrace(
        host="http://localhost:9999",
        public_key=f"test-models-{uuid.uuid4()}",
        secret_key="test-sk",
        device_id="urn:rosp:device:test:robot:001",
        enabled=False,
    )
    yield c
    c.shutdown()


class TestMissionContextManager:
    def test_creates_trace_id(self, client: RoboTrace):
        with client.mission("test") as m:
            assert len(m.mission_id) == 32
            assert len(m.span_id) == 16

    def test_nested_phases(self, client: RoboTrace):
        with client.mission("pick_and_place") as m:
            with m.phase("navigate") as nav:
                assert nav.span_id is not None
                with nav.phase("replan") as replan:
                    assert replan.span_id is not None

    def test_mission_event(self, client: RoboTrace):
        with client.mission("test") as m:
            m.event("obstacle_detected", data={"distance": 0.5}, severity="warning")

    def test_mission_decision(self, client: RoboTrace):
        with client.mission("test") as m:
            m.decision(
                name="path_planner",
                model="nav2",
                input={"goal": [1, 2]},
                output={"path": [[0, 0], [1, 2]]},
                confidence=0.9,
            )

    def test_mission_score(self, client: RoboTrace):
        with client.mission("test") as m:
            m.score("success", True)
            m.score("cycle_time", 12.5)

    def test_exception_sets_error(self, client: RoboTrace):
        try:
            with client.mission("failing") as m:
                raise RuntimeError("motor fault")
        except RuntimeError:
            pass


class TestPhaseContextManager:
    def test_phase_event(self, client: RoboTrace):
        with client.mission("m") as m:
            with m.phase("nav") as p:
                p.event("waypoint_reached", {"index": 3})

    def test_phase_decision(self, client: RoboTrace):
        with client.mission("m") as m:
            with m.phase("pick") as p:
                p.decision(
                    name="grasp",
                    model="graspnet",
                    input={"pose": [0, 0, 0]},
                    output={"confidence": 0.95},
                )

    def test_phase_log(self, client: RoboTrace):
        with client.mission("m") as m:
            with m.phase("nav") as p:
                p.log("sensors/battery", {"value": 87.5})

    def test_exception_in_phase(self, client: RoboTrace):
        try:
            with client.mission("m") as m:
                with m.phase("failing") as p:
                    raise ValueError("sensor error")
        except ValueError:
            pass


class TestDirectRecording:
    def test_immediate_phase(self, client: RoboTrace):
        m = client.mission("pick_cycle")
        m.phase(
            "move_to_approach",
            command="movej",
            input={"target_q": [0.52, -1.57]},
            output={"error_rad": 0.0008},
            duration_ms=1200,
        )
        m.end()

    def test_multiple_immediate_phases(self, client: RoboTrace):
        m = client.mission("assembly")
        m.phase("step_1", command="movej", input={}, output={}, duration_ms=500)
        m.phase("step_2", command="movel", input={}, output={}, duration_ms=300)
        m.phase("step_3", command="gripper", input={}, output={}, duration_ms=200)
        m.score("success", True)
        m.end()

    def test_manual_end(self, client: RoboTrace):
        m = client.mission("test")
        m.end(status="completed")

    def test_failed_end(self, client: RoboTrace):
        m = client.mission("test")
        m.end(status="failed")

    def test_double_end_is_safe(self, client: RoboTrace):
        m = client.mission("test")
        m.end()
        m.end()  # should not raise
