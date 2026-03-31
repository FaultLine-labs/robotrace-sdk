"""Lazy imports for ROS2 dependencies — fail with clear message if not installed."""

_INSTALL_MSG = (
    "ROS2 packages not found. The robotrace.ros2 module requires a ROS2 "
    "installation (Humble or later). Install ROS2 first, then: "
    "source /opt/ros/humble/setup.bash"
)


def require_rclpy():
    """Import and return rclpy, raising ImportError with help message if missing."""
    try:
        import rclpy
        return rclpy
    except ImportError:
        raise ImportError(_INSTALL_MSG)


def require_message_type(pkg: str, msg_type: str):
    """Import and return a specific ROS2 message type.

    Example: require_message_type("sensor_msgs.msg", "LaserScan")
    """
    try:
        import importlib
        mod = importlib.import_module(pkg)
        return getattr(mod, msg_type)
    except (ImportError, AttributeError):
        raise ImportError(
            f"Cannot import {pkg}.{msg_type}. {_INSTALL_MSG}"
        )
