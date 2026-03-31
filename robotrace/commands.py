"""Inbound command channel — receives commands from the RoboTrace server.

The SDK opens a WebSocket connection to the server and dispatches
incoming commands to registered handler callbacks. Runs in a daemon
thread so it doesn't block the robot's control loop.

Usage::

    rt = RoboTrace(...)
    cmd = rt.command_channel()

    @cmd.on("e_stop")
    def handle_estop(payload):
        robot.emergency_stop()

    @cmd.on("set_speed")
    def handle_speed(payload):
        robot.set_max_speed(payload["max_speed"])

    cmd.start()
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("robotrace.commands")


class CommandChannel:
    """Listens for inbound commands from the RoboTrace server via WebSocket.

    Runs a reconnecting WebSocket client in a daemon thread.
    Dispatches commands to registered handler callbacks.

    Parameters
    ----------
    host : str
        RoboTrace server URL (http:// or https://).
    public_key : str
        Project public key for authentication.
    secret_key : str
        Project secret key for authentication.
    device_id : str
        Device identifier to receive commands for.
    """

    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        device_id: str,
    ) -> None:
        self._host = host.rstrip("/")
        self._public_key = public_key
        self._secret_key = secret_key
        self._device_id = device_id
        self._handlers: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._default_handler: Callable[[str, dict[str, Any]], None] | None = None
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._connected = False

    def on(self, command_type: str) -> Callable:
        """Decorator to register a handler for a specific command type.

        Usage::

            @cmd.on("e_stop")
            def handle_estop(payload):
                robot.emergency_stop()
        """
        def decorator(fn: Callable[[dict[str, Any]], None]) -> Callable:
            self._handlers[command_type] = fn
            return fn
        return decorator

    def on_default(self, fn: Callable[[str, dict[str, Any]], None]) -> Callable:
        """Register a default handler for unrecognized command types.

        The handler receives (command_type, payload).
        """
        self._default_handler = fn
        return fn

    @property
    def connected(self) -> bool:
        """True if the WebSocket connection is currently active."""
        return self._connected

    def start(self) -> None:
        """Start the command listener in a daemon thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("CommandChannel already running")
            return

        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._connect_loop,
            daemon=True,
            name="robotrace-command-channel",
        )
        self._thread.start()
        logger.info("CommandChannel started for device %s", self._device_id)

    def stop(self) -> None:
        """Stop the command listener."""
        self._shutdown.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._connected = False
        logger.info("CommandChannel stopped")

    def _get_ticket(self) -> str | None:
        """Get a short-lived WS ticket for WebSocket authentication.

        Uses the /api/v1/ws/ticket endpoint which returns a single-use
        opaque token stored in Redis (30s TTL). This avoids sending
        the JWT in the WebSocket URL where it would appear in logs.
        """
        import httpx
        try:
            resp = httpx.post(
                f"{self._host}/api/v1/ws/ticket",
                auth=(self._public_key, self._secret_key),
                timeout=10.0,
            )
            if resp.status_code == 200:
                return resp.json().get("ticket")
            logger.warning("WS ticket request failed: HTTP %d", resp.status_code)
            return None
        except Exception as e:
            logger.warning("Failed to get WS ticket: %s", e)
            return None

    def _connect_loop(self) -> None:
        """Background thread: connect, receive commands, reconnect with backoff."""
        backoff = 1.0
        max_backoff = 60.0

        while not self._shutdown.is_set():
            try:
                self._run_websocket()
                backoff = 1.0  # Reset on clean disconnect
            except Exception as e:
                logger.warning("CommandChannel connection error: %s", e)
            finally:
                self._connected = False

            if self._shutdown.is_set():
                break

            logger.debug("CommandChannel reconnecting in %.1fs", backoff)
            self._shutdown.wait(timeout=backoff)
            backoff = min(backoff * 2, max_backoff)

    def _run_websocket(self) -> None:
        """Single WebSocket connection session."""
        try:
            import websockets.sync.client as ws_client
        except ImportError:
            logger.error(
                "CommandChannel requires 'websockets' package. "
                "Install with: pip install robotrace-sdk[commands]"
            )
            self._shutdown.set()
            return

        ticket = self._get_ticket()
        if not ticket:
            return

        # Convert http(s) to ws(s)
        ws_url = self._host.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{ws_url}/api/v1/ws/commands/{self._device_id}?token={ticket}"

        with ws_client.connect(url, close_timeout=5) as conn:
            self._connected = True
            logger.info("CommandChannel connected: %s", self._device_id)

            while not self._shutdown.is_set():
                try:
                    msg = conn.recv(timeout=1.0)
                except TimeoutError:
                    continue
                except Exception:
                    break

                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8")

                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    continue

                # Handle ping/pong
                if data.get("action") == "ping":
                    try:
                        conn.send(json.dumps({"action": "pong"}))
                    except Exception:
                        break
                    continue

                # Dispatch command
                cmd_type = data.get("command_type", "")
                cmd_id = data.get("command_id", "")
                payload = data.get("payload", {})

                logger.info("Command received: type=%s id=%s", cmd_type, cmd_id[:8] if cmd_id else "?")

                handler = self._handlers.get(cmd_type)
                if handler:
                    try:
                        handler(payload)
                        # Send ACK
                        if cmd_id:
                            conn.send(json.dumps({"action": "ack", "command_id": cmd_id}))
                    except Exception as e:
                        logger.warning("Command handler error (%s): %s", cmd_type, e)
                elif self._default_handler:
                    try:
                        self._default_handler(cmd_type, payload)
                        if cmd_id:
                            conn.send(json.dumps({"action": "ack", "command_id": cmd_id}))
                    except Exception as e:
                        logger.warning("Default command handler error: %s", e)
                else:
                    logger.warning("No handler for command type: %s", cmd_type)
