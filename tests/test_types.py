"""Tests for semantic data types."""

from robotrace.types import (
    BoundingBox2D,
    BoundingBox3D,
    DepthImage,
    GeoLocation,
    Image,
    JointState,
    LaserScan,
    Log,
    NumericSet,
    Path,
    PointCloud,
    Pose3D,
    Scalar,
    Transform3D,
    Twist,
    Vector3,
    VectorN,
)


class TestScalar:
    def test_to_dict(self):
        s = Scalar(42.5)
        assert s.to_dict() == {"type": "scalar", "value": 42.5}

    def test_type_name(self):
        assert Scalar(0.0).type_name == "scalar"

    def test_frozen(self):
        s = Scalar(1.0)
        try:
            s.value = 2.0  # type: ignore[misc]
            assert False, "Should raise"
        except AttributeError:
            pass


class TestVector3:
    def test_to_dict(self):
        v = Vector3(1.0, 2.0, 3.0)
        assert v.to_dict() == {"type": "vector3", "x": 1.0, "y": 2.0, "z": 3.0}

    def test_type_name(self):
        assert Vector3(0, 0, 0).type_name == "vector3"


class TestVectorN:
    def test_to_dict(self):
        v = VectorN([0.1, 0.2, 0.3])
        d = v.to_dict()
        assert d["type"] == "vectorn"
        assert d["values"] == [0.1, 0.2, 0.3]

    def test_empty(self):
        v = VectorN([])
        assert v.to_dict()["values"] == []


class TestPose3D:
    def test_defaults(self):
        p = Pose3D(x=1.0, y=2.0, z=0.0)
        assert p.qw == 1.0
        assert p.qx == 0.0

    def test_to_dict(self):
        p = Pose3D(1.0, 2.0, 3.0, qx=0.1, qy=0.2, qz=0.3, qw=0.9)
        d = p.to_dict()
        assert d["type"] == "pose3d"
        assert d["x"] == 1.0
        assert d["qw"] == 0.9


class TestTransform3D:
    def test_to_dict(self):
        t = Transform3D(translation=Vector3(1, 2, 3), rotation=(0.0, 0.0, 0.7, 0.7))
        d = t.to_dict()
        assert d["type"] == "transform3d"
        assert d["translation"]["x"] == 1
        assert d["rotation"] == [0.0, 0.0, 0.7, 0.7]


class TestPointCloud:
    def test_without_colors(self):
        pc = PointCloud(points=[[1, 2, 3], [4, 5, 6]])
        d = pc.to_dict()
        assert d["type"] == "pointcloud"
        assert len(d["points"]) == 2
        assert "colors" not in d

    def test_with_colors(self):
        pc = PointCloud(points=[[1, 2, 3]], colors=[[255, 0, 0]])
        d = pc.to_dict()
        assert d["colors"] == [[255, 0, 0]]


class TestImage:
    def test_to_dict(self):
        img = Image(data=b"\x00\x01\x02", format="png")
        d = img.to_dict()
        assert d["type"] == "image"
        assert d["size_bytes"] == 3
        assert d["format"] == "png"
        assert d["data"] == "AAEC"  # base64 of b"\x00\x01\x02"

    def test_to_dict_roundtrip(self):
        import base64
        raw = b"hello world"
        img = Image(data=raw, format="jpeg")
        d = img.to_dict()
        assert base64.b64decode(d["data"]) == raw


class TestDepthImage:
    def test_to_dict(self):
        di = DepthImage(data=b"\x00" * 100)
        d = di.to_dict()
        assert d["type"] == "depth_image"
        assert d["size_bytes"] == 100
        assert "data" in d  # base64-encoded bytes present

    def test_to_dict_data_roundtrip(self):
        import base64
        raw = b"\x01\x02\x03"
        di = DepthImage(data=raw)
        d = di.to_dict()
        assert base64.b64decode(d["data"]) == raw


class TestLaserScan:
    def test_to_dict(self):
        ls = LaserScan(ranges=[1.0, 2.0, 3.0], angle_min=-1.57, angle_max=1.57, angle_increment=0.01)
        d = ls.to_dict()
        assert d["type"] == "laser_scan"
        assert d["ranges"] == [1.0, 2.0, 3.0]
        assert d["angle_min"] == -1.57


class TestJointState:
    def test_minimal(self):
        js = JointState(names=["j1", "j2"], positions=[0.1, 0.2])
        d = js.to_dict()
        assert d["type"] == "joint_state"
        assert d["names"] == ["j1", "j2"]
        assert "velocities" not in d

    def test_full(self):
        js = JointState(names=["j1"], positions=[0.1], velocities=[0.5], efforts=[1.0])
        d = js.to_dict()
        assert d["velocities"] == [0.5]
        assert d["efforts"] == [1.0]


class TestNumericSet:
    def test_to_dict(self):
        ns = NumericSet({"shoulder": 42.1, "elbow": 38.5})
        d = ns.to_dict()
        assert d["type"] == "numeric_set"
        assert d["values"]["shoulder"] == 42.1


class TestBoundingBox2D:
    def test_to_dict(self):
        bb = BoundingBox2D(x=10, y=20, w=100, h=50, label="person")
        d = bb.to_dict()
        assert d["type"] == "bbox2d"
        assert d["label"] == "person"


class TestBoundingBox3D:
    def test_to_dict(self):
        bb = BoundingBox3D(center=Vector3(1, 2, 3), size=Vector3(0.5, 0.5, 1.0), label="box")
        d = bb.to_dict()
        assert d["type"] == "bbox3d"
        assert d["center"]["x"] == 1
        assert d["label"] == "box"


class TestGeoLocation:
    def test_minimal(self):
        geo = GeoLocation(latitude=47.6062, longitude=-122.3321)
        d = geo.to_dict()
        assert d["type"] == "geolocation"
        assert d["latitude"] == 47.6062
        assert d["longitude"] == -122.3321
        assert d["altitude"] == 0.0
        assert d["fix_type"] == "3d"
        assert "heading" not in d  # Optional fields excluded when None

    def test_full(self):
        geo = GeoLocation(
            latitude=12.9716, longitude=77.5946, altitude=920.5,
            heading=45.0, speed=1.2, accuracy=2.5, altitude_accuracy=5.0,
            fix_type="rtk_fixed",
        )
        d = geo.to_dict()
        assert d["heading"] == 45.0
        assert d["speed"] == 1.2
        assert d["accuracy"] == 2.5
        assert d["altitude_accuracy"] == 5.0
        assert d["fix_type"] == "rtk_fixed"

    def test_frozen(self):
        geo = GeoLocation(latitude=0.0, longitude=0.0)
        try:
            geo.latitude = 1.0
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestPath:
    def test_2d_path(self):
        path = Path(points=[[0, 0], [1, 0], [1, 1], [0, 1]])
        d = path.to_dict()
        assert d["type"] == "path"
        assert d["num_points"] == 4
        assert d["frame_id"] == "map"

    def test_3d_path(self):
        path = Path(points=[[0, 0, 0], [1, 0, 0.5]], frame_id="odom")
        d = path.to_dict()
        assert d["frame_id"] == "odom"
        assert len(d["points"]) == 2

    def test_empty(self):
        path = Path(points=[])
        d = path.to_dict()
        assert d["num_points"] == 0


class TestTwist:
    def test_defaults(self):
        tw = Twist()
        d = tw.to_dict()
        assert d["type"] == "twist"
        assert d["linear_x"] == 0.0
        assert d["angular_z"] == 0.0

    def test_forward_turn(self):
        tw = Twist(linear_x=0.5, angular_z=0.3)
        d = tw.to_dict()
        assert d["linear_x"] == 0.5
        assert d["angular_z"] == 0.3
        assert d["linear_y"] == 0.0  # Others stay zero


class TestLog:
    def test_default_level(self):
        log = Log(message="hello")
        assert log.level == "INFO"
        d = log.to_dict()
        assert d["type"] == "log"
        assert d["message"] == "hello"
