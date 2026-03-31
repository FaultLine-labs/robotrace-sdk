"""RoboTrace semantic convention constants for OpenTelemetry."""

# Resource attributes (robot identity)
ROBOT_ID = "robot.id"
ROBOT_TYPE = "robot.type"
ROBOT_MANUFACTURER = "robot.manufacturer"
ROBOT_MODEL = "robot.model"
ROBOT_FIRMWARE_VERSION = "robot.firmware_version"
ROBOT_FLEET = "robot.fleet"

# Mission attributes (trace-level)
MISSION_NAME = "robotrace.mission.name"
MISSION_STATUS = "robotrace.mission.status"
MISSION_TAGS = "robotrace.mission.tags"
SHIFT_ID = "robotrace.shift.id"

# Phase attributes (span-level)
PHASE_TYPE = "robotrace.phase.type"
PHASE_COMMAND = "robotrace.phase.command"

# Decision attributes
DECISION_MODEL = "robotrace.decision.model"
DECISION_INPUT = "robotrace.decision.input"
DECISION_OUTPUT = "robotrace.decision.output"
DECISION_CONFIDENCE = "robotrace.decision.confidence"

# Event attributes
EVENT_NAME = "robotrace.event.name"
EVENT_SEVERITY = "robotrace.event.severity"

# Score attributes
SCORE_NAME = "robotrace.score.name"
SCORE_VALUE = "robotrace.score.value"
SCORE_SOURCE = "robotrace.score.source"
SCORE_COMMENT = "robotrace.score.comment"

# Telemetry attributes
TELEMETRY_STREAM = "robotrace.telemetry.stream"
TELEMETRY_SENSOR_ID = "robotrace.telemetry.sensor_id"

# SDK metadata
SDK_NAME = "robotrace.sdk.name"
SDK_VERSION = "robotrace.sdk.version"
ENVIRONMENT = "robotrace.environment"
RELEASE = "robotrace.release"
