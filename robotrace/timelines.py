"""Multi-timeline support for sensor data — Rerun-style temporal context.

Robots have multiple clocks (sensor hardware, GPS, sim clock, wall clock).
This module provides thread-local timeline context that attaches to every
rt.log() call, enabling multi-rate sensor fusion and temporal correlation.

Usage::

    rt.set_time("sensor_clock", timestamp=sensor_ns)
    rt.set_time("frame_idx", sequence=42)
    rt.log("sensors/imu", rt.Vector3(...))  # carries both timelines

    rt.reset_time()  # clear custom timelines
"""

from __future__ import annotations

import threading
import time
from typing import Any


class TimelineContext(threading.local):
    """Thread-local storage for active timeline values.

    Each thread has its own independent set of timeline values.
    This follows the same pattern as OTel context propagation.

    Timeline types:
        sequence  — integer counter (frame index, tick number)
        timestamp — nanoseconds since Unix epoch
    """

    def __init__(self) -> None:
        super().__init__()
        self.timelines: dict[str, tuple[str, int]] = {}
        self._log_tick: int = 0

    def set_time(
        self,
        name: str,
        *,
        sequence: int | None = None,
        timestamp: int | float | None = None,
    ) -> None:
        """Set a timeline value in the current thread's context.

        Parameters
        ----------
        name : str
            Timeline name (e.g., "sensor_clock", "frame_idx", "sim_time").
        sequence : int, optional
            Sequence/counter value (mutually exclusive with timestamp).
        timestamp : int or float, optional
            Temporal value. If int, treated as nanoseconds since epoch.
            If float, treated as seconds since epoch (converted to nanos).
        """
        if sequence is not None:
            self.timelines[name] = ("sequence", int(sequence))
        elif timestamp is not None:
            if isinstance(timestamp, float):
                # Convert seconds to nanoseconds
                self.timelines[name] = ("timestamp", int(timestamp * 1e9))
            else:
                self.timelines[name] = ("timestamp", int(timestamp))

    def reset(self) -> None:
        """Clear all custom timeline values. Auto-timelines (log_tick, log_time) are unaffected."""
        self.timelines.clear()

    def snapshot(self) -> dict[str, str]:
        """Get current timeline values as a flat dict for inclusion in tags.

        Returns dict like: {"_tl_sensor_clock": "171153600000", "_tl_frame_idx": "42"}
        Timeline type is encoded in a companion key: {"_tl_sensor_clock_type": "timestamp"}
        """
        result: dict[str, str] = {}

        # Auto-timelines (lazy init for background threads — threading.local
        # only calls __init__ for the creating thread)
        self._log_tick = getattr(self, "_log_tick", 0) + 1
        result["_tl_log_tick"] = str(self._log_tick)
        result["_tl_log_tick_type"] = "sequence"
        result["_tl_log_time"] = str(int(time.time() * 1e9))
        result["_tl_log_time_type"] = "timestamp"

        # User-defined timelines
        timelines = getattr(self, "timelines", {})
        for name, (tl_type, value) in timelines.items():
            result[f"_tl_{name}"] = str(value)
            result[f"_tl_{name}_type"] = tl_type

        return result

    def has_custom_timelines(self) -> bool:
        """Check if any user-defined timelines are set."""
        return len(getattr(self, "timelines", {})) > 0

    def get_timeline_names(self) -> list[str]:
        """Return names of all active custom timelines."""
        return list(getattr(self, "timelines", {}).keys())


# Global singleton (thread-local, so each thread gets its own state)
_timeline_ctx = TimelineContext()


def get_timeline_context() -> TimelineContext:
    """Get the global thread-local timeline context."""
    return _timeline_ctx
