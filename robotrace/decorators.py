"""@mission() and @phase() decorators for automatic tracing.

Modeled on Langfuse's @observe() — wraps functions in OTel spans with
automatic input/output capture and context propagation.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from typing import Any, Callable, TypeVar

from opentelemetry import context as otel_context
from opentelemetry import trace

from . import _semconv as sc

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("robotrace-sdk")
except Exception:
    __version__ = "2.0.0"

logger = logging.getLogger("robotrace.decorators")

F = TypeVar("F", bound=Callable[..., Any])


def mission(
    name: str | None = None,
    tags: list[str] | None = None,
    capture_input: bool = True,
    capture_output: bool = True,
) -> Callable[[F], F]:
    """Decorator that wraps a function in a mission span (OTel trace root).

    Usage::

        @mission(name="deliver_pallet")
        def deliver(destination: str):
            ...
    """

    def decorator(fn: F) -> F:
        span_name = name or fn.__qualname__

        def _build_attrs(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
            attrs: dict[str, Any] = {
                "robotrace.type": "MISSION",
                sc.MISSION_NAME: span_name,
            }
            if tags:
                attrs[sc.MISSION_TAGS] = tags
            if capture_input:
                attrs[sc.DECISION_INPUT] = _serialize_args(fn, args, kwargs)
            return attrs

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = trace.get_tracer("robotrace", __version__)
                attrs = _build_attrs(fn, args, kwargs)

                span = tracer.start_span(span_name, attributes=attrs)
                ctx = trace.set_span_in_context(span)
                token = otel_context.attach(ctx)

                try:
                    result = await fn(*args, **kwargs)
                    if capture_output and result is not None:
                        span.set_attribute(sc.DECISION_OUTPUT, json.dumps(result, default=str))
                    span.set_attribute(sc.MISSION_STATUS, "completed")
                    span.set_status(trace.StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(trace.StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    span.set_attribute(sc.MISSION_STATUS, "failed")
                    raise
                finally:
                    span.end()
                    otel_context.detach(token)

            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = trace.get_tracer("robotrace", __version__)
                attrs = _build_attrs(fn, args, kwargs)

                span = tracer.start_span(span_name, attributes=attrs)
                ctx = trace.set_span_in_context(span)
                token = otel_context.attach(ctx)

                try:
                    result = fn(*args, **kwargs)
                    if capture_output and result is not None:
                        span.set_attribute(sc.DECISION_OUTPUT, json.dumps(result, default=str))
                    span.set_attribute(sc.MISSION_STATUS, "completed")
                    span.set_status(trace.StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(trace.StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    span.set_attribute(sc.MISSION_STATUS, "failed")
                    raise
                finally:
                    span.end()
                    otel_context.detach(token)

            return wrapper  # type: ignore[return-value]

    return decorator


def phase(
    name: str | None = None,
    capture_input: bool = True,
    capture_output: bool = True,
) -> Callable[[F], F]:
    """Decorator that wraps a function in a phase span (OTel child span).

    Automatically nests under the current active span via OTel context
    propagation.

    Usage::

        @phase()
        def navigate_to(destination: str):
            ...
    """

    def decorator(fn: F) -> F:
        span_name = name or fn.__qualname__

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = trace.get_tracer("robotrace", __version__)
                attrs: dict[str, Any] = {"robotrace.type": "PHASE"}
                if capture_input:
                    attrs[sc.DECISION_INPUT] = _serialize_args(fn, args, kwargs)

                with tracer.start_as_current_span(span_name, attributes=attrs) as span:
                    try:
                        result = await fn(*args, **kwargs)
                        if capture_output and result is not None:
                            span.set_attribute(sc.DECISION_OUTPUT, json.dumps(result, default=str))
                        span.set_status(trace.StatusCode.OK)
                        return result
                    except Exception as exc:
                        span.set_status(trace.StatusCode.ERROR, str(exc))
                        span.record_exception(exc)
                        raise

            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = trace.get_tracer("robotrace", __version__)
                attrs: dict[str, Any] = {"robotrace.type": "PHASE"}
                if capture_input:
                    attrs[sc.DECISION_INPUT] = _serialize_args(fn, args, kwargs)

                with tracer.start_as_current_span(span_name, attributes=attrs) as span:
                    try:
                        result = fn(*args, **kwargs)
                        if capture_output and result is not None:
                            span.set_attribute(sc.DECISION_OUTPUT, json.dumps(result, default=str))
                        span.set_status(trace.StatusCode.OK)
                        return result
                    except Exception as exc:
                        span.set_status(trace.StatusCode.ERROR, str(exc))
                        span.record_exception(exc)
                        raise

            return wrapper  # type: ignore[return-value]

    return decorator


def _serialize_args(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Best-effort serialization of function arguments."""
    try:
        import inspect
        sig = inspect.signature(fn)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        return json.dumps(dict(bound.arguments), default=str)
    except Exception:
        return json.dumps({"args": str(args), "kwargs": str(kwargs)})
