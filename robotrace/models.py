"""Mission, Phase, Event, and Decision models.

These wrap OTel spans and provide the user-facing context-manager API
for structured mission tracing.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional, TYPE_CHECKING

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from . import _semconv as sc

if TYPE_CHECKING:
    from .client import RoboTrace

logger = logging.getLogger("robotrace.models")


class Phase:
    """A timed sub-task within a mission (maps to an OTel child span).

    Can be used as a context manager or created/closed manually via the
    direct recording pattern.
    """

    def __init__(
        self,
        name: str,
        tracer: trace.Tracer,
        client: RoboTrace,
        mission_id: str,
        parent_context: otel_context.Context | None = None,
        *,
        command: str | None = None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._name = name
        self._tracer = tracer
        self._client = client
        self._mission_id = mission_id
        self._span: trace.Span | None = None
        self._token: object | None = None
        self._ctx: otel_context.Context | None = None
        self._parent_context = parent_context
        self._closed = False
        logger.debug("Phase created: name=%s mission_id=%s", name, mission_id)

        attrs: dict[str, Any] = {"robotrace.type": "PHASE"}
        if command:
            attrs[sc.PHASE_COMMAND] = command
        if input is not None:
            attrs[sc.DECISION_INPUT] = json.dumps(input, default=str)
        if output is not None:
            attrs[sc.DECISION_OUTPUT] = json.dumps(output, default=str)
        if metadata:
            for k, v in metadata.items():
                attrs[f"robotrace.metadata.{k}"] = str(v)

        if duration_ms is not None:
            self._create_immediate(name, attrs, input, output, duration_ms)
        else:
            ctx = self._parent_context or otel_context.get_current()
            self._span = self._tracer.start_span(name, context=ctx, attributes=attrs)
            self._ctx = trace.set_span_in_context(self._span, ctx)

    def _create_immediate(
        self,
        name: str,
        attrs: dict[str, Any],
        input: dict[str, Any] | None,
        output: dict[str, Any] | None,
        duration_ms: float,
    ) -> None:
        """Create a span that is immediately started and ended (direct recording)."""
        ctx = self._parent_context or otel_context.get_current()
        end_ns = time.time_ns()
        start_ns = end_ns - int(duration_ms * 1_000_000)

        span = self._tracer.start_span(name, context=ctx, attributes=attrs, start_time=start_ns)
        self._span = span  # Store before ending so span_id works
        self._ctx = trace.set_span_in_context(span, ctx)  # Set context so nested phases inherit parent
        span.set_status(StatusCode.OK)
        span.end(end_time=end_ns)
        self._closed = True

    @property
    def span_id(self) -> str | None:
        if self._span is None:
            return None
        return format(self._span.get_span_context().span_id, "016x")

    def log(self, path: str, data: Any, artifact: bool = False) -> None:
        self._client.log(path, data, mission_id=self._mission_id, artifact=artifact)

    def event(self, name: str, data: dict[str, Any] | None = None, severity: str = "info") -> None:
        if self._span is None:
            return
        attrs: dict[str, str] = {sc.EVENT_SEVERITY: severity}
        if data:
            attrs["robotrace.event.data"] = json.dumps(data, default=str)
        self._span.add_event(name, attributes=attrs)
        logger.debug("Event: %s (severity=%s) on phase %s", name, severity, self._name)

    def decision(
        self,
        name: str,
        model: str,
        input: dict[str, Any],
        output: dict[str, Any],
        confidence: float | None = None,
    ) -> None:
        if self._span is None:
            return
        attrs: dict[str, Any] = {
            "robotrace.type": "DECISION",
            sc.DECISION_MODEL: model,
            sc.DECISION_INPUT: json.dumps(input, default=str),
            sc.DECISION_OUTPUT: json.dumps(output, default=str),
        }
        if confidence is not None:
            attrs[sc.DECISION_CONFIDENCE] = confidence
        self._span.add_event(name, attributes=attrs)

    def phase(
        self,
        name: str,
        *,
        command: str | None = None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Phase:
        """Create a nested child phase."""
        ctx = self._ctx
        return Phase(
            name,
            self._tracer,
            self._client,
            self._mission_id,
            parent_context=ctx,
            command=command,
            input=input,
            output=output,
            duration_ms=duration_ms,
            metadata=metadata,
        )

    def end(self, status: str = "completed") -> None:
        if self._closed or self._span is None:
            return
        self._closed = True
        logger.debug("Phase ended: name=%s status=%s", self._name, status)
        if status == "completed":
            self._span.set_status(StatusCode.OK)
        else:
            self._span.set_status(StatusCode.ERROR, status)
        self._span.end()
        if self._token is not None:
            otel_context.detach(self._token)

    def __enter__(self) -> Phase:
        if self._span is not None and not self._closed:
            self._token = otel_context.attach(self._ctx)
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if exc_type is not None and self._span is not None:
            self._span.set_status(StatusCode.ERROR, str(exc_val))
            self._span.record_exception(exc_val)
        self.end("failed" if exc_type else "completed")


class Mission:
    """A complete robot task from assignment to completion (maps to an OTel trace).

    Can be used as a context manager or manually ended via `end()`.
    """

    def __init__(
        self,
        name: str,
        tracer: trace.Tracer,
        client: RoboTrace,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self._name = name
        self._tracer = tracer
        self._client = client
        self._closed = False
        self._token: object | None = None

        attrs: dict[str, Any] = {
            "robotrace.type": "MISSION",
            sc.MISSION_NAME: name,
        }
        if tags:
            attrs[sc.MISSION_TAGS] = tags
        if metadata:
            for k, v in metadata.items():
                attrs[f"robotrace.metadata.{k}"] = str(v)

        self._span = self._tracer.start_span(name, attributes=attrs)
        self._ctx = trace.set_span_in_context(self._span)
        logger.debug("Mission started: name=%s trace_id=%s tags=%s", name, self.mission_id, tags)

    @property
    def mission_id(self) -> str:
        return format(self._span.get_span_context().trace_id, "032x")

    @property
    def span_id(self) -> str:
        return format(self._span.get_span_context().span_id, "016x")

    def phase(
        self,
        name: str,
        *,
        command: str | None = None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Phase:
        return Phase(
            name,
            self._tracer,
            self._client,
            self.mission_id,
            parent_context=self._ctx,
            command=command,
            input=input,
            output=output,
            duration_ms=duration_ms,
            metadata=metadata,
        )

    def event(self, name: str, data: dict[str, Any] | None = None, severity: str = "info") -> None:
        attrs: dict[str, str] = {sc.EVENT_SEVERITY: severity}
        if data:
            attrs["robotrace.event.data"] = json.dumps(data, default=str)
        self._span.add_event(name, attributes=attrs)

    def decision(
        self,
        name: str,
        model: str,
        input: dict[str, Any],
        output: dict[str, Any],
        confidence: float | None = None,
    ) -> None:
        attrs: dict[str, Any] = {
            "robotrace.type": "DECISION",
            sc.DECISION_MODEL: model,
            sc.DECISION_INPUT: json.dumps(input, default=str),
            sc.DECISION_OUTPUT: json.dumps(output, default=str),
        }
        if confidence is not None:
            attrs[sc.DECISION_CONFIDENCE] = confidence
        self._span.add_event(name, attributes=attrs)

    def score(
        self,
        name: str,
        value: float | bool | str,
        comment: str | None = None,
        source: str = "API",
    ) -> None:
        self._client.score(
            name, value, mission_id=self.mission_id, comment=comment, source=source
        )

    def log(self, path: str, data: Any, artifact: bool = False) -> None:
        self._client.log(path, data, mission_id=self.mission_id, artifact=artifact)

    def end(self, status: str = "completed") -> None:
        if self._closed:
            return
        self._closed = True
        logger.debug("Mission ended: name=%s status=%s", self._name, status)
        self._span.set_attribute(sc.MISSION_STATUS, status)
        if status == "completed":
            self._span.set_status(StatusCode.OK)
        else:
            self._span.set_status(StatusCode.ERROR, status)
        self._span.end()
        if self._token is not None:
            otel_context.detach(self._token)

    def __enter__(self) -> Mission:
        self._token = otel_context.attach(self._ctx)
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if exc_type is not None:
            self._span.set_status(StatusCode.ERROR, str(exc_val))
            self._span.record_exception(exc_val)
        self.end("failed" if exc_type else "completed")
