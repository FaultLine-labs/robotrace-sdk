"""Tests for CommandChannel — inbound command handling via WebSocket."""

import threading
from unittest.mock import MagicMock

import pytest

from robotrace.commands import CommandChannel


class TestCommandChannel:
    """Tests for CommandChannel class (no WebSocket connection needed)."""

    def _make_channel(self):
        return CommandChannel(
            host="http://fake:8080",
            public_key="pk-test",
            secret_key="sk-test",
            device_id="test-device-001",
        )

    def test_construction_stores_parameters(self):
        ch = self._make_channel()
        assert ch._host == "http://fake:8080"
        assert ch._public_key == "pk-test"
        assert ch._secret_key == "sk-test"
        assert ch._device_id == "test-device-001"

    def test_host_trailing_slash_stripped(self):
        ch = CommandChannel(
            host="http://fake:8080/",
            public_key="pk", secret_key="sk", device_id="d",
        )
        assert ch._host == "http://fake:8080"

    def test_on_decorator_registers_handler(self):
        ch = self._make_channel()

        @ch.on("e_stop")
        def handle_estop(payload):
            pass

        assert "e_stop" in ch._handlers
        assert ch._handlers["e_stop"] is handle_estop

    def test_on_decorator_multiple_commands(self):
        ch = self._make_channel()

        @ch.on("e_stop")
        def h1(payload):
            pass

        @ch.on("set_speed")
        def h2(payload):
            pass

        assert len(ch._handlers) == 2
        assert "e_stop" in ch._handlers
        assert "set_speed" in ch._handlers

    def test_on_default_registers_default_handler(self):
        ch = self._make_channel()

        @ch.on_default
        def fallback(cmd_type, payload):
            pass

        assert ch._default_handler is fallback

    def test_connected_is_false_before_start(self):
        ch = self._make_channel()
        assert ch.connected is False

    def test_start_creates_daemon_thread(self):
        ch = self._make_channel()

        # Patch _connect_loop so it doesn't actually connect
        ch._connect_loop = lambda: None

        ch.start()

        assert ch._thread is not None
        assert ch._thread.daemon is True
        assert ch._thread.name == "robotrace-command-channel"

        # Cleanup
        ch._shutdown.set()
        ch._thread.join(timeout=2)

    def test_stop_sets_shutdown_event(self):
        ch = self._make_channel()
        # Don't actually start, just set the event state
        ch._shutdown.clear()
        assert not ch._shutdown.is_set()

        ch.stop()

        assert ch._shutdown.is_set()
        assert ch.connected is False

    def test_start_twice_does_not_create_second_thread(self):
        ch = self._make_channel()
        ch._connect_loop = lambda: ch._shutdown.wait()

        ch.start()
        first_thread = ch._thread

        ch.start()  # Should warn and return
        assert ch._thread is first_thread

        # Cleanup
        ch._shutdown.set()
        first_thread.join(timeout=2)

    def test_handlers_dict_empty_initially(self):
        ch = self._make_channel()
        assert ch._handlers == {}
        assert ch._default_handler is None

    def test_on_decorator_returns_original_function(self):
        ch = self._make_channel()

        def my_handler(payload):
            return "handled"

        result = ch.on("test_cmd")(my_handler)
        assert result is my_handler
        # The original function should still be callable
        assert result({"key": "value"}) == "handled"

    def test_on_default_returns_original_function(self):
        ch = self._make_channel()

        def my_default(cmd_type, payload):
            return "default"

        result = ch.on_default(my_default)
        assert result is my_default
