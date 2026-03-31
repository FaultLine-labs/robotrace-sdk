"""Live camera streaming -- publishes JPEG frames to the RoboTrace server."""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class StreamingCamera:
    """Publishes live camera frames to the RoboTrace server.

    Usage::

        camera = StreamingCamera(rt_client, camera_id="front_camera")
        camera.start()
        while capturing:
            frame = capture_frame()  # JPEG bytes
            camera.publish_frame(frame)
        camera.stop()
    """

    def __init__(
        self,
        client: Any,  # RoboTrace client instance
        camera_id: str = "default",
        max_fps: float = 15.0,
    ):
        self._client = client
        self._camera_id = camera_id
        self._min_interval = 1.0 / max_fps
        self._last_frame_time = 0.0
        self._running = False
        self._frame_count = 0

    @property
    def camera_id(self) -> str:
        return self._camera_id

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def publish_frame(self, jpeg_bytes: bytes) -> bool:
        """Publish a JPEG frame. Rate-limited to max_fps.

        Returns True if frame was sent, False if rate-limited/skipped.
        """
        if not self._running:
            return False

        now = time.monotonic()
        if now - self._last_frame_time < self._min_interval:
            return False  # Rate-limited

        self._last_frame_time = now

        try:
            # Guard against client shutdown (._http set to None or closed)
            http = getattr(self._client, '_http', None)
            host = getattr(self._client, '_host', '')
            if not http or not host or getattr(self._client, '_shutdown_called', False):
                return False
            resp = http.post(
                f"{host}/api/v1/stream/{self._camera_id}/frame",
                content=jpeg_bytes,
                headers={"Content-Type": "image/jpeg"},
            )
            if resp.status_code == 200:
                self._frame_count += 1
                return True
        except Exception as e:
            logger.debug("Frame publish failed: %s", e)

        return False

    def start(self) -> None:
        """Start the camera stream."""
        self._running = True
        self._frame_count = 0
        logger.info(
            "Camera stream started: %s (max %.0f fps)",
            self._camera_id,
            1.0 / self._min_interval,
        )

    def stop(self) -> None:
        """Stop the camera stream."""
        self._running = False
        logger.info(
            "Camera stream stopped: %s (%d frames sent)",
            self._camera_id,
            self._frame_count,
        )
