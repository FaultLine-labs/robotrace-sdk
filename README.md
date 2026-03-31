# RoboTrace SDK

OTel-native Python SDK for robot observability. Mission tracing, sensor telemetry, evaluation scoring -- think Langfuse, but for robots.

## Install

```bash
pip install robotrace-sdk
```

Optional extras:
```bash
pip install robotrace-sdk[mcap]      # MCAP recording
pip install robotrace-sdk[commands]  # Inbound command channel (WebSocket)
```

## Quick Start

```python
from robotrace import RoboTrace, Scalar, Pose3D, Battery

rt = RoboTrace(
    host="https://your-robotrace-server.com",
    public_key="rt-pk-xxx",
    secret_key="rt-sk-xxx",
    device_id="amr-001",
)

# Log sensor telemetry
rt.log("robot/pose", Pose3D(x=1.0, y=2.0, z=0.0))
rt.log("sensors/battery", Battery(voltage=12.6, soc=0.875, current=-2.1))

# Track missions
with rt.mission("deliver_pallet", tags=["zone-B"]) as m:
    with m.phase("navigate"):
        rt.log("robot/pose", Pose3D(x=5.0, y=3.0, z=0.0))
    m.score("success", True)

rt.shutdown()
```

## ROS2 Integration

Connect to any ROS2 robot with zero code changes (bridge) or 3 lines (converters):

### Converters (in your code)

```python
from robotrace.ros2 import from_odometry, from_laser_scan, from_battery_state

# In your ROS2 callback:
rt.log("robot/pose", from_odometry(odom_msg))
rt.log("sensors/lidar", from_laser_scan(scan_msg))
rt.log("sensors/battery", from_battery_state(battery_msg))
```

### Bridge (zero code changes)

```yaml
# bridge.yaml
robotrace:
  host: https://your-server.com
  public_key: rt-pk-xxx
  secret_key: rt-sk-xxx
  device_id: my-robot
topics:
  /scan: {stream: sensors/lidar, type: LaserScan}
  /odom: {stream: robot/pose, type: Odometry}
  /battery_state: {stream: sensors/battery, type: BatteryState}
```

```bash
python -m robotrace.ros2 --config bridge.yaml
```

16 converters included: LaserScan, Imu, BatteryState, Odometry, Twist, PoseStamped, NavSatFix, JointState, Image, CompressedImage, PointCloud2, Path, DiagnosticArray, Temperature, Range.

## Stream Configuration

Tell the platform what your data means (units, ranges, thresholds):

```python
rt.configure_stream("sensors/battery", {
    "display_name": "Main Battery",
    "data_type": "battery",
    "fields": {
        "soc": {"unit": "ratio", "scale": 100, "display_unit": "%"},
        "voltage": {"unit": "V", "range": [10.0, 14.8]},
        "temperature": {"unit": "C", "warning_above": 60},
    },
    "rate_hz": 1,
})
```

## Decorator Pattern

```python
from robotrace import mission, phase

@mission(name="deliver_pallet")
def deliver(destination):
    navigate_to(destination)
    pick_up()
    return {"status": "delivered"}

@phase()
def navigate_to(destination):
    rt.log("robot/pose", Pose3D(x=1.0, y=2.0, z=0.0))

# Works with async too
@mission()
async def async_delivery(destination):
    await navigate_async(destination)
```

## Context Manager Pattern

```python
with rt.mission("pick_and_place", tags=["priority-high"]) as m:
    with m.phase("navigate") as nav:
        nav.log("robot/pose", Pose3D(x=1.0, y=2.0, z=0.0))
        nav.event("obstacle_detected", {"distance_m": 0.5})
    with m.phase("pick") as pick:
        pick.decision("grasp_planner", model="graspnet-v2",
                      input={"target": [0, 0, 0]}, output={"confidence": 0.95})
    m.score("cycle_time_s", 14.2)
    m.score("success", True)
```

## Data Types

20 typed sensor data types:

| Type | Description |
|------|-------------|
| `Scalar(value)` | Single numeric value |
| `Vector3(x, y, z)` | 3D vector |
| `VectorN(values)` | N-dimensional vector |
| `Pose3D(x, y, z, qx, qy, qz, qw)` | 6DOF pose with quaternion |
| `Transform3D(translation, rotation)` | Coordinate transform |
| `PointCloud(points, colors)` | Nx3 point cloud |
| `Image(data, format)` | Compressed image (JPEG/PNG) |
| `DepthImage(data)` | Depth map |
| `Video(data, format)` | Video clip |
| `LaserScan(ranges, angle_min, angle_max, angle_increment)` | 2D LiDAR scan |
| `JointState(names, positions, velocities, efforts)` | Multi-DOF joint state |
| `NumericSet(values)` | Named numeric values |
| `GeoLocation(latitude, longitude, altitude, heading, speed)` | GPS/GNSS position |
| `Path(points, frame_id)` | Trajectory waypoints |
| `Twist(linear_x/y/z, angular_x/y/z)` | 6DOF velocity |
| `BoundingBox2D(x, y, w, h, label)` | 2D detection |
| `BoundingBox3D(center, size, label)` | 3D detection |
| `Log(message, level)` | Text log message |
| `Battery(voltage, current, soc, health, temperature)` | Battery state |
| `Bitset(value, width, labels)` | Hardware status register |

## Fleet Gateway

Manage telemetry for multiple robots from one process:

```python
from robotrace import RoboTraceFleet

fleet = RoboTraceFleet(
    host="https://your-server.com",
    public_key="rt-pk-xxx",
    secret_key="rt-sk-xxx",
    device_ids=["amr-001", "amr-002", "amr-003"],
)

with fleet.mission("amr-001", "deliver_pallet") as m:
    fleet.log("amr-001", "sensors/battery", Battery(voltage=12.4))
    m.score("success", True)

fleet.broadcast_log("fleet/heartbeat", Scalar(1.0))
fleet.shutdown_all()
```

## Inbound Commands

Receive commands from the server (requires `pip install robotrace-sdk[commands]`):

```python
cmd = rt.command_channel()

@cmd.on("e_stop")
def handle_estop(payload):
    robot.emergency_stop()

@cmd.on("set_speed")
def handle_speed(payload):
    robot.set_max_speed(payload["max_speed"])

cmd.start()
```

## MCAP Recording

Dual-output: telemetry goes to both the server AND a local MCAP file (requires `pip install robotrace-sdk[mcap]`):

```python
rt.start_recording("mission.mcap")
# All subsequent log() calls also write to the MCAP file
rt.stop_recording()
```

## Pipeline Health

```python
health = rt.health()
# {'buffered_count': 5, 'offline_count': 0, 'stats': {...}, 'is_recording': False}

rt = RoboTrace(..., on_error=lambda ctx, exc: print(f"Error in {ctx}: {exc}"))
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ROBOTRACE_HOST` | (none) | Server URL (fallback if `host` not set) |
| `ROBOTRACE_DEBUG` | `false` | Enable debug logging to stderr |
| `ROBOTRACE_LOG_LEVEL` | `WARNING` | Log level: DEBUG, INFO, WARNING, ERROR |
| `ROBOTRACE_LOG_FILE` | (none) | Write SDK logs to this file |
| `ROBOTRACE_DATA_DIR` | `~/.robotrace` | Directory for offline queue database |
| `ROBOTRACE_OFFLINE_MAX_MB` | `100` | Max offline queue size in MB |
| `ROBOTRACE_OFFLINE_MAX_HOURS` | `72` | Max offline queue age in hours |

## Development

```bash
pip install -e ".[dev]"
pytest                        # 242 tests
pytest tests/test_ros2_converters.py  # ROS2 converter tests only
```

## License

MIT
