"""MCAP file recording for RoboTrace telemetry.

Records telemetry data to MCAP files (the standard robotics recording format)
alongside the HTTP upload pipeline. The ``mcap`` package is an optional
dependency — if not installed, ``start_recording()`` logs a warning and
all write calls become no-ops.
"""

from __future__ import annotations

import atexit
import json
import logging
import threading
import time
from typing import Any

logger = logging.getLogger("robotrace.recorder")

# ---------------------------------------------------------------------------
# Conditional mcap import — graceful fallback when not installed
# ---------------------------------------------------------------------------

try:
    from mcap.writer import Writer as McapWriter, CompressionType as _CompressionType

    _MCAP_AVAILABLE = True

    _COMPRESSION_MAP: dict[str, _CompressionType] = {
        "zstd": _CompressionType.ZSTD,
        "lz4": _CompressionType.LZ4,
        "none": _CompressionType.NONE,
        "": _CompressionType.NONE,
    }
except ImportError:  # pragma: no cover
    McapWriter = None  # type: ignore[assignment,misc]
    _CompressionType = None  # type: ignore[assignment,misc]
    _COMPRESSION_MAP = {}  # type: ignore[assignment]
    _MCAP_AVAILABLE = False


def mcap_available() -> bool:
    """Return True if the ``mcap`` package is installed."""
    return _MCAP_AVAILABLE


# ---------------------------------------------------------------------------
# JSON schemas for each RoboTrace type (used as MCAP channel schemas)
# ---------------------------------------------------------------------------

_TYPE_SCHEMAS: dict[str, dict[str, Any]] = {
    "scalar": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "value": {"type": "number"},
        },
    },
    "vector3": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "z": {"type": "number"},
        },
    },
    "vectorn": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "values": {"type": "array", "items": {"type": "number"}},
        },
    },
    "pose3d": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "z": {"type": "number"},
            "qx": {"type": "number"},
            "qy": {"type": "number"},
            "qz": {"type": "number"},
            "qw": {"type": "number"},
        },
    },
    "transform3d": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "translation": {"type": "object"},
            "rotation": {"type": "array", "items": {"type": "number"}},
        },
    },
    "pointcloud": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            "colors": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
        },
    },
    "image": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "data": {"type": "string"},
            "size_bytes": {"type": "integer"},
            "format": {"type": "string"},
        },
    },
    "depth_image": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "data": {"type": "string"},
            "size_bytes": {"type": "integer"},
        },
    },
    "laser_scan": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "ranges": {"type": "array", "items": {"type": "number"}},
            "angle_min": {"type": "number"},
            "angle_max": {"type": "number"},
            "angle_increment": {"type": "number"},
        },
    },
    "joint_state": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "names": {"type": "array", "items": {"type": "string"}},
            "positions": {"type": "array", "items": {"type": "number"}},
            "velocities": {"type": "array", "items": {"type": "number"}},
            "efforts": {"type": "array", "items": {"type": "number"}},
        },
    },
    "numeric_set": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "values": {"type": "object"},
        },
    },
    "bbox2d": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "w": {"type": "number"},
            "h": {"type": "number"},
            "label": {"type": "string"},
        },
    },
    "bbox3d": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "center": {"type": "object"},
            "size": {"type": "object"},
            "label": {"type": "string"},
        },
    },
    "geolocation": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "latitude": {"type": "number"},
            "longitude": {"type": "number"},
            "altitude": {"type": "number"},
            "fix_type": {"type": "string"},
            "heading": {"type": "number"},
            "speed": {"type": "number"},
            "accuracy": {"type": "number"},
            "altitude_accuracy": {"type": "number"},
        },
    },
    "path": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            "frame_id": {"type": "string"},
            "num_points": {"type": "integer"},
        },
    },
    "twist": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "linear_x": {"type": "number"},
            "linear_y": {"type": "number"},
            "linear_z": {"type": "number"},
            "angular_x": {"type": "number"},
            "angular_y": {"type": "number"},
            "angular_z": {"type": "number"},
        },
    },
    "log": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "message": {"type": "string"},
            "level": {"type": "string"},
        },
    },
    "battery": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "voltage": {"type": "number"},
            "current": {"type": "number"},
            "soc": {"type": "number"},
            "health": {"type": "number"},
            "temperature": {"type": "number"},
            "cell_voltages": {"type": "array", "items": {"type": "number"}},
        },
    },
    "bitset": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "value": {"type": ["integer", "string"]},
            "width": {"type": "integer"},
            "labels": {"type": "object"},
            "active": {"type": "array", "items": {"type": "string"}},
        },
    },
    "video": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "size_bytes": {"type": "integer"},
            "format": {"type": "string"},
            "duration_s": {"type": "number"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
        },
    },
}

# Fallback schema for unknown types
_DEFAULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}


def _schema_for(type_name: str) -> dict[str, Any]:
    """Return the JSON schema for a given RoboTrace type name."""
    return _TYPE_SCHEMAS.get(type_name, _DEFAULT_SCHEMA)


# ---------------------------------------------------------------------------
# McapRecorder
# ---------------------------------------------------------------------------


class McapRecorder:
    """Records telemetry to an MCAP file alongside HTTP upload.

    Thread-safe: multiple threads (e.g. the sensor pipeline flush thread)
    can call :meth:`write` concurrently.

    Usage::

        recorder = McapRecorder("output.mcap")
        recorder.start()
        recorder.write("sensors/imu", {"type": "vector3", "x": 1, "y": 2, "z": 3})
        recorder.stop()

    Parameters
    ----------
    path : str
        Output file path for the MCAP recording.
    compression : str
        Compression algorithm: ``"zstd"`` (default), ``"lz4"``, or ``""`` (none).
    """

    def __init__(self, path: str, compression: str = "zstd") -> None:
        self._path = path
        self._compression = compression
        self._writer: Any | None = None
        self._file: Any | None = None
        self._lock = threading.Lock()
        self._recording = False
        self._message_count = 0

        # Lazily-registered channels: stream name -> channel_id
        self._channels: dict[str, int] = {}
        # Schema cache: schema name -> schema_id
        self._schemas: dict[str, int] = {}

    def start(self) -> None:
        """Open the MCAP file and begin recording.

        Raises a warning and returns silently if ``mcap`` is not installed.
        """
        if not _MCAP_AVAILABLE:
            logger.warning(
                "Cannot start MCAP recording: 'mcap' package not installed. "
                "Install with: pip install robotrace-sdk[mcap]"
            )
            return

        with self._lock:
            if self._recording:
                logger.warning("MCAP recording already in progress: %s", self._path)
                return

            compression_type = _COMPRESSION_MAP.get(self._compression.lower())
            if compression_type is None:
                logger.warning("Unknown MCAP compression %r — falling back to ZSTD", self._compression)
                compression_type = _CompressionType.ZSTD

            try:
                from pathlib import Path
                Path(self._path).parent.mkdir(parents=True, exist_ok=True)
                self._file = open(self._path, "wb")
                self._writer = McapWriter(output=self._file, compression=compression_type)
                self._writer.start(profile="", library="robotrace-sdk")
            except Exception:
                # Clean up file handle on failure to prevent leaks
                if self._file is not None:
                    self._file.close()
                    self._file = None
                self._writer = None
                raise

            self._recording = True
            self._message_count = 0
            self._channels.clear()
            self._schemas.clear()

        # Register atexit handler only once (unregister first to avoid accumulation)
        atexit.unregister(self._atexit_finalize)
        atexit.register(self._atexit_finalize)
        logger.info("MCAP recording started: %s (compression=%s)", self._path, self._compression)

    def write(
        self,
        stream: str,
        data: dict[str, Any],
        timestamp_ns: int | None = None,
    ) -> None:
        """Write a message to the MCAP file.

        Parameters
        ----------
        stream : str
            Topic/channel name (e.g. ``"sensors/imu"``).
        data : dict
            Serializable data dict (typically from ``value.to_dict()``).
        timestamp_ns : int, optional
            Timestamp in nanoseconds. Defaults to current wall-clock time.
        """
        if not self._recording:
            return

        ts = timestamp_ns if timestamp_ns is not None else time.time_ns()

        with self._lock:
            if not self._recording or self._writer is None:
                return

            # Lazily register schema + channel on first use
            channel_id = self._channels.get(stream)
            if channel_id is None:
                channel_id = self._register_channel(stream, data)

            self._writer.add_message(
                channel_id=channel_id,
                log_time=ts,
                data=json.dumps(data, default=str).encode("utf-8"),
                publish_time=ts,
            )
            self._message_count += 1

    def stop(self) -> None:
        """Finalize the MCAP file and close the file handle."""
        with self._lock:
            if not self._recording:
                return
            self._recording = False

            if self._writer is not None:
                try:
                    self._writer.finish()
                except Exception as e:
                    logger.warning("Error finalizing MCAP writer: %s", e)
                self._writer = None

            if self._file is not None:
                try:
                    self._file.close()
                except Exception as e:
                    logger.warning("Error closing MCAP file: %s", e)
                self._file = None

        # Unregister atexit handler (file already finalized)
        try:
            atexit.unregister(self._atexit_finalize)
        except Exception:
            pass

        logger.info(
            "MCAP recording stopped: %s (%d messages)",
            self._path,
            self._message_count,
        )

    @property
    def is_recording(self) -> bool:
        """True if the recorder is actively recording."""
        return self._recording

    @property
    def path(self) -> str:
        """Output file path."""
        return self._path

    @property
    def message_count(self) -> int:
        """Number of messages written so far."""
        return self._message_count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _register_channel(self, stream: str, data: dict[str, Any]) -> int:
        """Register a new MCAP schema + channel for the given stream.

        Must be called while holding ``self._lock``.
        """
        # Determine the type name from the data dict
        type_name = data.get("type", "unknown")
        schema_name = f"robotrace.{type_name}"

        # Register or reuse schema
        schema_id = self._schemas.get(schema_name)
        if schema_id is None:
            schema_dict = _schema_for(type_name)
            schema_bytes = json.dumps(schema_dict).encode("utf-8")
            schema_id = self._writer.register_schema(
                name=schema_name,
                encoding="jsonschema",
                data=schema_bytes,
            )
            self._schemas[schema_name] = schema_id

        # Register channel
        channel_id = self._writer.register_channel(
            topic=stream,
            message_encoding="json",
            schema_id=schema_id,
        )
        self._channels[stream] = channel_id
        logger.debug(
            "MCAP channel registered: %s (schema=%s, channel_id=%d)",
            stream,
            schema_name,
            channel_id,
        )
        return channel_id

    def _atexit_finalize(self) -> None:
        """Safety net: finalize the MCAP file if the process exits unexpectedly."""
        if self._recording:
            logger.info("Finalizing MCAP recording on process exit: %s", self._path)
            self.stop()
