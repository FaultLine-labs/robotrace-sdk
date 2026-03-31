"""ROS2 message -> RoboTrace type converters.

Pure functions — no rclpy dependency. Work with any object that has the
correct attributes (duck-typed).

Usage:
    from robotrace.ros2 import from_odometry, from_laser_scan

    rt.log("robot/pose", from_odometry(odom_msg))
    rt.log("sensors/lidar", from_laser_scan(scan_msg))
"""

from __future__ import annotations

import logging
import math
import struct
import warnings
from typing import Any

from robotrace import (
    Battery, GeoLocation, Image, JointState, LaserScan, Log,
    Path, PointCloud, Pose3D, Scalar, Twist, Vector3,
)

logger = logging.getLogger("robotrace.ros2.converters")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> float | None:
    """Return float or None if NaN/inf."""
    if v is None:
        return None
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _clean_ranges(ranges) -> list[float]:
    """Replace inf/NaN in lidar ranges with 0.0 (out-of-range sentinel)."""
    out = []
    for r in ranges:
        f = float(r)
        if math.isinf(f) or math.isnan(f):
            out.append(0.0)
        else:
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# Converter: LaserScan
# ---------------------------------------------------------------------------

def from_laser_scan(msg) -> LaserScan:
    """Convert sensor_msgs/msg/LaserScan to RoboTrace LaserScan.

    Replaces inf/NaN range values with 0.0 (out-of-range sentinel).
    """
    return LaserScan(
        ranges=_clean_ranges(msg.ranges),
        angle_min=float(msg.angle_min),
        angle_max=float(msg.angle_max),
        angle_increment=float(msg.angle_increment),
    )


# ---------------------------------------------------------------------------
# Converter: Imu
# ---------------------------------------------------------------------------

def from_imu(msg) -> dict[str, Vector3]:
    """Convert sensor_msgs/msg/Imu to dict of RoboTrace types.

    Returns:
        {"accel": Vector3, "gyro": Vector3, "orientation": Vector3}
        orientation is returned as euler-like (qx, qy, qz) in a Vector3
        for simplicity; use from_odometry for full quaternion Pose3D.
    """
    accel = Vector3(
        x=float(msg.linear_acceleration.x),
        y=float(msg.linear_acceleration.y),
        z=float(msg.linear_acceleration.z),
    )
    gyro = Vector3(
        x=float(msg.angular_velocity.x),
        y=float(msg.angular_velocity.y),
        z=float(msg.angular_velocity.z),
    )
    orientation = Vector3(
        x=float(msg.orientation.x),
        y=float(msg.orientation.y),
        z=float(msg.orientation.z),
    )
    return {"accel": accel, "gyro": gyro, "orientation": orientation}


# ---------------------------------------------------------------------------
# Converter: BatteryState
# ---------------------------------------------------------------------------

def from_battery_state(msg) -> Battery:
    """Convert sensor_msgs/msg/BatteryState to RoboTrace Battery.

    msg.percentage is passed AS-IS (0.0-1.0 or 0-100 depending on source).
    NaN values are converted to None.
    """
    voltage = _safe_float(msg.voltage)
    current = _safe_float(msg.current)
    percentage = _safe_float(msg.percentage)

    # Extract cell voltages if available
    cell_voltages = None
    if hasattr(msg, "cell_voltage") and msg.cell_voltage:
        cell_voltages = [float(v) for v in msg.cell_voltage
                         if not math.isnan(float(v))]
        if not cell_voltages:
            cell_voltages = None

    # Extract temperature if available
    temperature = None
    if hasattr(msg, "temperature"):
        temperature = _safe_float(msg.temperature)

    return Battery(
        voltage=voltage if voltage is not None else 0.0,
        current=current,
        soc=percentage,
        temperature=temperature,
        cell_voltages=cell_voltages,
    )


# ---------------------------------------------------------------------------
# Converter: Odometry (pose only)
# ---------------------------------------------------------------------------

def from_odometry(msg) -> Pose3D:
    """Convert nav_msgs/msg/Odometry to RoboTrace Pose3D (position + orientation).

    Extracts only the pose. Use from_odometry_twist() for velocity.
    """
    pos = msg.pose.pose.position
    ori = msg.pose.pose.orientation
    return Pose3D(
        x=float(pos.x),
        y=float(pos.y),
        z=float(pos.z),
        qx=float(ori.x),
        qy=float(ori.y),
        qz=float(ori.z),
        qw=float(ori.w),
    )


# ---------------------------------------------------------------------------
# Converter: Odometry twist
# ---------------------------------------------------------------------------

def from_odometry_twist(msg) -> Twist:
    """Convert nav_msgs/msg/Odometry twist component to RoboTrace Twist."""
    tw = msg.twist.twist
    return Twist(
        linear_x=float(tw.linear.x),
        linear_y=float(tw.linear.y),
        linear_z=float(tw.linear.z),
        angular_x=float(tw.angular.x),
        angular_y=float(tw.angular.y),
        angular_z=float(tw.angular.z),
    )


# ---------------------------------------------------------------------------
# Converter: Twist
# ---------------------------------------------------------------------------

def from_twist(msg) -> Twist:
    """Convert geometry_msgs/msg/Twist to RoboTrace Twist."""
    return Twist(
        linear_x=float(msg.linear.x),
        linear_y=float(msg.linear.y),
        linear_z=float(msg.linear.z),
        angular_x=float(msg.angular.x),
        angular_y=float(msg.angular.y),
        angular_z=float(msg.angular.z),
    )


# ---------------------------------------------------------------------------
# Converter: PoseStamped
# ---------------------------------------------------------------------------

def from_pose_stamped(msg) -> Pose3D:
    """Convert geometry_msgs/msg/PoseStamped to RoboTrace Pose3D."""
    pos = msg.pose.position
    ori = msg.pose.orientation
    return Pose3D(
        x=float(pos.x),
        y=float(pos.y),
        z=float(pos.z),
        qx=float(ori.x),
        qy=float(ori.y),
        qz=float(ori.z),
        qw=float(ori.w),
    )


# ---------------------------------------------------------------------------
# Converter: NavSatFix
# ---------------------------------------------------------------------------

_NAV_SAT_STATUS_MAP = {
    -1: "none",       # STATUS_NO_FIX
    0: "2d",          # STATUS_FIX
    1: "3d",          # STATUS_SBAS_FIX
    2: "rtk_float",   # STATUS_GBAS_FIX
}


def from_nav_sat_fix(msg) -> GeoLocation:
    """Convert sensor_msgs/msg/NavSatFix to RoboTrace GeoLocation.

    Maps msg.status.status to fix_type string. Extracts horizontal accuracy
    from the covariance diagonal (sqrt of first diagonal element).
    """
    lat = _safe_float(msg.latitude)
    lon = _safe_float(msg.longitude)
    alt = _safe_float(msg.altitude)

    # Map status code to fix_type string
    status_code = int(msg.status.status) if hasattr(msg, "status") else 0
    fix_type = _NAV_SAT_STATUS_MAP.get(status_code, "3d")

    # Extract accuracy from covariance diagonal
    accuracy = None
    if hasattr(msg, "position_covariance") and msg.position_covariance:
        cov = msg.position_covariance
        # Covariance is 3x3 row-major: [0]=lat, [4]=lon, [8]=alt
        if len(cov) >= 5:
            lat_var = _safe_float(cov[0])
            lon_var = _safe_float(cov[4])
            if lat_var is not None and lon_var is not None:
                accuracy = math.sqrt((lat_var + lon_var) / 2.0)

    return GeoLocation(
        latitude=lat if lat is not None else 0.0,
        longitude=lon if lon is not None else 0.0,
        altitude=alt if alt is not None else 0.0,
        fix_type=fix_type,
        accuracy=accuracy,
    )


# ---------------------------------------------------------------------------
# Converter: JointState
# ---------------------------------------------------------------------------

def from_joint_state(msg) -> JointState:
    """Convert sensor_msgs/msg/JointState to RoboTrace JointState."""
    names = list(msg.name) if msg.name else []
    positions = [float(p) for p in msg.position] if msg.position else []
    velocities = [float(v) for v in msg.velocity] if msg.velocity else None
    efforts = [float(e) for e in msg.effort] if msg.effort else None

    # Don't pass empty lists as None
    if velocities is not None and len(velocities) == 0:
        velocities = None
    if efforts is not None and len(efforts) == 0:
        efforts = None

    return JointState(
        names=names,
        positions=positions,
        velocities=velocities,
        efforts=efforts,
    )


# ---------------------------------------------------------------------------
# Converter: Image (raw)
# ---------------------------------------------------------------------------

def from_image(msg, *, compress: bool = True) -> Image:
    """Convert sensor_msgs/msg/Image to RoboTrace Image.

    Args:
        msg: ROS2 Image message with data, height, width, encoding fields.
        compress: If True, compress raw pixels to JPEG using cv2 or PIL.
                  If neither is available, returns raw bytes with a warning.
    """
    raw_data = bytes(msg.data)

    if not compress:
        fmt = getattr(msg, "encoding", "raw")
        return Image(data=raw_data, format=fmt)

    # Try OpenCV first
    try:
        import cv2
        import numpy as np
        height = int(msg.height)
        width = int(msg.width)
        encoding = getattr(msg, "encoding", "bgr8")

        if encoding in ("bgr8", "8UC3"):
            arr = np.frombuffer(raw_data, dtype=np.uint8).reshape(height, width, 3)
        elif encoding in ("rgb8",):
            arr = np.frombuffer(raw_data, dtype=np.uint8).reshape(height, width, 3)
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        elif encoding in ("mono8", "8UC1"):
            arr = np.frombuffer(raw_data, dtype=np.uint8).reshape(height, width)
        elif encoding in ("16UC1", "mono16"):
            arr = np.frombuffer(raw_data, dtype=np.uint16).reshape(height, width)
            arr = (arr / 256).astype(np.uint8)
        else:
            arr = np.frombuffer(raw_data, dtype=np.uint8).reshape(height, width, -1)

        _, jpeg_buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return Image(data=bytes(jpeg_buf), format="jpeg")
    except ImportError:
        pass

    # Try PIL
    try:
        from PIL import Image as PILImage
        import io
        import numpy as np
        height = int(msg.height)
        width = int(msg.width)
        encoding = getattr(msg, "encoding", "rgb8")

        if encoding in ("rgb8", "8UC3"):
            arr = np.frombuffer(raw_data, dtype=np.uint8).reshape(height, width, 3)
            pil_img = PILImage.fromarray(arr, "RGB")
        elif encoding in ("bgr8",):
            arr = np.frombuffer(raw_data, dtype=np.uint8).reshape(height, width, 3)
            pil_img = PILImage.fromarray(arr[:, :, ::-1], "RGB")
        elif encoding in ("mono8", "8UC1"):
            arr = np.frombuffer(raw_data, dtype=np.uint8).reshape(height, width)
            pil_img = PILImage.fromarray(arr, "L")
        else:
            arr = np.frombuffer(raw_data, dtype=np.uint8).reshape(height, width, -1)
            pil_img = PILImage.fromarray(arr)

        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=80)
        return Image(data=buf.getvalue(), format="jpeg")
    except ImportError:
        pass

    # Fallback: return raw bytes with warning
    warnings.warn(
        "Neither cv2 nor PIL available — returning raw image bytes. "
        "Install opencv-python or Pillow for JPEG compression.",
        stacklevel=2,
    )
    fmt = getattr(msg, "encoding", "raw")
    return Image(data=raw_data, format=fmt)


# ---------------------------------------------------------------------------
# Converter: CompressedImage
# ---------------------------------------------------------------------------

def from_compressed_image(msg) -> Image:
    """Convert sensor_msgs/msg/CompressedImage to RoboTrace Image.

    The data is already compressed (jpeg/png), so just wrap it.
    """
    fmt = getattr(msg, "format", "jpeg")
    # ROS2 format string can be "jpeg" or "bgr8; jpeg compressed"
    if "jpeg" in fmt.lower() or "jpg" in fmt.lower():
        fmt = "jpeg"
    elif "png" in fmt.lower():
        fmt = "png"
    return Image(data=bytes(msg.data), format=fmt)


# ---------------------------------------------------------------------------
# Converter: PointCloud2
# ---------------------------------------------------------------------------

def from_point_cloud2(msg, *, max_points: int = 10000) -> PointCloud:
    """Convert sensor_msgs/msg/PointCloud2 to RoboTrace PointCloud.

    Tries sensor_msgs_py.point_cloud2.read_points() first, then falls back
    to manual struct parsing of the binary data.

    Args:
        msg: ROS2 PointCloud2 message.
        max_points: Maximum points to keep (subsampled if exceeded).
    """
    points: list[list[float]] = []

    # Try the official helper first
    try:
        from sensor_msgs_py.point_cloud2 import read_points
        for pt in read_points(msg, field_names=("x", "y", "z"), skip_nans=True):
            points.append([float(pt[0]), float(pt[1]), float(pt[2])])
    except (ImportError, Exception):
        # Manual parsing fallback
        points = _parse_pointcloud2_manual(msg)

    # Subsample if needed
    if len(points) > max_points:
        step = len(points) / max_points
        points = [points[int(i * step)] for i in range(max_points)]

    return PointCloud(points=points)


def _parse_pointcloud2_manual(msg) -> list[list[float]]:
    """Parse PointCloud2 binary data manually using struct."""
    points: list[list[float]] = []

    # Find x, y, z field offsets
    x_offset = y_offset = z_offset = None
    for field in msg.fields:
        name = field.name.lower()
        if name == "x":
            x_offset = field.offset
        elif name == "y":
            y_offset = field.offset
        elif name == "z":
            z_offset = field.offset

    if x_offset is None or y_offset is None or z_offset is None:
        logger.warning("PointCloud2 missing x/y/z fields")
        return points

    data = bytes(msg.data)
    point_step = int(msg.point_step)
    row_step = int(msg.row_step)
    height = int(msg.height)
    width = int(msg.width)

    for row in range(height):
        for col in range(width):
            offset = row * row_step + col * point_step
            try:
                x = struct.unpack_from("<f", data, offset + x_offset)[0]
                y = struct.unpack_from("<f", data, offset + y_offset)[0]
                z = struct.unpack_from("<f", data, offset + z_offset)[0]
                if not (math.isnan(x) or math.isnan(y) or math.isnan(z)):
                    points.append([x, y, z])
            except struct.error:
                break

    return points


# ---------------------------------------------------------------------------
# Converter: Path
# ---------------------------------------------------------------------------

def from_path(msg) -> Path:
    """Convert nav_msgs/msg/Path to RoboTrace Path."""
    pts: list[list[float]] = []
    for pose_stamped in msg.poses:
        pos = pose_stamped.pose.position
        pts.append([float(pos.x), float(pos.y), float(pos.z)])

    frame_id = "map"
    if hasattr(msg, "header") and hasattr(msg.header, "frame_id"):
        frame_id = msg.header.frame_id or "map"

    return Path(points=pts, frame_id=frame_id)


# ---------------------------------------------------------------------------
# Converter: DiagnosticArray
# ---------------------------------------------------------------------------

_DIAG_LEVEL_MAP = {
    0: "INFO",   # OK
    1: "WARN",   # WARN
    2: "ERROR",  # ERROR
    3: "ERROR",  # STALE
}


def from_diagnostic_array(msg) -> list[Log]:
    """Convert diagnostic_msgs/msg/DiagnosticArray to list of RoboTrace Logs.

    One Log per DiagnosticStatus entry.
    """
    logs: list[Log] = []
    for status in msg.status:
        level_num = int(status.level) if hasattr(status, "level") else 0
        level = _DIAG_LEVEL_MAP.get(level_num, "INFO")

        # Build message from name + message + key-value pairs
        parts = []
        if hasattr(status, "name") and status.name:
            parts.append(f"[{status.name}]")
        if hasattr(status, "message") and status.message:
            parts.append(status.message)
        if hasattr(status, "values") and status.values:
            kv = ", ".join(f"{v.key}={v.value}" for v in status.values)
            parts.append(f"({kv})")

        message = " ".join(parts) if parts else "diagnostic status"
        logs.append(Log(message=message, level=level))

    return logs


# ---------------------------------------------------------------------------
# Converter: Temperature
# ---------------------------------------------------------------------------

def from_temperature(msg) -> Scalar:
    """Convert sensor_msgs/msg/Temperature to RoboTrace Scalar.

    Returns temperature in Celsius.
    """
    temp = _safe_float(msg.temperature)
    return Scalar(value=temp if temp is not None else 0.0)


# ---------------------------------------------------------------------------
# Converter: Range
# ---------------------------------------------------------------------------

def from_range(msg) -> Scalar:
    """Convert sensor_msgs/msg/Range to RoboTrace Scalar.

    Returns range distance in meters. Inf/NaN mapped to 0.0.
    """
    r = _safe_float(msg.range)
    return Scalar(value=r if r is not None else 0.0)
