"""Tests for StreamingCamera — live JPEG frame publishing."""

import time
from unittest.mock import MagicMock

import pytest

from robotrace.streaming import StreamingCamera


class MockClient:
    """Mock RoboTrace client for StreamingCamera tests."""

    def __init__(self, with_http=False):
        self._host = "http://fake:8080"
        self._shutdown_called = False
        if with_http:
            self._http = MagicMock()
            # Default: successful publish
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            self._http.post.return_value = mock_resp
        else:
            self._http = None


class TestStreamingCamera:
    """Tests for StreamingCamera class."""

    def test_construction_sets_camera_id(self):
        client = MockClient()
        cam = StreamingCamera(client, camera_id="front_camera")
        assert cam.camera_id == "front_camera"

    def test_construction_default_camera_id(self):
        client = MockClient()
        cam = StreamingCamera(client)
        assert cam.camera_id == "default"

    def test_construction_sets_max_fps(self):
        client = MockClient()
        cam = StreamingCamera(client, max_fps=30.0)
        assert cam._min_interval == pytest.approx(1.0 / 30.0)

    def test_start_sets_running_and_resets_frame_count(self):
        client = MockClient()
        cam = StreamingCamera(client)

        # Manually set frame_count to a non-zero value
        cam._frame_count = 10
        cam.start()

        assert cam._running is True
        assert cam._frame_count == 0

    def test_stop_sets_running_false(self):
        client = MockClient()
        cam = StreamingCamera(client)
        cam.start()
        cam.stop()
        assert cam._running is False

    def test_publish_frame_returns_false_when_not_running(self):
        client = MockClient(with_http=True)
        cam = StreamingCamera(client)
        # Not started
        result = cam.publish_frame(b"\xff\xd8frame_data")
        assert result is False

    def test_publish_frame_returns_false_when_no_http(self):
        client = MockClient(with_http=False)
        cam = StreamingCamera(client)
        cam.start()
        result = cam.publish_frame(b"\xff\xd8frame_data")
        assert result is False

    def test_publish_frame_succeeds_with_http(self):
        client = MockClient(with_http=True)
        cam = StreamingCamera(client, max_fps=1000)  # High fps to avoid rate limiting
        cam.start()

        result = cam.publish_frame(b"\xff\xd8frame_data")
        assert result is True
        assert cam.frame_count == 1

    def test_publish_frame_rate_limits(self):
        client = MockClient(with_http=True)
        cam = StreamingCamera(client, max_fps=1.0)  # 1 fps = 1s interval
        cam.start()

        # First frame should succeed
        result1 = cam.publish_frame(b"\xff\xd8frame1")
        assert result1 is True

        # Immediate second frame should be rate-limited
        result2 = cam.publish_frame(b"\xff\xd8frame2")
        assert result2 is False

        # Frame count should be 1 (only first succeeded)
        assert cam.frame_count == 1

    def test_frame_count_increments(self):
        client = MockClient(with_http=True)
        cam = StreamingCamera(client, max_fps=10000)  # Very high fps
        cam.start()

        for i in range(5):
            cam.publish_frame(b"\xff\xd8frame")
            # Advance time to avoid rate limiting
            cam._last_frame_time = 0.0

        assert cam.frame_count == 5

    def test_camera_id_property(self):
        client = MockClient()
        cam = StreamingCamera(client, camera_id="rear_camera")
        assert cam.camera_id == "rear_camera"

    def test_shutdown_graceful_client_shutdown(self):
        """When client._shutdown_called is True, publish_frame returns False."""
        client = MockClient(with_http=True)
        cam = StreamingCamera(client, max_fps=10000)
        cam.start()

        # First frame should work
        result1 = cam.publish_frame(b"\xff\xd8frame")
        assert result1 is True

        # Simulate client shutdown
        client._shutdown_called = True
        cam._last_frame_time = 0.0  # Reset rate limiter

        result2 = cam.publish_frame(b"\xff\xd8frame2")
        assert result2 is False

    def test_publish_frame_handles_http_exception(self):
        client = MockClient(with_http=True)
        client._http.post.side_effect = Exception("connection refused")
        cam = StreamingCamera(client, max_fps=10000)
        cam.start()

        result = cam.publish_frame(b"\xff\xd8frame")
        assert result is False
        assert cam.frame_count == 0

    def test_publish_frame_non_200_status(self):
        client = MockClient(with_http=True)
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        client._http.post.return_value = mock_resp

        cam = StreamingCamera(client, max_fps=10000)
        cam.start()

        result = cam.publish_frame(b"\xff\xd8frame")
        assert result is False
        assert cam.frame_count == 0

    def test_start_stop_start_resets_frame_count(self):
        client = MockClient(with_http=True)
        cam = StreamingCamera(client, max_fps=10000)

        cam.start()
        cam.publish_frame(b"\xff\xd8frame")
        assert cam.frame_count == 1

        cam.stop()
        cam.start()
        assert cam.frame_count == 0
