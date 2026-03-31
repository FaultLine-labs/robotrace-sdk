"""Tests for @mission() and @phase() decorators."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from robotrace.decorators import mission, phase

# Single global provider for all decorator tests (OTel only allows set once)
_exporter = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_exporter))
trace.set_tracer_provider(_provider)


def _clear() -> None:
    _exporter.clear()


class TestMissionDecorator:
    def test_captures_return_value(self):
        _clear()

        @mission(name="test_mission")
        def my_mission() -> dict:
            return {"status": "done"}

        result = my_mission()
        assert result == {"status": "done"}

        _provider.force_flush()
        spans = _exporter.get_finished_spans()
        assert len(spans) >= 1
        mission_span = next(s for s in spans if s.name == "test_mission")
        assert mission_span.name == "test_mission"

    def test_uses_function_name_as_default(self):
        _clear()

        @mission()
        def deliver_pallet():
            return None

        deliver_pallet()
        _provider.force_flush()
        spans = _exporter.get_finished_spans()
        assert any("deliver_pallet" in s.name for s in spans)

    def test_captures_exception(self):
        _clear()

        @mission(name="failing")
        def fail():
            raise ValueError("robot error")

        try:
            fail()
        except ValueError:
            pass

        _provider.force_flush()
        spans = _exporter.get_finished_spans()
        failing_span = next(s for s in spans if s.name == "failing")
        assert failing_span.status.status_code.name == "ERROR"

    def test_capture_input_false(self):
        _clear()

        @mission(capture_input=False)
        def secret_mission(password: str):
            return "ok"

        secret_mission("s3cret")
        _provider.force_flush()
        spans = _exporter.get_finished_spans()
        # Find the span for this function
        sm_span = next(s for s in spans if "secret_mission" in s.name)
        attrs = dict(sm_span.attributes)
        assert "robotrace.decision.input" not in attrs


class TestPhaseDecorator:
    def test_nesting(self):
        _clear()

        @mission(name="outer")
        def outer():
            return inner()

        @phase(name="inner_phase")
        def inner():
            return 42

        result = outer()
        assert result == 42

        _provider.force_flush()
        spans = _exporter.get_finished_spans()
        names = {s.name for s in spans}
        assert "outer" in names
        assert "inner_phase" in names

        inner_span = next(s for s in spans if s.name == "inner_phase")
        outer_span = next(s for s in spans if s.name == "outer")
        assert inner_span.parent is not None
        assert inner_span.parent.span_id == outer_span.context.span_id

    def test_capture_output(self):
        _clear()

        @phase(name="compute")
        def compute():
            return {"result": 42}

        @mission(name="m_wrapper")
        def wrapper():
            return compute()

        wrapper()
        _provider.force_flush()
        spans = _exporter.get_finished_spans()
        compute_span = next(s for s in spans if s.name == "compute")
        attrs = dict(compute_span.attributes)
        assert "robotrace.decision.output" in attrs
