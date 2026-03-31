# Changelog

All notable changes to the `robotrace-sdk` package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-03-30

### Added

#### Core SDK
- `RoboTrace` client with OTel-native mission/phase tracing
- 20 typed sensor data types: Scalar, Vector3, VectorN, Pose3D, Transform3D, PointCloud, Image, DepthImage, Video, LaserScan, JointState, NumericSet, BoundingBox2D, BoundingBox3D, GeoLocation, Path, Twist, Log, Battery, Bitset
- `rt.log()` — high-frequency sensor telemetry logging with batched HTTP flush
- `rt.mission()` / `m.phase()` — structured task lifecycle tracking (OTel spans)
- `rt.score()` — numeric, boolean, and categorical mission quality metrics
- `rt.event()` / `rt.decision()` — point-in-time records
- `rt.component()` — device hardware hierarchy (sensor/actuator binding)
- `rt.configure_stream()` — declare stream metadata (units, ranges, thresholds)
- `rt.health()` — pipeline health snapshot (buffered count, offline queue status)
- `on_error` callback parameter — notified on telemetry/score delivery failures
- `@mission()` and `@phase()` decorators with async support
- SQLite offline queue — survives crashes and power loss (WAL mode, configurable limits)
- Multi-timeline support — `rt.set_time("sensor_clock", timestamp=ns)`
- MCAP dual-output recording — `rt.start_recording("out.mcap")`
- Background flush threads — non-blocking, never stalls robot control loops

#### Fleet & Commands
- `RoboTraceFleet` — manage telemetry for N robots from one process
- `CommandChannel` — bidirectional WebSocket for server-to-device commands
- `StreamingCamera` — MJPEG frame publishing

#### ROS2 Integration (`robotrace.ros2`)
- 16 pure converter functions (duck-typed, no rclpy dependency):
  - `from_laser_scan`, `from_imu`, `from_battery_state`, `from_odometry`, `from_odometry_twist`, `from_twist`, `from_pose_stamped`, `from_nav_sat_fix`, `from_joint_state`, `from_image`, `from_compressed_image`, `from_point_cloud2`, `from_path`, `from_diagnostic_array`, `from_temperature`, `from_range`
- `RoboTraceBridge` — configurable bridge node (YAML config, per-topic rate limiting)
- Lazy imports — `import robotrace` never triggers rclpy import
- `python -m robotrace.ros2 --config bridge.yaml` entry point

#### Infrastructure
- MIT License
- `py.typed` marker for PEP 561
- 242 tests (109 core + 34 ROS2 converters + 97 module tests + 2 integration)
- Published to PyPI: `pip install robotrace-sdk`

### Environment Variables
- `ROBOTRACE_HOST` — server URL fallback
- `ROBOTRACE_DEBUG` — enable debug logging
- `ROBOTRACE_LOG_LEVEL` — log level (DEBUG/INFO/WARNING/ERROR)
- `ROBOTRACE_LOG_FILE` — file logging path
- `ROBOTRACE_DATA_DIR` — offline queue directory (default: `~/.robotrace/`)
- `ROBOTRACE_OFFLINE_MAX_MB` — max offline queue size
- `ROBOTRACE_OFFLINE_MAX_HOURS` — max offline queue age

[2.0.0]: https://github.com/FaultLine-labs/robotrace-sdk/releases/tag/v2.0.0
