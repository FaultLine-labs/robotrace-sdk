"""Tests for the RoboTrace span processor."""

from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from robotrace.processor import RoboTraceSpanProcessor, _serialize_attr


class TestSpanToEvent:
    def test_converts_span(self):
        proc = RoboTraceSpanProcessor(
            host="http://localhost:9999",
            public_key="pk",
            secret_key="sk",
            flush_interval=60.0,
        )

        provider = TracerProvider()
        provider.add_span_processor(proc)

        tracer = provider.get_tracer("test")
        span = tracer.start_span("test_span", attributes={"key": "value"})
        span.end()

        # The span should be in the processor queue
        assert len(proc._queue) >= 1
        event = proc._queue[0]
        assert event["name"] == "test_span"
        # Non-robotrace attributes go into metadata (flattened, no nested "attributes" key)
        assert event["metadata"]["key"] == "value"
        assert event["type"] == "PHASE"  # default type
        assert event["device_id"] == ""  # no resource configured

        proc.shutdown()
        provider.shutdown()

    def test_parent_id_set(self):
        proc = RoboTraceSpanProcessor(
            host="http://localhost:9999",
            public_key="pk",
            secret_key="sk",
            flush_interval=60.0,
        )

        provider = TracerProvider()
        provider.add_span_processor(proc)

        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("parent"):
            child = tracer.start_span("child")
            child.end()

        events = list(proc._queue)
        child_event = next(e for e in events if e["name"] == "child")
        assert "parent_id" in child_event

        proc.shutdown()
        provider.shutdown()


class TestBatching:
    def test_flush_at_threshold(self):
        proc = RoboTraceSpanProcessor(
            host="http://localhost:9999",
            public_key="pk",
            secret_key="sk",
            flush_at=2,
            flush_interval=60.0,
        )

        provider = TracerProvider()
        provider.add_span_processor(proc)
        tracer = provider.get_tracer("test")

        import httpx as httpx_mod
        with patch.object(httpx_mod, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            # Add 2 spans to trigger flush
            s1 = tracer.start_span("s1")
            s1.end()
            s2 = tracer.start_span("s2")
            s2.end()

            # Give flush thread a moment, then force flush
            proc.force_flush()

            assert mock_post.called, "Expected HTTP POST to be triggered at flush_at threshold"
            call_args = mock_post.call_args
            assert "/api/v1/ingest" in call_args[0][0]

        proc.shutdown()
        provider.shutdown()

    def test_force_flush(self):
        proc = RoboTraceSpanProcessor(
            host="http://localhost:9999",
            public_key="pk",
            secret_key="sk",
            flush_interval=60.0,
        )

        result = proc.force_flush()
        assert result is True
        proc.shutdown()


class TestSerializeAttr:
    def test_primitives(self):
        assert _serialize_attr("hello") == "hello"
        assert _serialize_attr(42) == 42
        assert _serialize_attr(3.14) == 3.14
        assert _serialize_attr(True) is True

    def test_list(self):
        assert _serialize_attr([1, "a", 3.0]) == [1, "a", 3.0]

    def test_complex_fallback(self):
        result = _serialize_attr({"key": "val"})
        assert isinstance(result, str)


class TestShutdown:
    def test_shutdown_idempotent(self):
        proc = RoboTraceSpanProcessor(
            host="http://localhost:9999",
            public_key="pk",
            secret_key="sk",
            flush_interval=60.0,
        )
        proc.shutdown()
        proc.shutdown()  # should not raise
