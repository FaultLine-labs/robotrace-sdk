"""Semantic data types for robot telemetry, inspired by Rerun archetypes."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Scalar:
    """Single numeric value — auto-plotted as time series."""

    value: float

    @property
    def type_name(self) -> str:
        return "scalar"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type_name, "value": self.value}


@dataclass(frozen=True, slots=True)
class Vector3:
    """3D vector (position, velocity, acceleration, force, etc.)."""

    x: float
    y: float
    z: float

    @property
    def type_name(self) -> str:
        return "vector3"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type_name, "x": self.x, "y": self.y, "z": self.z}


@dataclass(frozen=True, slots=True)
class VectorN:
    """N-dimensional vector (joint positions, feature vectors, etc.)."""

    values: list[float]

    @property
    def type_name(self) -> str:
        return "vectorn"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type_name, "values": self.values}


@dataclass(frozen=True, slots=True)
class Pose3D:
    """6DOF pose: position + quaternion orientation."""

    x: float
    y: float
    z: float
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0
    qw: float = 1.0

    @property
    def type_name(self) -> str:
        return "pose3d"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "x": self.x, "y": self.y, "z": self.z,
            "qx": self.qx, "qy": self.qy, "qz": self.qz, "qw": self.qw,
        }


@dataclass(frozen=True, slots=True)
class Transform3D:
    """Coordinate transform: translation + rotation quaternion."""

    translation: Vector3
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)

    @property
    def type_name(self) -> str:
        return "transform3d"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "translation": self.translation.to_dict(),
            "rotation": list(self.rotation),
        }


@dataclass(frozen=True, slots=True)
class PointCloud:
    """Point cloud: Nx3 positions with optional Nx3 colors."""

    points: list[list[float]]
    colors: list[list[float]] | None = None

    @property
    def type_name(self) -> str:
        return "pointcloud"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type_name, "points": self.points}
        if self.colors is not None:
            d["colors"] = self.colors
        return d


@dataclass(frozen=True, slots=True)
class Image:
    """Compressed image data."""

    data: bytes
    format: str = "jpeg"

    @property
    def type_name(self) -> str:
        return "image"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "data": base64.b64encode(self.data).decode("ascii"),
            "size_bytes": len(self.data),
            "format": self.format,
        }


@dataclass(frozen=True, slots=True)
class DepthImage:
    """Depth map as raw bytes or array."""

    data: bytes

    @property
    def type_name(self) -> str:
        return "depth_image"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "data": base64.b64encode(self.data).decode("ascii"),
            "size_bytes": len(self.data),
        }


@dataclass(frozen=True, slots=True)
class Video:
    """Video clip data for upload to artifact storage.

    Note: ``to_dict()`` returns only metadata (size, format, dimensions).
    The binary ``data`` field is **not** included in the dict because video
    files must be uploaded separately via a presigned URL obtained from the
    ``/api/v1/artifacts/presign`` endpoint.
    """

    data: bytes
    format: str = "mp4"  # mp4, webm, avi
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None

    @property
    def type_name(self) -> str:
        return "video"

    def to_dict(self) -> dict[str, Any]:
        """Return metadata dict (no binary data). Binary must be uploaded via presigned URL."""
        d: dict[str, Any] = {
            "type": self.type_name,
            "size_bytes": len(self.data),
            "format": self.format,
        }
        if self.duration_s is not None:
            d["duration_s"] = self.duration_s
        if self.width is not None:
            d["width"] = self.width
        if self.height is not None:
            d["height"] = self.height
        return d


@dataclass(frozen=True, slots=True)
class LaserScan:
    """2D lidar scan."""

    ranges: list[float]
    angle_min: float
    angle_max: float
    angle_increment: float

    @property
    def type_name(self) -> str:
        return "laser_scan"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "ranges": self.ranges,
            "angle_min": self.angle_min,
            "angle_max": self.angle_max,
            "angle_increment": self.angle_increment,
        }


@dataclass(frozen=True, slots=True)
class JointState:
    """Joint state for multi-DOF robots."""

    names: list[str]
    positions: list[float]
    velocities: list[float] | None = None
    efforts: list[float] | None = None

    @property
    def type_name(self) -> str:
        return "joint_state"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type_name,
            "names": self.names,
            "positions": self.positions,
        }
        if self.velocities is not None:
            d["velocities"] = self.velocities
        if self.efforts is not None:
            d["efforts"] = self.efforts
        return d


@dataclass(frozen=True, slots=True)
class NumericSet:
    """Named set of numeric values (e.g. joint temperatures)."""

    values: dict[str, float]

    @property
    def type_name(self) -> str:
        return "numeric_set"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type_name, "values": self.values}


@dataclass(frozen=True, slots=True)
class BoundingBox2D:
    """2D detection bounding box."""

    x: float
    y: float
    w: float
    h: float
    label: str = ""

    @property
    def type_name(self) -> str:
        return "bbox2d"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class BoundingBox3D:
    """3D detection bounding box."""

    center: Vector3
    size: Vector3
    label: str = ""

    @property
    def type_name(self) -> str:
        return "bbox3d"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "center": self.center.to_dict(),
            "size": self.size.to_dict(),
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class GeoLocation:
    """GPS/GNSS geographic position (WGS84).

    Fields follow Foxglove LocationFix + ROS2 NavSatFix conventions.
    All coordinates in WGS84 (EPSG:4326).
    """

    latitude: float     # Degrees (-90 to 90)
    longitude: float    # Degrees (-180 to 180)
    altitude: float = 0.0          # Meters above WGS84 ellipsoid
    heading: float | None = None   # Degrees clockwise from north (0-360)
    speed: float | None = None     # Meters per second
    accuracy: float | None = None  # Horizontal accuracy in meters (CEP)
    altitude_accuracy: float | None = None  # Vertical accuracy in meters
    fix_type: str = "3d"           # "none", "2d", "3d", "rtk_fixed", "rtk_float"

    @property
    def type_name(self) -> str:
        return "geolocation"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "fix_type": self.fix_type,
        }
        if self.heading is not None:
            d["heading"] = self.heading
        if self.speed is not None:
            d["speed"] = self.speed
        if self.accuracy is not None:
            d["accuracy"] = self.accuracy
        if self.altitude_accuracy is not None:
            d["altitude_accuracy"] = self.altitude_accuracy
        return d


@dataclass(frozen=True, slots=True)
class Path:
    """Sequence of 2D or 3D points representing a trajectory or planned path."""

    points: list[list[float]]  # [[x, y], [x, y, z], ...]
    frame_id: str = "map"

    @property
    def type_name(self) -> str:
        return "path"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "points": self.points,
            "frame_id": self.frame_id,
            "num_points": len(self.points),
        }


@dataclass(frozen=True, slots=True)
class Twist:
    """Linear and angular velocity (robot velocity command/state)."""

    linear_x: float = 0.0    # Forward/backward (m/s)
    linear_y: float = 0.0    # Left/right (m/s)
    linear_z: float = 0.0    # Up/down (m/s)
    angular_x: float = 0.0   # Roll rate (rad/s)
    angular_y: float = 0.0   # Pitch rate (rad/s)
    angular_z: float = 0.0   # Yaw rate (rad/s)

    @property
    def type_name(self) -> str:
        return "twist"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "linear_x": self.linear_x,
            "linear_y": self.linear_y,
            "linear_z": self.linear_z,
            "angular_x": self.angular_x,
            "angular_y": self.angular_y,
            "angular_z": self.angular_z,
        }


@dataclass(frozen=True, slots=True)
class Log:
    """Text log message."""

    message: str
    level: str = "INFO"

    @property
    def type_name(self) -> str:
        return "log"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type_name, "message": self.message, "level": self.level}


@dataclass(frozen=True, slots=True)
class Battery:
    """Battery state — voltage, current, state of charge, and health.

    Usage::

        rt.log("sensors/battery", Battery(voltage=12.6, soc=87.5, current=-2.1))
    """

    voltage: float                              # Volts
    current: float | None = None                # Amps (negative = discharging)
    soc: float | None = None                    # State of charge 0.0–100.0 (%)
    health: float | None = None                 # State of health 0.0–100.0 (%)
    temperature: float | None = None            # Celsius
    cell_voltages: list[float] | None = None    # Per-cell voltages

    @property
    def type_name(self) -> str:
        return "battery"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type_name, "voltage": self.voltage}
        if self.current is not None:
            d["current"] = self.current
        if self.soc is not None:
            d["soc"] = self.soc
        if self.health is not None:
            d["health"] = self.health
        if self.temperature is not None:
            d["temperature"] = self.temperature
        if self.cell_voltages is not None:
            d["cell_voltages"] = self.cell_voltages
        return d


@dataclass(frozen=True, slots=True)
class Bitset:
    """Hardware status register as a bit field.

    Used for motor enable states, sensor health bits, safety interlock bits.

    Usage::

        labels = {0: "motor_enabled", 1: "brake_released", 7: "e_stop"}
        rt.log("hw/status", Bitset(value=0b10000011, labels=labels))
    """

    value: int                                  # Raw integer register value
    width: int = 32                             # 32 or 64 bits
    labels: dict[int, str] | None = None        # bit_index -> label name

    @property
    def type_name(self) -> str:
        return "bitset"

    def is_set(self, bit: int) -> bool:
        """Return True if the bit at position ``bit`` is set."""
        return bool((self.value >> bit) & 1)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type_name,
            # Serialize as string when > 32 bits to avoid JS Number precision loss
            "value": str(self.value) if self.width > 32 else self.value,
            "width": self.width,
        }
        if self.labels:
            d["labels"] = {str(k): v for k, v in self.labels.items()}
            d["active"] = [
                self.labels[i]
                for i in sorted(self.labels)
                if self.is_set(i)
            ]
        return d
