"""RoboTrace client — OTel-native robot observability SDK."""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Optional

import httpx
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from . import _semconv as sc
from .models import Mission

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("robotrace-sdk")
except Exception:
    __version__ = "2.0.0"

from .processor import RoboTraceSpanProcessor
from .recorder import McapRecorder, mcap_available
from .sensor import SensorPipeline
from .timelines import get_timeline_context

logger = logging.getLogger("robotrace")

_instances: dict[str, "RoboTrace"] = {}
_instances_lock = threading.Lock()


# ---------------------------------------------------------------------------
# SDK Statistics — tracks export successes and failures
# ---------------------------------------------------------------------------

class SDKStats:
    """Thread-safe counters for SDK self-diagnostics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {
            "spans_exported": 0,
            "spans_dropped": 0,
            "scores_sent": 0,
            "scores_failed": 0,
            "sensor_samples_sent": 0,
            "sensor_samples_dropped": 0,
            "artifacts_uploaded": 0,
            "artifacts_failed": 0,
        }

    def record(self, category: str, count: int = 1) -> None:
        with self._lock:
            if category in self._counters:
                self._counters[category] += count

    def summary(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)


# ---------------------------------------------------------------------------
# Logging configuration from environment variables
# ---------------------------------------------------------------------------

def _configure_sdk_logging() -> None:
    """Configure the 'robotrace' logger based on environment variables.

    Environment variables:
        ROBOTRACE_DEBUG     — "true" enables debug mode (stderr handler + DEBUG level)
        ROBOTRACE_LOG_LEVEL — Override log level: DEBUG, INFO, WARNING, ERROR
        ROBOTRACE_LOG_FILE  — Write SDK logs to this file path
    """
    rt_logger = logging.getLogger("robotrace")

    debug = os.environ.get("ROBOTRACE_DEBUG", "").lower() in ("true", "1", "yes")
    log_level_str = os.environ.get("ROBOTRACE_LOG_LEVEL", "DEBUG" if debug else "WARNING")
    log_file = os.environ.get("ROBOTRACE_LOG_FILE")

    level = getattr(logging, log_level_str.upper(), logging.WARNING)
    rt_logger.setLevel(level)

    fmt = "%(asctime)s [robotrace] %(levelname)s %(name)s: %(message)s"

    # Debug mode: add stderr handler if none exists (besides NullHandler)
    if debug:
        has_real_handler = any(
            not isinstance(h, logging.NullHandler) for h in rt_logger.handlers
        )
        if not has_real_handler:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(fmt))
            rt_logger.addHandler(handler)

    # File-based logging: toggled by ROBOTRACE_LOG_FILE env var
    # Guard: only add if no FileHandler for this path already exists (prevents duplicates on reload)
    if log_file:
        has_file_handler = any(
            isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == os.path.abspath(log_file)
            for h in rt_logger.handlers
        )
        if not has_file_handler:
            try:
                file_handler = logging.FileHandler(log_file)
                file_handler.setFormatter(logging.Formatter(fmt))
                rt_logger.addHandler(file_handler)
            except OSError as e:
                rt_logger.warning("Failed to open log file %s: %s", log_file, e)


# Configure logging once at import time
_configure_sdk_logging()


class RoboTrace:
    """OTel-native robot observability client.

    Thread-safe singleton keyed by ``public_key:device_id``. Repeated
    construction with the same key+device returns the existing instance.

    Parameters
    ----------
    host : str
        RoboTrace server URL.
    public_key : str
        Project public key for authentication.
    secret_key : str
        Project secret key for authentication.
    device_id : str
        Unique robot identifier (ROSP URN recommended).
    environment : str
        Deployment environment (production, staging, sim).
    release : str | None
        Firmware/software version tag.
    sample_rate : float
        Fraction of missions to trace (0.0–1.0).
    enabled : bool
        Set False to disable all network I/O (dry-run mode).
    on_error : callable, optional
        Callback ``(context: str, exc: Exception) -> None`` invoked on
        telemetry or score delivery failures. Context is ``"telemetry"``
        or ``"scores"``.

        .. note:: RoboTrace is a singleton keyed by ``public_key``.
           ``on_error`` (and all other params) are only applied on the
           **first** construction. Subsequent calls with the same key
           return the existing instance and ignore new parameters.
    """

    def __new__(
        cls,
        host: str = "",
        public_key: str = "",
        secret_key: str = "",
        device_id: str = "",
        device_type: str = "",
        manufacturer: str = "",
        model: str = "",
        firmware_version: str = "",
        environment: str = "production",
        release: str | None = None,
        sample_rate: float = 1.0,
        enabled: bool = True,
        on_error: Any = None,
    ) -> "RoboTrace":
        # Singleton keyed by public_key:device_id — allows multiple devices
        # per project key (required for RoboTraceFleet / fleet gateway)
        instance_key = f"{public_key}:{device_id}" if device_id else public_key
        if instance_key:
            with _instances_lock:
                if instance_key in _instances:
                    return _instances[instance_key]
                instance = super().__new__(cls)
                _instances[instance_key] = instance
                return instance
        return super().__new__(cls)

    def __init__(
        self,
        host: str = "",
        public_key: str = "",
        secret_key: str = "",
        device_id: str = "",
        device_type: str = "",
        manufacturer: str = "",
        model: str = "",
        firmware_version: str = "",
        environment: str = "production",
        release: str | None = None,
        sample_rate: float = 1.0,
        enabled: bool = True,
        on_error: Any = None,
    ) -> None:
        with _instances_lock:
            if hasattr(self, "_initialized"):
                return
            self._initialized = True

        # Resolve host from env var if not provided
        if not host:
            host = os.environ.get("ROBOTRACE_HOST", "")
        if not host and enabled:
            logger.warning("RoboTrace: no host configured. Set host= parameter or ROBOTRACE_HOST env var.")
            enabled = False

        self._host = host.rstrip("/") if host else ""
        self._public_key = public_key
        self._secret_key = secret_key
        self._device_id = device_id
        self._device_type = device_type
        self._manufacturer = manufacturer
        self._model = model
        self._firmware_version = firmware_version
        self._environment = environment
        self._release = release
        self._sample_rate = sample_rate
        self._enabled = enabled
        self._on_error = on_error  # Callable[[str, Exception], None] | None

        resource_attrs: dict[str, Any] = {
            sc.SDK_NAME: "robotrace-python",
            sc.SDK_VERSION: __version__,
            sc.ENVIRONMENT: environment,
        }
        if device_id:
            resource_attrs[sc.ROBOT_ID] = device_id
        if device_type:
            resource_attrs[sc.ROBOT_TYPE] = device_type
        if manufacturer:
            resource_attrs[sc.ROBOT_MANUFACTURER] = manufacturer
        if model:
            resource_attrs[sc.ROBOT_MODEL] = model
        if firmware_version:
            resource_attrs[sc.ROBOT_FIRMWARE_VERSION] = firmware_version
        if release:
            resource_attrs[sc.RELEASE] = release

        resource = Resource.create(resource_attrs)

        self._provider = TracerProvider(resource=resource)
        if enabled and host:
            otlp_exporter = OTLPSpanExporter(
                endpoint=f"{self._host}/api/public/otel/v1/traces",
                headers={"Authorization": f"Basic {self._encode_auth()}"},
            )
            self._provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

            # RoboTraceSpanProcessor is available for offline/custom use but NOT
            # registered by default — the OTLP exporter above handles span export.
            # Registering both would cause duplicate events in ClickHouse.
            self._processor = None

            # Shared HTTP client with connection pooling (reuses TCP connections)
            self._http = httpx.Client(
                auth=(self._public_key, self._secret_key),
                timeout=10.0,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )

            self._sensor_pipeline = SensorPipeline(
                host=self._host,
                public_key=self._public_key,
                secret_key=self._secret_key,
                device_id=self._device_id,
                http_client=self._http,
                on_error=self._on_error,
            )

            # Register device with type/manufacturer/model/firmware on startup (non-blocking)
            if self._device_id and (self._device_type or self._manufacturer or self._model or self._firmware_version):
                threading.Thread(
                    target=self._register_device, daemon=True, name="robotrace-device-reg"
                ).start()
        else:
            self._processor = None
            self._sensor_pipeline = None
            self._http = None

        self._tracer = self._provider.get_tracer("robotrace", __version__)

        # MCAP recorder (set via start_recording / stop_recording)
        self._recorder: McapRecorder | None = None

        # SDK stats, score queue, and artifact queue (bounded to prevent OOM)
        self.stats = SDKStats()
        self._score_queue: deque[dict[str, Any]] = deque(maxlen=10000)
        self._score_lock = threading.Lock()
        self._score_flush_at = 10
        self._artifact_queue: deque[tuple[str, Any, str | None]] = deque(maxlen=1000)
        self._artifact_lock = threading.Lock()

        # Background thread for score + artifact flushing
        self._bg_shutdown = threading.Event()
        self._bg_thread = threading.Thread(
            target=self._background_flush_loop, daemon=True, name="robotrace-bg-flush"
        )
        self._bg_thread.start()

        atexit.register(self.shutdown)

        # Startup banner
        logger.info(
            "RoboTrace SDK initialized: host=%s device=%s env=%s enabled=%s version=%s",
            self._host, self._device_id, self._environment, self._enabled, __version__,
        )

    def _register_device(self) -> None:
        """Register device metadata with the server (runs in background thread)."""
        try:
            self._http.post(
                f"{self._host}/api/v1/devices/register",
                json={
                    "device_id": self._device_id,
                    "device_type": self._device_type or None,
                    "manufacturer": self._manufacturer or None,
                    "model": self._model or None,
                    "firmware_version": self._firmware_version or None,
                },
            )
            logger.debug("Device registered: %s (type=%s, mfr=%s, model=%s)", self._device_id, self._device_type, self._manufacturer, self._model)
        except Exception as e:
            logger.debug("Device registration skipped: %s", e)

    def _encode_auth(self) -> str:
        import base64
        credentials = f"{self._public_key}:{self._secret_key}"
        return base64.b64encode(credentials.encode()).decode()

    def auth_check(self) -> bool:
        """Verify connectivity and authentication with the RoboTrace server.

        Returns True if the server is reachable and credentials are valid.
        Use during setup/testing — not recommended in production hot paths.
        """
        try:
            if self._http:
                resp = self._http.get(f"{self._host}/health")
            else:
                with httpx.Client(timeout=5.0) as client:
                    resp = client.get(f"{self._host}/health")
            if resp.status_code == 200:
                logger.info("RoboTrace: server at %s is reachable (health OK)", self._host)
                return True
            logger.warning("RoboTrace: server at %s returned HTTP %d", self._host, resp.status_code)
            return False
        except Exception as e:
            logger.warning("RoboTrace: server at %s is unreachable: %s", self._host, e)
            return False

    def health(self) -> dict[str, Any]:
        """Return a snapshot of the SDK pipeline health.

        Returns a dict with:
        - ``buffered_count``: telemetry samples waiting in the outgoing batch
        - ``offline_count``: batches in the SQLite offline queue
        - ``offline_size_bytes``: total bytes in offline queue
        - ``stats``: dict of SDK counters (spans, scores, artifacts)
        - ``is_recording``: True if MCAP recording is active

        Usage::

            h = rt.health()
            if h["offline_count"] > 100:
                print("Warning: telemetry backlog growing")
        """
        if self._sensor_pipeline is not None:
            snapshot = self._sensor_pipeline.health_snapshot()
        else:
            snapshot = {"buffered_count": 0, "offline_count": 0, "offline_size_bytes": 0}

        return {
            **snapshot,
            "stats": self.stats.summary(),
            "is_recording": self.is_recording,
        }

    # ------------------------------------------------------------------
    # Timeline API (multi-clock support)
    # ------------------------------------------------------------------

    def set_time(
        self,
        name: str,
        *,
        sequence: int | None = None,
        timestamp: int | float | None = None,
    ) -> None:
        """Set a timeline value for subsequent log() calls (thread-local).

        Enables multi-rate sensor fusion by attaching additional temporal
        coordinates to telemetry samples.

        Parameters
        ----------
        name : str
            Timeline name (e.g., "sensor_clock", "frame_idx", "sim_time").
        sequence : int, optional
            Sequence/counter value (frame index, tick number).
        timestamp : int or float, optional
            Temporal value. int = nanoseconds, float = seconds.

        Example::

            rt.set_time("sensor_clock", timestamp=sensor_ns)
            rt.set_time("frame_idx", sequence=42)
            rt.log("sensors/imu", rt.Vector3(...))  # carries both timelines
        """
        ctx = get_timeline_context()
        ctx.set_time(name, sequence=sequence, timestamp=timestamp)
        logger.debug("Timeline set: %s=%s", name, sequence or timestamp)

    def reset_time(self) -> None:
        """Clear all custom timeline values. Auto-timelines (log_tick, log_time) continue."""
        ctx = get_timeline_context()
        ctx.reset()
        logger.debug("Timelines reset")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def command_channel(self) -> "CommandChannel":
        """Create a CommandChannel for receiving inbound commands.

        Returns a channel that, once started, connects to the server
        via WebSocket and dispatches commands to registered handlers.

        Requires the ``websockets`` package (``pip install robotrace-sdk[commands]``).

        Usage::

            cmd = rt.command_channel()

            @cmd.on("e_stop")
            def handle_estop(payload):
                robot.emergency_stop()

            cmd.start()
        """
        from .commands import CommandChannel
        return CommandChannel(
            host=self._host,
            public_key=self._public_key,
            secret_key=self._secret_key,
            device_id=self._device_id,
        )

    def component(
        self, component_id: str, type: str = "sensor", **kwargs
    ) -> "Component":
        """Register and return a Component for this device.

        Usage::

            lidar = rt.component("lidar-front", type="lidar", model="VLP-16")
            lidar.log(scan_data)
        """
        from robotrace.component import Component
        return Component(self, component_id, component_type=type, **kwargs)

    def mission(
        self,
        name: str,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> Mission:
        """Create a new mission (OTel trace root span).

        Use as a context manager or call `end()` manually.
        """
        logger.debug("Creating mission: name=%s tags=%s", name, tags)
        return Mission(
            name=name,
            tracer=self._tracer,
            client=self,
            metadata=metadata,
            tags=tags,
        )

    def log(
        self,
        path: str,
        data: Any,
        mission_id: str | None = None,
        artifact: bool = False,
        sensor_id: str | None = None,
    ) -> None:
        """Rerun-style semantic logging.

        Sensor data routes through the SensorPipeline. Artifacts are
        uploaded via presigned URL (placeholder — uploads in Phase 2).
        """
        try:
            if artifact:
                # Queue artifact for background upload (non-blocking)
                with self._artifact_lock:
                    self._artifact_queue.append((path, data, mission_id))
                logger.debug("Artifact queued: %s (queue_size=%d)", path, len(self._artifact_queue))
                return

            if self._sensor_pipeline is not None:
                self._sensor_pipeline.log(
                    stream=path,
                    value=data,
                    mission_id=mission_id,
                    sensor_id=sensor_id,
                )
            else:
                logger.debug("log() called but sensor pipeline not initialized (enabled=False)")
        except Exception as e:
            logger.warning("RoboTrace: log() failed for stream '%s': %s", path, e)
            self.stats.record("sensor_samples_dropped")

    def score(
        self,
        name: str,
        value: float | bool | str,
        mission_id: str | None = None,
        comment: str | None = None,
        source: str = "API",
    ) -> None:
        """Queue a score for async delivery (non-blocking)."""
        if not self._enabled:
            return

        string_value = None
        if isinstance(value, bool):
            score_value = 1.0 if value else 0.0
            data_type = "BOOLEAN"
        elif isinstance(value, (int, float)):
            score_value = float(value)
            data_type = "NUMERIC"
        elif isinstance(value, str):
            score_value = 0.0
            string_value = value
            data_type = "CATEGORICAL"
        else:
            score_value = float(value)
            data_type = "NUMERIC"

        payload: dict[str, Any] = {
            "name": name,
            "value": score_value,
            "data_type": data_type,
            "source": source,
        }
        if string_value is not None:
            payload["string_value"] = string_value
        if mission_id:
            payload["mission_id"] = mission_id
        if self._device_id:
            payload["device_id"] = self._device_id
        if comment:
            payload["comment"] = comment

        with self._score_lock:
            self._score_queue.append(payload)
            queue_len = len(self._score_queue)
            should_flush = queue_len >= self._score_flush_at

        logger.debug("Score queued: name=%s value=%s (queue_size=%d)", name, score_value, queue_len)

        if should_flush:
            self._flush_scores()

    def event(
        self,
        name: str,
        data: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> None:
        """Record a standalone point-in-time event."""
        try:
            span = self._tracer.start_span(name, attributes={
                "robotrace.type": "EVENT",
                sc.EVENT_NAME: name,
                sc.EVENT_SEVERITY: severity,
            })
            if data:
                span.set_attribute("robotrace.event.data", json.dumps(data, default=str))
            span.end()
        except Exception as e:
            logger.warning("RoboTrace: event('%s') failed: %s", name, e)

    def decision(
        self,
        name: str,
        model: str,
        input: dict[str, Any],
        output: dict[str, Any],
        confidence: float | None = None,
    ) -> None:
        """Record a standalone AI/algorithm decision."""
        try:
            attrs: dict[str, Any] = {
                "robotrace.type": "DECISION",
                sc.DECISION_MODEL: model,
                sc.DECISION_INPUT: json.dumps(input, default=str),
                sc.DECISION_OUTPUT: json.dumps(output, default=str),
            }
            if confidence is not None:
                attrs[sc.DECISION_CONFIDENCE] = confidence
            span = self._tracer.start_span(name, attributes=attrs)
            span.end()
        except Exception as e:
            logger.warning("RoboTrace: decision('%s') failed: %s", name, e)

    # ------------------------------------------------------------------
    # MCAP recording
    # ------------------------------------------------------------------

    def start_recording(self, path: str = "recording.mcap", compression: str = "zstd") -> None:
        """Start recording telemetry to an MCAP file.

        Data flows to BOTH the server (HTTP) AND the local file simultaneously.
        Requires the ``mcap`` optional dependency (``pip install robotrace-sdk[mcap]``).
        If ``mcap`` is not installed, logs a warning and returns without crashing.

        Parameters
        ----------
        path : str
            Output file path (default ``"recording.mcap"``).
        compression : str
            Compression: ``"zstd"`` (default), ``"lz4"``, or ``""`` (none).
        """
        if not mcap_available():
            logger.warning(
                "Cannot start MCAP recording: 'mcap' package not installed. "
                "Install with: pip install robotrace-sdk[mcap]"
            )
            return
        self._recorder = McapRecorder(path, compression)
        self._recorder.start()
        # Propagate recorder to sensor pipeline so it can dual-write
        if self._sensor_pipeline is not None:
            self._sensor_pipeline.set_recorder(self._recorder)
        logger.info("MCAP recording started: %s", path)

    def stop_recording(self) -> str:
        """Stop recording and finalize the MCAP file.

        Returns the file path of the completed recording, or ``""`` if
        no recording was active.
        """
        if self._recorder and self._recorder.is_recording:
            path = self._recorder.path
            self._recorder.stop()  # McapRecorder.stop() logs the message count
            self._recorder = None
            # Clear recorder from sensor pipeline
            if self._sensor_pipeline is not None:
                self._sensor_pipeline.set_recorder(None)
            return path
        return ""

    @property
    def is_recording(self) -> bool:
        """True if MCAP recording is active."""
        return self._recorder is not None and self._recorder.is_recording

    def flush(self) -> None:
        """Flush all pending data (OTel spans + sensor pipeline + scores)."""
        logger.debug("Flush requested: flushing OTel spans + sensor pipeline + scores")
        try:
            self._provider.force_flush()
            logger.debug("OTel spans flushed")
        except Exception as e:
            logger.warning("RoboTrace: OTel span flush failed: %s", e)
        if self._sensor_pipeline is not None:
            try:
                self._sensor_pipeline.flush()
            except Exception as e:
                logger.warning("RoboTrace: sensor pipeline flush failed: %s", e)
        self._flush_scores()

    def shutdown(self) -> None:
        """Gracefully shut down all pipelines and log diagnostics."""
        if getattr(self, "_shutdown_called", False):
            return
        self._shutdown_called = True

        # Stop MCAP recording if active
        if self._recorder and self._recorder.is_recording:
            try:
                self._recorder.stop()
                logger.info("MCAP recording finalized during shutdown")
            except Exception as e:
                logger.warning("RoboTrace: MCAP recording finalization failed: %s", e)
            self._recorder = None

        # Stop background thread and flush remaining data
        self._bg_shutdown.set()
        if self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5.0)
        self._flush_scores()
        self._process_artifact_queue()

        try:
            self._provider.shutdown()
        except Exception as e:
            logger.warning("RoboTrace: OTel shutdown failed: %s", e)
        if self._sensor_pipeline is not None:
            try:
                self._sensor_pipeline.shutdown()
            except Exception as e:
                logger.warning("RoboTrace: sensor pipeline shutdown failed: %s", e)

        # Close HTTP client
        if self._http:
            try:
                self._http.close()
            except Exception:
                pass

        instance_key = f"{self._public_key}:{self._device_id}" if self._device_id else self._public_key
        if instance_key:
            with _instances_lock:
                _instances.pop(instance_key, None)

        # Log shutdown summary with stats
        stats = self.stats.summary()
        drops = {k: v for k, v in stats.items() if ("dropped" in k or "failed" in k) and v > 0}
        if drops:
            logger.warning("RoboTrace shutdown — data loss detected: %s", drops)
        else:
            logger.info("RoboTrace shutdown complete — stats: %s", stats)

    def _flush_scores(self) -> None:
        """Send queued scores to the server using write-ahead pattern.

        1. Snapshot batch from in-memory queue
        2. Write batch to offline queue (durable on disk)
        3. Send to server
        4. On success: delete from offline queue
        5. On failure: batch stays on disk for retry

        This guarantees at-least-once delivery — scores survive crashes.
        """
        # Snapshot AND clear atomically to prevent duplicate flush from
        # concurrent calls (background thread + threshold trigger).
        with self._score_lock:
            if not self._score_queue:
                return
            batch = list(self._score_queue)
            self._score_queue.clear()

        if not self._http:
            return

        logger.debug("Flushing %d scores to server", len(batch))

        # Step 1: Write to offline queue for durability (write-ahead log)
        offline_queue = (
            self._sensor_pipeline._offline_queue
            if self._sensor_pipeline and self._sensor_pipeline._offline_queue
            else None
        )
        row_id = None
        if offline_queue:
            try:
                row_id = offline_queue.put(batch, batch_type="scores")
            except Exception as e:
                # WAL write failed — still attempt HTTP (WAL is best-effort)
                logger.debug("Offline queue write failed (proceeding to HTTP): %s", e)

        # Step 2: Try batch endpoint
        try:
            resp = self._http.post(
                f"{self._host}/api/v1/scores/batch",
                json={"batch": batch},
            )
            resp.raise_for_status()
            self.stats.record("scores_sent", len(batch))
            logger.debug("Score batch delivered: %d scores via batch endpoint", len(batch))
            # Step 3: Delete from offline queue on success
            if offline_queue and row_id is not None:
                try:
                    offline_queue.delete(row_id)
                except Exception:
                    pass  # Duplicate delivery is OK (server deduplicates by score_id)
            return
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                pass  # Batch endpoint not available, fall back to per-score
            else:
                logger.warning("RoboTrace: score batch delivery failed: %s", e)
                if self._on_error:
                    try:
                        self._on_error("scores", e)
                    except Exception:
                        pass
                return
        except Exception as e:
            logger.warning("RoboTrace: score batch network error: %s", e)
            if self._on_error:
                try:
                    self._on_error("scores", e)
                except Exception:
                    pass
            return

        # Fallback: per-score (only reached if batch endpoint returned 404)
        # Delete the WAL entry since we'll handle each score individually
        if offline_queue and row_id is not None:
            try:
                offline_queue.delete(row_id)
            except Exception:
                pass

        for payload in batch:
            try:
                resp = self._http.post(
                    f"{self._host}/api/v1/scores",
                    json=payload,
                )
                resp.raise_for_status()
                self.stats.record("scores_sent")
            except Exception as e:
                self.stats.record("scores_failed")
                logger.warning("RoboTrace: score '%s' delivery failed: %s", payload.get("name", "?"), e)
                if self._on_error:
                    try:
                        self._on_error("scores", e)
                    except Exception:
                        pass
                # Individual failed score → write to offline queue for retry
                if offline_queue:
                    try:
                        offline_queue.put([payload], batch_type="scores")
                    except Exception:
                        pass

    def _background_flush_loop(self) -> None:
        """Background thread: flushes scores, retries offline scores, uploads artifacts."""
        while not self._bg_shutdown.is_set():
            self._bg_shutdown.wait(timeout=2.0)
            try:
                self._flush_scores()
                self._retry_offline_scores()
                self._process_artifact_queue()
            except Exception as e:
                logger.debug("Background flush error: %s", e)

    def _retry_offline_scores(self) -> None:
        """Retry sending scores from the offline queue."""
        if not self._sensor_pipeline or not self._sensor_pipeline._offline_queue:
            return

        batches = self._sensor_pipeline._offline_queue.peek(limit=3, batch_type="scores")
        for row_id, batch_type, batch_data in batches:
            if not self._http:
                break
            try:
                for payload in batch_data:
                    resp = self._http.post(
                        f"{self._host}/api/v1/scores",
                        json=payload,
                    )
                    resp.raise_for_status()
                    self.stats.record("scores_sent")
                self._sensor_pipeline._offline_queue.delete(row_id)
                logger.debug("Offline score retry succeeded: batch %d", row_id)
            except Exception:
                self._sensor_pipeline._offline_queue.increment_retry(row_id)
                break

    def _process_artifact_queue(self) -> None:
        """Process queued artifact uploads (runs on background thread)."""
        while True:
            with self._artifact_lock:
                if not self._artifact_queue:
                    return
                path, data, mission_id = self._artifact_queue.popleft()
            try:
                self._upload_artifact(path, data, mission_id)
                self.stats.record("artifacts_uploaded")
            except Exception as e:
                self.stats.record("artifacts_failed")
                logger.warning("RoboTrace: artifact upload failed for '%s': %s", path, e)

    def _upload_artifact(self, path: str, data: Any, mission_id: str | None) -> None:
        """Upload artifact data via presigned S3 URL.

        Flow: 1) Get presigned URL from server  2) Upload bytes to S3
              3) Log the S3 URL as telemetry with artifact_url field

        Runs on the background thread (non-blocking for the caller).
        """
        if not self._http:
            return

        # Extract bytes from the data object
        if hasattr(data, "data"):
            # Image, DepthImage, PointCloud — have a .data attribute with raw bytes
            raw_bytes = data.data if isinstance(data.data, bytes) else str(data.data).encode()
            content_type = getattr(data, "format", "application/octet-stream")
            if content_type in ("jpeg", "png", "bmp"):
                content_type = f"image/{content_type}"
            elif content_type in ("mp4", "webm", "avi", "mkv"):
                content_type = f"video/{content_type}"
        elif isinstance(data, bytes):
            raw_bytes = data
            content_type = "application/octet-stream"
        else:
            raw_bytes = str(data).encode()
            content_type = "application/octet-stream"

        # Build a unique key from path and timestamp
        key = f"{path.replace('/', '_')}_{int(time.time() * 1000)}"

        # 1) Get presigned URL
        presign_resp = self._http.post(
            f"{self._host}/api/v1/artifacts/presign",
            json={"key": key, "content_type": content_type},
        )
        presign_resp.raise_for_status()
        presign_data = presign_resp.json()
        upload_url = presign_data["url"]
        s3_key = presign_data["key"]

        # 2) Upload to S3 via presigned URL (use a separate client for S3 — no auth header)
        upload_resp = httpx.put(
            upload_url,
            content=raw_bytes,
            headers={"Content-Type": content_type},
            timeout=30.0,
        )
        upload_resp.raise_for_status()

        # 3) Log the artifact URL as telemetry
        if self._sensor_pipeline is not None:
            self._sensor_pipeline.log(
                stream=path,
                value=data,
                mission_id=mission_id,
            )

        logger.info("Artifact uploaded: %s (%d bytes) → %s", path, len(raw_bytes), s3_key)

    # ------------------------------------------------------------------
    # Stream configuration
    # ------------------------------------------------------------------

    def configure_stream(
        self,
        stream: str,
        config: dict[str, Any],
    ) -> None:
        """Declare stream metadata (units, ranges, thresholds) to the server.

        Called once per stream at startup. The server stores the config and
        the dashboard uses it for rendering + alerts.

        Parameters
        ----------
        stream : str
            Stream path (e.g., "sensors/battery", "robot/pose")
        config : dict
            Stream configuration:
            - display_name: str -- human-readable name
            - data_type: str -- RoboTrace type name (battery, laser_scan, etc.)
            - unit: str -- primary unit (%, V, m/s, etc.)
            - fields: dict -- per-field metadata (unit, scale, range, thresholds)
            - rate_hz: float -- expected data rate

        Example::

            rt.configure_stream("sensors/battery", {
                "display_name": "Main Battery",
                "data_type": "battery",
                "fields": {
                    "soc": {"unit": "ratio", "scale": 100, "display_unit": "%"},
                    "voltage": {"unit": "V", "range": [10.0, 14.8]},
                    "temperature": {"unit": "C", "warning_above": 60},
                },
                "rate_hz": 1,
            })
        """
        if not self._enabled or not self._http:
            return
        try:
            payload = {
                "device_id": self._device_id,
                "stream": stream,
                **config,
            }
            self._http.post(
                f"{self._host}/api/v1/streams/configure",
                json=payload,
            )
            logger.debug("Stream configured: %s", stream)
        except Exception as e:
            logger.debug("Stream config failed (non-fatal): %s", e)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "RoboTrace":
        return self

    def __exit__(self, *_: Any) -> None:
        self.shutdown()

    # ------------------------------------------------------------------
    # Convenience accessors for types (Rerun-style rt.Pose3D(...))
    # ------------------------------------------------------------------


    def __repr__(self) -> str:
        return (
            f"RoboTrace(host={self._host!r}, device_id={self._device_id!r}, "
            f"environment={self._environment!r}, enabled={self._enabled})"
        )
