"""RoboTrace SDK v2 — OTel-native robot observability.

Quick start::

    from robotrace import RoboTrace, mission, phase, Scalar, Pose3D

    rt = RoboTrace(
        host="http://localhost:8080",
        public_key="rt-pk-xxx",
        secret_key="rt-sk-xxx",
        device_id="urn:rosp:device:robotis:turtlebot3:001",
    )

    with rt.mission("deliver_pallet", tags=["zone-B"]) as m:
        with m.phase("navigate") as nav:
            nav.log("robot/pose", Pose3D(x=1.0, y=2.0, z=0.0))
            nav.log("sensors/battery", Scalar(87.5))
        m.score("success", True)
"""

import logging as _logging

# Python library best practice: NullHandler prevents "No handler found" warnings
# when the application doesn't configure logging. Users configure their own handlers.
_logging.getLogger("robotrace").addHandler(_logging.NullHandler())

# Single version source — read from installed package metadata, fallback to hardcoded.
# Must be defined before submodule imports (client.py and decorators.py reference it).
try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("robotrace-sdk")
except Exception:
    __version__ = "2.0.0"

from .client import RoboTrace
from .commands import CommandChannel
from .component import Component
from .fleet import RoboTraceFleet
from .decorators import mission, phase
from .recorder import McapRecorder, mcap_available
from .streaming import StreamingCamera
from .types import (
    Battery,
    Bitset,
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
    Video,
)

__all__ = [
    "RoboTrace",
    "RoboTraceFleet",
    "CommandChannel",
    "Component",
    "mission",
    "phase",
    "McapRecorder",
    "mcap_available",
    "StreamingCamera",
    # Timeline values are set via rt.set_time() / rt.reset_time() on the RoboTrace instance
    "Scalar",
    "Vector3",
    "VectorN",
    "Pose3D",
    "Transform3D",
    "PointCloud",
    "Image",
    "DepthImage",
    "LaserScan",
    "JointState",
    "NumericSet",
    "GeoLocation",
    "Path",
    "Twist",
    "BoundingBox2D",
    "BoundingBox3D",
    "Video",
    "Log",
    "Battery",
    "Bitset",
]

