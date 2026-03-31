"""RoboTrace ROS2 integration — converters and bridge for ROS2 robots.

Usage:
    # Pattern 1: Use converters in your own node
    from robotrace.ros2 import from_odometry, from_laser_scan
    rt.log("robot/pose", from_odometry(odom_msg))

    # Pattern 2: Run the bridge (zero code changes to robot)
    from robotrace.ros2 import RoboTraceBridge
    bridge = RoboTraceBridge(config_path="bridge.yaml")
    bridge.spin()
"""

from .converters import (
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
from .bridge import RoboTraceBridge

__all__ = [
    "from_laser_scan", "from_imu", "from_battery_state",
    "from_odometry", "from_odometry_twist", "from_twist",
    "from_pose_stamped", "from_nav_sat_fix", "from_joint_state",
    "from_image", "from_compressed_image", "from_point_cloud2",
    "from_path", "from_diagnostic_array", "from_temperature",
    "from_range", "RoboTraceBridge",
]
