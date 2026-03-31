"""Tests for ROS2 converters — uses duck-typed mock objects, no ROS2 needed."""

from __future__ import annotations

import math
import pytest

from robotrace.ros2.converters import (
    from_battery_state,
    from_compressed_image,
    from_diagnostic_array,
    from_image,
    from_imu,
    from_joint_state,
    from_laser_scan,
    from_nav_sat_fix,
    from_odometry,
    from_odometry_twist,
    from_path,
    from_point_cloud2,
    from_pose_stamped,
    from_range,
    from_temperature,
    from_twist,
)
from robotrace import (
    Battery, GeoLocation, Image, JointState, LaserScan, Log,
    Path, PointCloud, Pose3D, Scalar, Twist, Vector3,
)


# ===========================================================================
# Fake ROS2 message objects (duck-typed)
# ===========================================================================

class FakeLaserScan:
    ranges = [1.0, 2.0, float("inf"), 3.0, float("nan")]
    angle_min = -3.14159
    angle_max = 3.14159
    angle_increment = 0.01745


class FakeImu:
    class linear_acceleration:
        x, y, z = 0.1, 0.2, 9.81

    class angular_velocity:
        x, y, z = 0.01, 0.02, 0.03

    class orientation:
        x, y, z, w = 0.0, 0.0, 0.707, 0.707


class FakeBatteryState:
    voltage = 12.6
    current = -2.1
    percentage = 87.5
    temperature = 35.0
    cell_voltage = [3.15, 3.15, 3.15, 3.15]


class FakeBatteryStateNaN:
    voltage = 12.0
    current = float("nan")
    percentage = float("nan")
    temperature = float("nan")
    cell_voltage = []


class _FakePosition:
    def __init__(self, x=1.0, y=2.0, z=0.5):
        self.x = x
        self.y = y
        self.z = z


class _FakeOrientation:
    def __init__(self, x=0.0, y=0.0, z=0.707, w=0.707):
        self.x = x
        self.y = y
        self.z = z
        self.w = w


class _FakeLinear:
    def __init__(self, x=1.0, y=0.0, z=0.0):
        self.x = x
        self.y = y
        self.z = z


class _FakeAngular:
    def __init__(self, x=0.0, y=0.0, z=0.5):
        self.x = x
        self.y = y
        self.z = z


class _FakePose:
    def __init__(self):
        self.position = _FakePosition()
        self.orientation = _FakeOrientation()


class _FakePoseWithCovariance:
    def __init__(self):
        self.pose = _FakePose()


class _FakeTwistInner:
    def __init__(self):
        self.linear = _FakeLinear()
        self.angular = _FakeAngular()


class _FakeTwistWithCovariance:
    def __init__(self):
        self.twist = _FakeTwistInner()


class FakeOdometry:
    def __init__(self):
        self.pose = _FakePoseWithCovariance()
        self.twist = _FakeTwistWithCovariance()


class FakeTwist:
    def __init__(self):
        self.linear = _FakeLinear(1.0, 0.0, 0.0)
        self.angular = _FakeAngular(0.0, 0.0, 0.5)


class FakePoseStamped:
    def __init__(self):
        self.pose = _FakePose()


class _FakeNavSatStatus:
    status = 0  # STATUS_FIX


class FakeNavSatFix:
    latitude = 37.7749
    longitude = -122.4194
    altitude = 10.0
    status = _FakeNavSatStatus()
    position_covariance = [2.0, 0, 0, 0, 3.0, 0, 0, 0, 5.0]


class FakeNavSatFixNoFix:
    latitude = 0.0
    longitude = 0.0
    altitude = float("nan")
    status = type("S", (), {"status": -1})()
    position_covariance = [float("nan"), 0, 0, 0, float("nan"), 0, 0, 0, 0]


class FakeJointState:
    name = ["joint_1", "joint_2", "joint_3"]
    position = [0.0, 1.57, -0.5]
    velocity = [0.1, 0.0, -0.2]
    effort = [10.0, 5.0, 8.0]


class FakeJointStateMinimal:
    name = ["j1"]
    position = [0.5]
    velocity = []
    effort = []


class FakeImage:
    data = bytes([128] * (4 * 4 * 3))  # 4x4 RGB
    height = 4
    width = 4
    encoding = "rgb8"


class FakeCompressedImage:
    data = b"\xff\xd8\xff\xe0fake_jpeg_data"
    format = "jpeg"


class FakeCompressedImagePng:
    data = b"\x89PNG\r\nfake_png_data"
    format = "bgr8; png compressed"


class _FakePointField:
    def __init__(self, name, offset):
        self.name = name
        self.offset = offset


class FakePointCloud2:
    """Minimal PointCloud2 with 3 points, float32 x/y/z."""
    import struct as _struct

    fields = [
        _FakePointField("x", 0),
        _FakePointField("y", 4),
        _FakePointField("z", 8),
    ]
    point_step = 12
    height = 1
    width = 3
    row_step = 36
    # 3 points: (1,2,3), (4,5,6), (7,8,9)
    data = (
        _struct.pack("<fff", 1.0, 2.0, 3.0)
        + _struct.pack("<fff", 4.0, 5.0, 6.0)
        + _struct.pack("<fff", 7.0, 8.0, 9.0)
    )


class _FakePoseStampedInPath:
    def __init__(self, x, y, z):
        self.pose = type("P", (), {
            "position": type("Pos", (), {"x": x, "y": y, "z": z})()
        })()


class FakePath:
    class header:
        frame_id = "odom"

    poses = [
        _FakePoseStampedInPath(0.0, 0.0, 0.0),
        _FakePoseStampedInPath(1.0, 1.0, 0.0),
        _FakePoseStampedInPath(2.0, 0.0, 0.0),
    ]


class _FakeKeyValue:
    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeDiagStatus:
    def __init__(self, name, message, level, values=None):
        self.name = name
        self.message = message
        self.level = level
        self.values = values or []


class FakeDiagnosticArray:
    status = [
        _FakeDiagStatus("motor_driver", "Running", 0, [_FakeKeyValue("rpm", "3000")]),
        _FakeDiagStatus("battery_monitor", "Low battery", 1),
        _FakeDiagStatus("sensor_hub", "Disconnected", 2),
    ]


class FakeTemperature:
    temperature = 42.5


class FakeTemperatureNaN:
    temperature = float("nan")


class FakeRange:
    range = 1.5


class FakeRangeInf:
    range = float("inf")


# ===========================================================================
# Tests
# ===========================================================================

class TestFromLaserScan:
    def test_normal_values(self):
        result = from_laser_scan(FakeLaserScan())
        assert isinstance(result, LaserScan)
        assert result.angle_min == pytest.approx(-3.14159)
        assert result.angle_max == pytest.approx(3.14159)
        assert result.angle_increment == pytest.approx(0.01745)

    def test_inf_nan_replaced(self):
        result = from_laser_scan(FakeLaserScan())
        assert result.ranges[0] == 1.0
        assert result.ranges[1] == 2.0
        assert result.ranges[2] == 0.0  # was inf
        assert result.ranges[3] == 3.0
        assert result.ranges[4] == 0.0  # was nan

    def test_empty_ranges(self):
        msg = type("Scan", (), {
            "ranges": [], "angle_min": 0.0, "angle_max": 0.0, "angle_increment": 0.0
        })()
        result = from_laser_scan(msg)
        assert result.ranges == []


class TestFromImu:
    def test_returns_dict_with_three_keys(self):
        result = from_imu(FakeImu())
        assert set(result.keys()) == {"accel", "gyro", "orientation"}

    def test_accel_values(self):
        result = from_imu(FakeImu())
        assert isinstance(result["accel"], Vector3)
        assert result["accel"].x == pytest.approx(0.1)
        assert result["accel"].y == pytest.approx(0.2)
        assert result["accel"].z == pytest.approx(9.81)

    def test_gyro_values(self):
        result = from_imu(FakeImu())
        assert isinstance(result["gyro"], Vector3)
        assert result["gyro"].x == pytest.approx(0.01)

    def test_orientation_values(self):
        result = from_imu(FakeImu())
        assert isinstance(result["orientation"], Vector3)
        assert result["orientation"].z == pytest.approx(0.707)


class TestFromBatteryState:
    def test_normal_values(self):
        result = from_battery_state(FakeBatteryState())
        assert isinstance(result, Battery)
        assert result.voltage == pytest.approx(12.6)
        assert result.current == pytest.approx(-2.1)
        assert result.soc == pytest.approx(87.5)
        assert result.temperature == pytest.approx(35.0)
        assert result.cell_voltages == [3.15, 3.15, 3.15, 3.15]

    def test_nan_handling(self):
        result = from_battery_state(FakeBatteryStateNaN())
        assert result.voltage == pytest.approx(12.0)
        assert result.current is None
        assert result.soc is None
        assert result.temperature is None
        assert result.cell_voltages is None


class TestFromOdometry:
    def test_pose_extraction(self):
        result = from_odometry(FakeOdometry())
        assert isinstance(result, Pose3D)
        assert result.x == pytest.approx(1.0)
        assert result.y == pytest.approx(2.0)
        assert result.z == pytest.approx(0.5)
        assert result.qz == pytest.approx(0.707)
        assert result.qw == pytest.approx(0.707)


class TestFromOdometryTwist:
    def test_twist_extraction(self):
        result = from_odometry_twist(FakeOdometry())
        assert isinstance(result, Twist)
        assert result.linear_x == pytest.approx(1.0)
        assert result.angular_z == pytest.approx(0.5)


class TestFromTwist:
    def test_normal_values(self):
        result = from_twist(FakeTwist())
        assert isinstance(result, Twist)
        assert result.linear_x == pytest.approx(1.0)
        assert result.angular_z == pytest.approx(0.5)


class TestFromPoseStamped:
    def test_normal_values(self):
        result = from_pose_stamped(FakePoseStamped())
        assert isinstance(result, Pose3D)
        assert result.x == pytest.approx(1.0)
        assert result.qw == pytest.approx(0.707)


class TestFromNavSatFix:
    def test_normal_values(self):
        result = from_nav_sat_fix(FakeNavSatFix())
        assert isinstance(result, GeoLocation)
        assert result.latitude == pytest.approx(37.7749)
        assert result.longitude == pytest.approx(-122.4194)
        assert result.altitude == pytest.approx(10.0)
        assert result.fix_type == "2d"  # STATUS_FIX = 0 maps to "2d"

    def test_accuracy_from_covariance(self):
        result = from_nav_sat_fix(FakeNavSatFix())
        # sqrt((2.0 + 3.0) / 2) = sqrt(2.5) ~ 1.581
        assert result.accuracy == pytest.approx(math.sqrt(2.5))

    def test_no_fix(self):
        result = from_nav_sat_fix(FakeNavSatFixNoFix())
        assert result.fix_type == "none"
        assert result.altitude == 0.0  # NaN -> fallback
        assert result.accuracy is None  # NaN covariance


class TestFromJointState:
    def test_full_state(self):
        result = from_joint_state(FakeJointState())
        assert isinstance(result, JointState)
        assert result.names == ["joint_1", "joint_2", "joint_3"]
        assert result.positions == pytest.approx([0.0, 1.57, -0.5])
        assert result.velocities == pytest.approx([0.1, 0.0, -0.2])
        assert result.efforts == pytest.approx([10.0, 5.0, 8.0])

    def test_minimal_state(self):
        result = from_joint_state(FakeJointStateMinimal())
        assert result.names == ["j1"]
        assert result.positions == [0.5]
        assert result.velocities is None
        assert result.efforts is None


class TestFromImage:
    def test_compress_false_returns_raw(self):
        result = from_image(FakeImage(), compress=False)
        assert isinstance(result, Image)
        assert result.format == "rgb8"
        assert len(result.data) == 4 * 4 * 3

    def test_compress_true_fallback(self):
        """When cv2/PIL unavailable, should return raw with warning."""
        # This test may pass with JPEG if cv2/PIL is installed,
        # or return raw with warning if not. Either way it should not error.
        result = from_image(FakeImage(), compress=True)
        assert isinstance(result, Image)
        assert len(result.data) > 0


class TestFromCompressedImage:
    def test_jpeg(self):
        result = from_compressed_image(FakeCompressedImage())
        assert isinstance(result, Image)
        assert result.format == "jpeg"
        assert result.data == FakeCompressedImage.data

    def test_png_format_parsing(self):
        result = from_compressed_image(FakeCompressedImagePng())
        assert result.format == "png"


class TestFromPointCloud2:
    def test_three_points(self):
        result = from_point_cloud2(FakePointCloud2())
        assert isinstance(result, PointCloud)
        assert len(result.points) == 3
        assert result.points[0] == pytest.approx([1.0, 2.0, 3.0])
        assert result.points[1] == pytest.approx([4.0, 5.0, 6.0])
        assert result.points[2] == pytest.approx([7.0, 8.0, 9.0])

    def test_max_points_subsampling(self):
        result = from_point_cloud2(FakePointCloud2(), max_points=2)
        assert len(result.points) == 2

    def test_empty_cloud(self):
        msg = type("PC2", (), {
            "fields": [_FakePointField("x", 0), _FakePointField("y", 4), _FakePointField("z", 8)],
            "point_step": 12, "height": 1, "width": 0, "row_step": 0, "data": b"",
        })()
        result = from_point_cloud2(msg)
        assert result.points == []


class TestFromPath:
    def test_normal_path(self):
        result = from_path(FakePath())
        assert isinstance(result, Path)
        assert len(result.points) == 3
        assert result.points[0] == [0.0, 0.0, 0.0]
        assert result.points[2] == [2.0, 0.0, 0.0]
        assert result.frame_id == "odom"

    def test_empty_path(self):
        msg = type("Path", (), {"poses": [], "header": type("H", (), {"frame_id": "map"})()})()
        result = from_path(msg)
        assert result.points == []
        assert result.frame_id == "map"


class TestFromDiagnosticArray:
    def test_returns_list_of_logs(self):
        result = from_diagnostic_array(FakeDiagnosticArray())
        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(log, Log) for log in result)

    def test_log_levels(self):
        result = from_diagnostic_array(FakeDiagnosticArray())
        assert result[0].level == "INFO"   # OK = 0
        assert result[1].level == "WARN"   # WARN = 1
        assert result[2].level == "ERROR"  # ERROR = 2

    def test_log_message_content(self):
        result = from_diagnostic_array(FakeDiagnosticArray())
        assert "motor_driver" in result[0].message
        assert "rpm=3000" in result[0].message
        assert "Low battery" in result[1].message


class TestFromTemperature:
    def test_normal_value(self):
        result = from_temperature(FakeTemperature())
        assert isinstance(result, Scalar)
        assert result.value == pytest.approx(42.5)

    def test_nan_value(self):
        result = from_temperature(FakeTemperatureNaN())
        assert result.value == 0.0


class TestFromRange:
    def test_normal_value(self):
        result = from_range(FakeRange())
        assert isinstance(result, Scalar)
        assert result.value == pytest.approx(1.5)

    def test_inf_value(self):
        result = from_range(FakeRangeInf())
        assert result.value == 0.0
