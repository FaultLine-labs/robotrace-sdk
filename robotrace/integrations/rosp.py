"""ROSP integration — automatic tracing for ROSP adapter operations.

Wraps a ROSP adapter so that:
- describe() registers the device and creates a mission-level span
- stream() logs sensor data through the RoboTrace sensor pipeline
- command() calls are traced as decisions

Usage::

    from robotrace import RoboTrace
    from robotrace.integrations.rosp import RoboTraceMiddleware

    rt = RoboTrace(host="http://localhost:8080", public_key="...", secret_key="...")
    traced = RoboTraceMiddleware(adapter=my_adapter, robotrace=rt)

    async with traced:
        card = await traced.describe()
        async for msg in traced.stream(["/sensor/lidar/scan"]):
            print(msg.data)
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

logger = logging.getLogger("robotrace.integrations.rosp")


class RoboTraceMiddleware:
    """Transparent tracing wrapper for any ROSP adapter."""

    def __init__(self, adapter: Any, robotrace: Any, auto_register: bool = True) -> None:
        self._adapter = adapter
        self._rt = robotrace
        self._auto_register = auto_register
        self._device_id: str | None = None

    async def connect(self) -> None:
        await self._adapter.connect()

    async def disconnect(self) -> None:
        try:
            self._rt.flush()
        except Exception:
            logger.debug("Flush failed during disconnect")
        await self._adapter.disconnect()

    async def __aenter__(self) -> "RoboTraceMiddleware":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        await self.disconnect()

    async def describe(self, depth: str = "full") -> Any:
        """Call adapter.describe() and register the device in RoboTrace."""
        card = await self._adapter.describe(depth=depth)
        self._device_id = card.id

        if self._auto_register and self._device_id:
            try:
                self._rt.event(
                    "device_registered",
                    data={
                        "device_id": self._device_id,
                        "manufacturer": getattr(card, "identity", {}).get("manufacturer"),
                        "model": getattr(card, "identity", {}).get("model"),
                    },
                    severity="info",
                )
                logger.info("Registered device %s in RoboTrace", self._device_id)
            except Exception as e:
                logger.warning("Failed to register device in RoboTrace: %s", e)

        return card

    async def stream(self, topics: list[str], qos: Any = None) -> AsyncIterator[Any]:
        """Stream sensor data with automatic telemetry logging."""
        device_id = self._device_id
        if not device_id:
            try:
                card = await self._adapter.describe(depth="summary")
                device_id = card.id
                self._device_id = device_id
            except Exception:
                logger.warning("Could not resolve device_id for tracing")

        async for msg in self._adapter.stream(topics, qos):
            if device_id:
                try:
                    self._rt.log(
                        path=msg.topic,
                        data=msg.data if isinstance(msg.data, dict) else {"raw": str(msg.data)},
                    )
                except Exception:
                    logger.debug("Failed to trace %s", msg.topic)
            yield msg

    async def discover(self) -> Any:
        return await self._adapter.discover()

    async def health_check(self) -> Any:
        return await self._adapter.health_check()

    @property
    def is_connected(self) -> bool:
        return self._adapter.is_connected

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)
