"""Component — a physical sensor/actuator on a device."""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from robotrace.client import RoboTrace

logger = logging.getLogger(__name__)


class Component:
    """A registered hardware component that logs telemetry attributed to itself.

    Usage:
        lidar = rt.component("lidar-front", type="lidar", model="VLP-16", manufacturer="Velodyne")
        lidar.log(scan_data)  # -> stream="lidar-front", sensor_id="lidar-front"
    """

    def __init__(
        self,
        client: "RoboTrace",
        component_id: str,
        component_type: str = "sensor",
        name: str | None = None,
        manufacturer: str | None = None,
        model: str | None = None,
        serial_number: str | None = None,
        firmware_version: str | None = None,
    ):
        self._client = client
        self.id = component_id
        self.type = component_type
        self.name = name
        self.manufacturer = manufacturer
        self.model = model
        self.serial_number = serial_number
        self.firmware_version = firmware_version

        # Register component on server (non-blocking)
        self._register()

    def _register(self) -> None:
        """Register this component on the server."""
        if not self._client._http or not self._client._device_id:
            return
        try:
            self._client._http.post(
                f"{self._client._host}/api/v1/devices/{self._client._device_id}/components",
                json={
                    "component_id": self.id,
                    "component_type": self.type,
                    "name": self.name,
                    "manufacturer": self.manufacturer,
                    "model": self.model,
                    "serial_number": self.serial_number,
                    "firmware_version": self.firmware_version,
                },
            )
            logger.debug("Component registered: %s (%s)", self.id, self.type)
        except Exception as e:
            logger.debug("Component registration skipped: %s", e)

    def log(self, value: Any, timestamp: float | None = None) -> None:
        """Log telemetry through this component."""
        if timestamp is not None and self._client._sensor_pipeline is not None:
            self._client._sensor_pipeline.log(
                stream=self.id,
                value=value,
                timestamp=timestamp,
                sensor_id=self.id,
            )
        else:
            self._client.log(
                path=self.id,
                data=value,
                sensor_id=self.id,
            )

    def score(self, name: str, value: float | bool | str, **kwargs) -> None:
        """Score attributed to this component."""
        self._client.score(name, value, **kwargs)
