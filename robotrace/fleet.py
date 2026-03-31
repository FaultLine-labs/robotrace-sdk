"""Fleet gateway — manages telemetry for multiple robots from a single SDK instance.

Intended for edge gateways, simulation supervisors, and fleet management nodes
where one process handles N robots simultaneously.

Usage::

    fleet = RoboTraceFleet(
        host="http://localhost:8080",
        public_key="rt-pk-xxx",
        secret_key="rt-sk-xxx",
        device_ids=["amr-001", "amr-002", "amr-003"],
    )

    # Per-device operations
    with fleet.mission("amr-001", "deliver_pallet") as m:
        fleet.log("amr-001", "battery/voltage", Scalar(12.4))
        m.score("success", True)

    # Broadcast a command to all devices
    fleet.broadcast_log("fleet/heartbeat", Scalar(1.0))

    fleet.shutdown_all()
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .client import RoboTrace
from .models import Mission

logger = logging.getLogger("robotrace.fleet")


class RoboTraceFleet:
    """Fleet gateway: manages telemetry for N robots from a single SDK instance.

    Each device gets its own ``RoboTrace`` singleton (keyed by
    ``public_key:device_id``), sharing the same project credentials
    but maintaining independent sensor pipelines, score queues, and
    OTel trace providers.

    Parameters
    ----------
    host : str
        RoboTrace server URL.
    public_key : str
        Project public key for authentication.
    secret_key : str
        Project secret key for authentication.
    device_ids : list[str]
        Initial list of device identifiers to manage.
    environment : str
        Deployment environment (production, staging, sim).
    on_error : callable, optional
        Error callback shared across all device clients.
    **kwargs
        Additional keyword arguments passed to each ``RoboTrace`` instance
        (e.g., ``release``, ``sample_rate``, ``enabled``).
    """

    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        device_ids: list[str] | None = None,
        environment: str = "production",
        on_error: Any = None,
        **kwargs: Any,
    ) -> None:
        self._host = host
        self._public_key = public_key
        self._secret_key = secret_key
        self._environment = environment
        self._on_error = on_error
        self._kwargs = kwargs
        self._lock = threading.RLock()
        self._clients: dict[str, RoboTrace] = {}

        for device_id in (device_ids or []):
            self._create_client(device_id)

        logger.info(
            "RoboTraceFleet initialized: %d devices, host=%s",
            len(self._clients), host,
        )

    def _create_client(self, device_id: str) -> RoboTrace:
        """Create and register a RoboTrace client for a device."""
        client = RoboTrace(
            host=self._host,
            public_key=self._public_key,
            secret_key=self._secret_key,
            device_id=device_id,
            environment=self._environment,
            on_error=self._on_error,
            **self._kwargs,
        )
        self._clients[device_id] = client
        return client

    @property
    def device_ids(self) -> list[str]:
        """List of managed device IDs."""
        return list(self._clients.keys())

    @property
    def device_count(self) -> int:
        """Number of managed devices."""
        return len(self._clients)

    def device(self, device_id: str) -> RoboTrace:
        """Get the RoboTrace client for a specific device.

        Raises KeyError if the device is not registered.
        """
        with self._lock:
            if device_id not in self._clients:
                raise KeyError(f"Device '{device_id}' not in fleet. Registered: {self.device_ids}")
            return self._clients[device_id]

    def register_device(self, device_id: str, **kwargs: Any) -> RoboTrace:
        """Dynamically add a device to the fleet.

        For discovery-based scenarios where devices appear at runtime.
        Returns the new RoboTrace client.
        """
        with self._lock:
            if device_id in self._clients:
                return self._clients[device_id]
            merged_kwargs = {**self._kwargs, **kwargs}
            client = RoboTrace(
                host=self._host,
                public_key=self._public_key,
                secret_key=self._secret_key,
                device_id=device_id,
                environment=self._environment,
                on_error=self._on_error,
                **merged_kwargs,
            )
            self._clients[device_id] = client
        logger.info("Fleet: registered new device %s (total: %d)", device_id, len(self._clients))
        return client

    def remove_device(self, device_id: str) -> None:
        """Remove a device from the fleet and shut down its client."""
        import atexit
        with self._lock:
            client = self._clients.pop(device_id, None)
        if client:
            client.shutdown()
            atexit.unregister(client.shutdown)
            logger.info("Fleet: removed device %s (total: %d)", device_id, len(self._clients))

    def mission(self, device_id: str, name: str, **kwargs: Any) -> Mission:
        """Start a mission on a specific device.

        Usage::

            with fleet.mission("amr-001", "deliver_pallet") as m:
                m.log("sensors/battery", Scalar(87.5))
        """
        return self.device(device_id).mission(name, **kwargs)

    def log(self, device_id: str, path: str, data: Any, **kwargs: Any) -> None:
        """Log telemetry for a specific device."""
        self.device(device_id).log(path, data, **kwargs)

    def score(self, device_id: str, name: str, value: Any, **kwargs: Any) -> None:
        """Record a score for a specific device."""
        self.device(device_id).score(name, value, **kwargs)

    def broadcast_log(self, path: str, data: Any, **kwargs: Any) -> None:
        """Log the same telemetry data to ALL devices in the fleet."""
        with self._lock:
            clients = list(self._clients.items())
        for device_id, client in clients:
            try:
                client.log(path, data, **kwargs)
            except Exception as e:
                logger.warning("Fleet: broadcast_log failed for %s: %s", device_id, e)

    def broadcast_event(self, name: str, data: dict[str, Any] | None = None, severity: str = "info") -> None:
        """Record the same event on ALL devices in the fleet."""
        with self._lock:
            clients = list(self._clients.items())
        for device_id, client in clients:
            try:
                client.event(name, data, severity)
            except Exception as e:
                logger.warning("Fleet: broadcast_event failed for %s: %s", device_id, e)

    def health_all(self) -> dict[str, dict[str, Any]]:
        """Get health snapshots for all devices.

        Returns a dict keyed by device_id.
        """
        with self._lock:
            clients = dict(self._clients)
        return {
            device_id: client.health()
            for device_id, client in clients.items()
        }

    def flush_all(self) -> None:
        """Flush all pending data across all devices."""
        with self._lock:
            clients = list(self._clients.values())
        for client in clients:
            client.flush()

    def shutdown_all(self) -> None:
        """Shut down all device clients gracefully."""
        import atexit
        with self._lock:
            clients = list(self._clients.items())
            self._clients.clear()
        for device_id, client in clients:
            try:
                client.shutdown()
                atexit.unregister(client.shutdown)
            except Exception as e:
                logger.warning("Fleet: shutdown failed for %s: %s", device_id, e)
        logger.info("RoboTraceFleet shutdown complete")

    def __enter__(self) -> "RoboTraceFleet":
        return self

    def __exit__(self, *_: Any) -> None:
        self.shutdown_all()

    def __repr__(self) -> str:
        return (
            f"RoboTraceFleet(host={self._host!r}, devices={self.device_ids}, "
            f"environment={self._environment!r})"
        )
