"""RoboTrace ROS2 Bridge — subscribes to ROS2 topics, forwards to RoboTrace.

Can be used as:
1. A standalone node: python -m robotrace.ros2.bridge --config bridge.yaml
2. A library: bridge = RoboTraceBridge(config); bridge.spin()

Configuration via YAML:
    robotrace:
      host: http://localhost:8080
      public_key: rt-pk-xxx
      secret_key: rt-sk-xxx
      device_id: my-robot
    topics:
      /scan: {stream: sensors/lidar, type: LaserScan}
      /odom: {stream: robot/pose, type: Odometry, split: true}
      /battery_state: {stream: sensors/battery, type: BatteryState}
      /imu/data: {stream: sensors/imu, type: Imu, split: true, rate_hz: 10}
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from typing import Any

from . import converters
from ._imports import require_rclpy, require_message_type

logger = logging.getLogger("robotrace.ros2.bridge")


# ---------------------------------------------------------------------------
# ROS2 type name -> (ros2_pkg.msg, MsgClass) mapping
# ---------------------------------------------------------------------------

_ROS2_MSG_TYPES: dict[str, tuple[str, str]] = {
    "LaserScan": ("sensor_msgs.msg", "LaserScan"),
    "Imu": ("sensor_msgs.msg", "Imu"),
    "BatteryState": ("sensor_msgs.msg", "BatteryState"),
    "Odometry": ("nav_msgs.msg", "Odometry"),
    "Twist": ("geometry_msgs.msg", "Twist"),
    "PoseStamped": ("geometry_msgs.msg", "PoseStamped"),
    "NavSatFix": ("sensor_msgs.msg", "NavSatFix"),
    "JointState": ("sensor_msgs.msg", "JointState"),
    "Image": ("sensor_msgs.msg", "Image"),
    "CompressedImage": ("sensor_msgs.msg", "CompressedImage"),
    "PointCloud2": ("sensor_msgs.msg", "PointCloud2"),
    "Path": ("nav_msgs.msg", "Path"),
    "DiagnosticArray": ("diagnostic_msgs.msg", "DiagnosticArray"),
    "Temperature": ("sensor_msgs.msg", "Temperature"),
    "Range": ("sensor_msgs.msg", "Range"),
}


# ---------------------------------------------------------------------------
# Handler functions: converter + rt.log
# ---------------------------------------------------------------------------

def _handle_laser_scan(rt, stream: str, msg, **kw) -> None:
    rt.log(stream, converters.from_laser_scan(msg))


def _handle_imu(rt, stream: str, msg, *, split: bool = False, **kw) -> None:
    result = converters.from_imu(msg)
    if split:
        rt.log(f"{stream}/accel", result["accel"])
        rt.log(f"{stream}/gyro", result["gyro"])
        rt.log(f"{stream}/orientation", result["orientation"])
    else:
        # Log accel as the primary value
        rt.log(stream, result["accel"])


def _handle_battery_state(rt, stream: str, msg, **kw) -> None:
    rt.log(stream, converters.from_battery_state(msg))


def _handle_odometry(rt, stream: str, msg, *, split: bool = False, **kw) -> None:
    rt.log(stream, converters.from_odometry(msg))
    if split:
        rt.log(f"{stream}/twist", converters.from_odometry_twist(msg))


def _handle_twist(rt, stream: str, msg, **kw) -> None:
    rt.log(stream, converters.from_twist(msg))


def _handle_pose_stamped(rt, stream: str, msg, **kw) -> None:
    rt.log(stream, converters.from_pose_stamped(msg))


def _handle_nav_sat_fix(rt, stream: str, msg, **kw) -> None:
    rt.log(stream, converters.from_nav_sat_fix(msg))


def _handle_joint_state(rt, stream: str, msg, **kw) -> None:
    rt.log(stream, converters.from_joint_state(msg))


def _handle_image(rt, stream: str, msg, **kw) -> None:
    rt.log(stream, converters.from_image(msg))


def _handle_compressed_image(rt, stream: str, msg, **kw) -> None:
    rt.log(stream, converters.from_compressed_image(msg))


def _handle_point_cloud2(rt, stream: str, msg, **kw) -> None:
    max_points = kw.get("max_points", 10000)
    rt.log(stream, converters.from_point_cloud2(msg, max_points=max_points))


def _handle_path(rt, stream: str, msg, **kw) -> None:
    rt.log(stream, converters.from_path(msg))


def _handle_diagnostic_array(rt, stream: str, msg, **kw) -> None:
    logs = converters.from_diagnostic_array(msg)
    for log_entry in logs:
        rt.log(stream, log_entry)


def _handle_temperature(rt, stream: str, msg, **kw) -> None:
    rt.log(stream, converters.from_temperature(msg))


def _handle_range(rt, stream: str, msg, **kw) -> None:
    rt.log(stream, converters.from_range(msg))


_TYPE_MAP = {
    "LaserScan": _handle_laser_scan,
    "Imu": _handle_imu,
    "BatteryState": _handle_battery_state,
    "Odometry": _handle_odometry,
    "Twist": _handle_twist,
    "PoseStamped": _handle_pose_stamped,
    "NavSatFix": _handle_nav_sat_fix,
    "JointState": _handle_joint_state,
    "Image": _handle_image,
    "CompressedImage": _handle_compressed_image,
    "PointCloud2": _handle_point_cloud2,
    "Path": _handle_path,
    "DiagnosticArray": _handle_diagnostic_array,
    "Temperature": _handle_temperature,
    "Range": _handle_range,
}


# ---------------------------------------------------------------------------
# Stream type metadata for configure_stream()
# ---------------------------------------------------------------------------

_STREAM_TYPE_HINTS: dict[str, str] = {
    "LaserScan": "laser_scan",
    "Imu": "vector3",
    "BatteryState": "battery",
    "Odometry": "pose3d",
    "Twist": "twist",
    "PoseStamped": "pose3d",
    "NavSatFix": "geolocation",
    "JointState": "joint_state",
    "Image": "image",
    "CompressedImage": "image",
    "PointCloud2": "pointcloud",
    "Path": "path",
    "DiagnosticArray": "log",
    "Temperature": "scalar",
    "Range": "scalar",
}


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class RoboTraceBridge:
    """Configurable bridge: subscribes to ROS2 topics and forwards to RoboTrace.

    Args:
        config: Dict with 'robotrace' and 'topics' keys.
        config_path: Path to YAML config file (alternative to config dict).
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        config_path: str | None = None,
    ) -> None:
        if config is None and config_path is not None:
            config = self._load_yaml(config_path)
        if config is None:
            raise ValueError("Must provide config dict or config_path")

        self._config = config
        self._rt = None
        self._node = None
        self._subscriptions: list[Any] = []
        self._rate_limiters: dict[str, float] = {}  # topic -> last_send_time
        self._rate_limits: dict[str, float] = {}    # topic -> min_interval_s
        self._shutdown = False

    @staticmethod
    def _load_yaml(path: str) -> dict[str, Any]:
        """Load YAML config file."""
        import yaml
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def _init_robotrace(self) -> Any:
        """Create and return a RoboTrace client from config."""
        from robotrace import RoboTrace

        rt_cfg = self._config.get("robotrace", {})
        rt = RoboTrace(
            host=rt_cfg.get("host", "http://localhost:8080"),
            public_key=rt_cfg.get("public_key", ""),
            secret_key=rt_cfg.get("secret_key", ""),
            device_id=rt_cfg.get("device_id", "ros2-bridge"),
        )
        return rt

    def _init_node(self) -> None:
        """Initialize ROS2 node and subscriptions."""
        rclpy = require_rclpy()

        if not rclpy.ok():
            rclpy.init()

        self._node = rclpy.create_node("robotrace_bridge")
        self._rt = self._init_robotrace()

        topics_cfg = self._config.get("topics", {})
        for topic_name, topic_cfg in topics_cfg.items():
            self._subscribe_topic(topic_name, topic_cfg)

        logger.info(
            "RoboTrace bridge initialized: %d topic(s)", len(self._subscriptions)
        )

    def _subscribe_topic(self, topic_name: str, topic_cfg: dict[str, Any]) -> None:
        """Subscribe to a single ROS2 topic."""
        type_name = topic_cfg.get("type", "")
        stream = topic_cfg.get("stream", topic_name.strip("/").replace("/", "."))
        split = topic_cfg.get("split", False)
        rate_hz = topic_cfg.get("rate_hz")

        if type_name not in _TYPE_MAP:
            logger.warning("Unknown type '%s' for topic %s — skipping", type_name, topic_name)
            return

        # Resolve ROS2 message class
        pkg, cls_name = _ROS2_MSG_TYPES[type_name]
        msg_cls = require_message_type(pkg, cls_name)

        # Set up rate limiting
        if rate_hz is not None and rate_hz > 0:
            self._rate_limits[topic_name] = 1.0 / float(rate_hz)
            self._rate_limiters[topic_name] = 0.0

        # Configure stream type hint
        if hasattr(self._rt, "configure_stream"):
            type_hint = _STREAM_TYPE_HINTS.get(type_name, "scalar")
            try:
                self._rt.configure_stream(stream, type=type_hint)
            except Exception:
                pass  # configure_stream is best-effort

        handler = _TYPE_MAP[type_name]
        extra_kw = {}
        if "max_points" in topic_cfg:
            extra_kw["max_points"] = topic_cfg["max_points"]

        def make_callback(h, s, sp, tn, ekw):
            def callback(msg):
                # Rate limiting
                if tn in self._rate_limits:
                    now = time.monotonic()
                    if now - self._rate_limiters.get(tn, 0.0) < self._rate_limits[tn]:
                        return
                    self._rate_limiters[tn] = now
                try:
                    h(self._rt, s, msg, split=sp, **ekw)
                except Exception:
                    logger.exception("Error processing %s on %s", type_name, tn)
            return callback

        cb = make_callback(handler, stream, split, topic_name, extra_kw)
        sub = self._node.create_subscription(msg_cls, topic_name, cb, 10)
        self._subscriptions.append(sub)
        logger.info("Subscribed: %s -> %s (type=%s)", topic_name, stream, type_name)

    def spin(self) -> None:
        """Block and process ROS2 callbacks until shutdown."""
        rclpy = require_rclpy()

        if self._node is None:
            self._init_node()

        # Handle Ctrl+C gracefully
        def _signal_handler(sig, frame):
            self._shutdown = True

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        logger.info("RoboTrace bridge spinning...")
        try:
            while not self._shutdown and rclpy.ok():
                rclpy.spin_once(self._node, timeout_sec=0.1)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Clean up ROS2 node and RoboTrace client."""
        self._shutdown = True
        if self._node is not None:
            self._node.destroy_node()
            self._node = None
        rclpy = None
        try:
            import rclpy as _rclpy
            rclpy = _rclpy
        except ImportError:
            pass
        if rclpy is not None and rclpy.ok():
            try:
                rclpy.shutdown()
            except Exception:
                pass
        logger.info("RoboTrace bridge shut down.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """CLI entry point: python -m robotrace.ros2 --config bridge.yaml"""
    parser = argparse.ArgumentParser(
        description="RoboTrace ROS2 Bridge — forward ROS2 topics to RoboTrace"
    )
    parser.add_argument(
        "--config", "-c",
        required=True,
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    bridge = RoboTraceBridge(config_path=args.config)
    bridge.spin()
