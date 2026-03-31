"""Sensor pipeline for high-frequency telemetry data.

This is separate from OTel — sensor data flows through a custom batch HTTP
pipeline (gRPC streaming planned for Phase 2).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from .offline_queue import OfflineQueue
from .timelines import get_timeline_context

logger = logging.getLogger("robotrace.sensor")


class SensorPipeline:
    """Buffers and batches high-frequency sensor data for upload.

    Each named stream gets its own ring buffer. Data is flushed in batches
    to the RoboTrace server via HTTP POST.
    """

    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        device_id: str = "",
        ring_buffer_size: int = 10000,
        flush_interval: float = 1.0,
        flush_at: int = 1000,
        http_client: Any = None,
        on_error: Any = None,
    ) -> None:
        self._host = host.rstrip("/")
        self._public_key = public_key
        self._secret_key = secret_key
        self._device_id = device_id
        self._http = http_client  # Shared connection-pooled httpx.Client
        self._on_error = on_error  # Callable[[str, Exception], None] | None
        self._ring_buffer_size = ring_buffer_size
        self._flush_interval = flush_interval
        self._flush_at = flush_at

        self._buffers: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=ring_buffer_size)
        )
        self._batch: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()

        # Disk-persistent offline queue (survives crashes and power cycles)
        self._offline_queue = OfflineQueue()
        try:
            self._offline_queue.open()
        except Exception as e:
            logger.warning("Failed to open offline queue: %s (falling back to memory-only)", e)
            self._offline_queue = None

        # MCAP recorder (set by client when recording is active)
        self._recorder: Any = None

        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="robotrace-sensor-flush"
        )
        self._flush_thread.start()

    def set_recorder(self, recorder: Any) -> None:
        """Set (or clear) the MCAP recorder for dual-output recording."""
        with self._lock:
            self._recorder = recorder

    def log(
        self,
        stream: str,
        value: Any,
        timestamp: float | None = None,
        mission_id: str | None = None,
        phase_id: str | None = None,
        sensor_id: str | None = None,
    ) -> None:
        """Record a sensor sample.

        The value is stored in the ring buffer for the given stream and
        added to the outgoing batch.
        """
        ts = timestamp if timestamp is not None else time.time()

        # Attach timeline values from thread-local context
        timeline_tags = get_timeline_context().snapshot()

        sample: dict[str, Any] = {
            "device_id": self._device_id,
            "stream": stream,
            "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "tags": timeline_tags,
        }
        if mission_id:
            sample["mission_id"] = mission_id
        if phase_id:
            sample["phase_id"] = phase_id
        if sensor_id:
            sample["sensor_id"] = sensor_id

        # Map data type to correct TelemetryPoint field
        # Note: to_dict() creates new dicts, json.dumps snapshots at call time.
        # List values are copied to prevent mutation between log() and flush.
        if hasattr(value, "to_dict"):
            data = value.to_dict()
            if "value" in data and isinstance(data["value"], (int, float)):
                sample["value_float"] = float(data["value"])
            elif "values" in data and isinstance(data["values"], list):
                sample["value_array"] = list(data["values"])  # defensive copy
            else:
                import json as _json
                sample["value_json"] = _json.dumps(data)
        elif isinstance(value, (int, float)):
            sample["value_float"] = float(value)
        elif isinstance(value, dict):
            import json as _json
            sample["value_json"] = _json.dumps(value)
        else:
            import json as _json
            sample["value_json"] = _json.dumps({"value": str(value)})

        # Write to MCAP file if recording (dual-output: HTTP + local file)
        recorder = self._recorder
        if recorder is not None and recorder.is_recording:
            # Build the data dict for MCAP (raw typed data, not the HTTP sample)
            if hasattr(value, "to_dict"):
                mcap_data = value.to_dict()
            elif isinstance(value, dict):
                mcap_data = value
            else:
                mcap_data = {"value": value}
            try:
                recorder.write(stream, mcap_data, timestamp_ns=int(ts * 1e9))
            except Exception as e:
                logger.debug("MCAP write failed for stream '%s': %s", stream, e)

        with self._lock:
            self._buffers[stream].append(sample)
            self._batch.append(sample)
            # Never flush on the caller's thread — the background thread handles it.
            # This prevents surprise blocking in the robot's control loop.

    def health_snapshot(self) -> dict[str, int]:
        """Return pipeline health metrics under the batch lock."""
        with self._lock:
            buffered = len(self._batch)
        offline_count = 0
        offline_bytes = 0
        if self._offline_queue:
            offline_count = self._offline_queue.qsize()
            offline_bytes = self._offline_queue.total_size_bytes()
        return {
            "buffered_count": buffered,
            "offline_count": offline_count,
            "offline_size_bytes": offline_bytes,
        }

    def flush(self) -> None:
        self._do_flush()

    def shutdown(self) -> None:
        self._shutdown_event.set()
        self._do_flush()
        if self._flush_thread.is_alive():
            self._flush_thread.join(timeout=5.0)
        if self._offline_queue:
            self._offline_queue.close()

    def get_buffer(self, stream: str) -> list[dict[str, Any]]:
        """Return a snapshot of the ring buffer for a given stream."""
        with self._lock:
            return list(self._buffers.get(stream, []))

    def _do_flush(self) -> None:
        with self._lock:
            if not self._batch:
                return
            to_send = list(self._batch)
            self._batch.clear()

        logger.debug("Flushing %d telemetry samples to server", len(to_send))
        try:
            url = f"{self._host}/api/v1/telemetry"
            if self._http:
                response = self._http.post(url, json={"batch": to_send})
            else:
                import httpx as _httpx
                response = _httpx.post(
                    url, json={"batch": to_send},
                    auth=(self._public_key, self._secret_key), timeout=10.0,
                )
            response.raise_for_status()
            logger.debug("Telemetry flush delivered: %d samples (HTTP %d)", len(to_send), response.status_code)
        except Exception as e:
            # Don't retry client errors (4xx) — the payload is bad
            is_client_error = hasattr(e, 'response') and hasattr(e.response, 'status_code') and 400 <= e.response.status_code < 500
            if is_client_error:
                logger.warning("RoboTrace: sensor flush rejected (4xx, not retrying): %s", e)
            else:
                logger.warning("RoboTrace: sensor flush failed (%d samples queued offline): %s", len(to_send), e)
                if self._offline_queue:
                    self._offline_queue.put(to_send, batch_type="telemetry")
                else:
                    logger.debug("No offline queue — %d samples dropped", len(to_send))
            if self._on_error:
                try:
                    self._on_error("telemetry", e)
                except Exception:
                    pass

    def _flush_loop(self) -> None:
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(timeout=self._flush_interval)
            try:
                self._do_flush()
                self._retry_offline()
            except Exception as e:
                logger.debug("Error in sensor flush loop: %s", e)

    def _retry_offline(self) -> None:
        """Attempt to send queued offline batches (up to 5 per cycle)."""
        if not self._offline_queue:
            return

        # Periodic eviction (once per cycle)
        self._offline_queue.evict_expired()

        batches = self._offline_queue.peek(limit=5, batch_type="telemetry")
        for row_id, batch_type, batch_data in batches:
            try:
                self._send_batch(batch_data)
                self._offline_queue.delete(row_id)
                logger.debug("Offline retry succeeded: batch %d (%d samples)", row_id, len(batch_data))
            except Exception:
                self._offline_queue.increment_retry(row_id)
                break  # Stop retrying on first failure

    def _send_batch(self, batch: list[dict[str, Any]]) -> None:
        """Send a batch of samples to the server."""
        url = f"{self._host}/api/v1/telemetry"
        if self._http:
            response = self._http.post(url, json={"batch": batch})
        else:
            import httpx as _httpx
            response = _httpx.post(
                url, json={"batch": batch},
                auth=(self._public_key, self._secret_key), timeout=10.0,
            )
        response.raise_for_status()
