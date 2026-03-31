"""ROS2 timestamp conversion helpers."""


def stamp_to_float(stamp) -> float:
    """Convert builtin_interfaces/msg/Time to float seconds."""
    return float(stamp.sec) + float(stamp.nanosec) * 1e-9


def stamp_to_ns(stamp) -> int:
    """Convert to nanoseconds (for rt.set_time)."""
    return stamp.sec * 1_000_000_000 + stamp.nanosec
