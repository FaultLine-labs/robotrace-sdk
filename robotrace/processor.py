"""RoboTrace span processor — converts OTel spans to RoboTrace events and batches them."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

logger = logging.getLogger("robotrace.processor")


class RoboTraceSpanProcessor(SpanProcessor):
    """Batches completed OTel spans and flushes them to the RoboTrace server.

    Spans are queued in memory and flushed when either `flush_at` events
    accumulate or `flush_interval` seconds have elapsed.
    """

    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        flush_at: int = 512,
        flush_interval: float = 5.0,
        http_client: Any = None,
    ) -> None:
        self._host = host.rstrip("/")
        self._public_key = public_key
        self._secret_key = secret_key
        self._http = http_client  # Shared connection-pooled httpx.Client
        self._flush_at = flush_at
        self._flush_interval = flush_interval

        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._flushing = False
        self._shutdown_event = threading.Event()

        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="robotrace-processor-flush"
        )
        self._flush_thread.start()

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        pass

    def on_end(self, span: ReadableSpan) -> None:
        try:
            event = self._span_to_event(span)
            with self._lock:
                self._queue.append(event)
                should_flush = len(self._queue) >= self._flush_at
            if should_flush:
                self._do_flush()
        except Exception as e:
            logger.warning("RoboTrace: failed to process span '%s': %s", span.name if span else "?", e)

    def shutdown(self) -> None:
        self._shutdown_event.set()
        self._do_flush()
        if self._flush_thread.is_alive():
            self._flush_thread.join(timeout=5.0)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self._do_flush()
        return True

    def _span_to_event(self, span: ReadableSpan) -> dict[str, Any]:
        """Convert an OTel span to a flat dict matching IngestionEvent schema."""
        ctx = span.get_span_context()
        parent = span.parent
        attrs = dict(span.attributes) if span.attributes else {}
        resource_attrs = dict(span.resource.attributes) if span.resource and span.resource.attributes else {}

        # Map OTel status to RoboTrace status
        status_map = {"UNSET": "completed", "OK": "completed", "ERROR": "failed"}
        status = status_map.get(span.status.status_code.name, "completed") if span.status else "completed"

        # Extract robotrace.* attributes and flatten to IngestionEvent fields
        tags_raw = attrs.get("robotrace.mission.tags")
        mission_tags = list(tags_raw) if isinstance(tags_raw, (list, tuple)) else []

        event: dict[str, Any] = {
            "event_id": format(ctx.span_id, "016x"),
            "trace_id": format(ctx.trace_id, "032x"),
            "name": span.name,
            "type": str(attrs.get("robotrace.type", "PHASE")),
            "status": status,
            "start_time": _ns_to_iso(span.start_time),
            "end_time": _ns_to_iso(span.end_time) if span.end_time else None,
            "device_id": str(resource_attrs.get("robot.id", "")),
            "shift_id": _str_or_none(attrs.get("robotrace.shift.id")),
            "mission_name": _str_or_none(attrs.get("robotrace.mission.name")),
            "mission_tags": mission_tags,
            "environment": str(resource_attrs.get("robotrace.environment", attrs.get("robotrace.environment", "production"))),
            "release": _str_or_none(resource_attrs.get("robotrace.release", attrs.get("robotrace.release"))),
            "model": _str_or_none(attrs.get("robotrace.decision.model")),
            "model_params": {},
            "input": str(attrs.get("robotrace.decision.input", "")),
            "output": str(attrs.get("robotrace.decision.output", "")),
            "metadata": {k: _serialize_attr(v) for k, v in attrs.items() if not k.startswith("robotrace.")},
            "level": str(attrs.get("robotrace.event.severity", "DEFAULT")).upper(),
            "status_message": _str_or_none(attrs.get("robotrace.status_message")),
        }

        if parent is not None:
            event["parent_id"] = format(parent.span_id, "016x")

        return event

    def _do_flush(self) -> None:
        with self._lock:
            if not self._queue or self._flushing:
                return
            self._flushing = True
            batch = list(self._queue)
            self._queue.clear()

        try:
            url = f"{self._host}/api/v1/ingest"
            if self._http:
                response = self._http.post(url, json={"batch": batch})
            else:
                import httpx
                response = httpx.post(
                    url,
                    json={"batch": batch},
                    auth=(self._public_key, self._secret_key),
                    timeout=10.0,
                )
            response.raise_for_status()
        except Exception as e:
            logger.warning("RoboTrace: failed to flush %d events (will retry): %s", len(batch), e)
            with self._lock:
                self._queue.extendleft(reversed(batch))
        finally:
            with self._lock:
                self._flushing = False

    def _flush_loop(self) -> None:
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(timeout=self._flush_interval)
            try:
                self._do_flush()
            except Exception as e:
                logger.debug("Error in processor flush loop: %s", e)


def _str_or_none(v: Any) -> str | None:
    """Convert to string or None."""
    return str(v) if v is not None else None


def _ns_to_iso(ns: int) -> str:
    """Convert nanosecond Unix timestamp to ISO 8601 string."""
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()


def _serialize_attr(value: Any) -> Any:
    """Ensure attribute values are JSON-serializable."""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_serialize_attr(v) for v in value]
    return str(value)
