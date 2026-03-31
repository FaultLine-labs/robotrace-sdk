"""Disk-persistent offline queue — SQLite WAL mode.

Survives process restarts and power cycles. Used by the SensorPipeline
and score queue to buffer data when the server is unreachable.

Storage: SQLite with WAL (Write-Ahead Logging) for concurrent read/write
without blocking. Python's built-in sqlite3 module — no external deps.

Configuration via environment variables:
    ROBOTRACE_OFFLINE_MAX_MB    — Max queue size in MB (default: 100)
    ROBOTRACE_OFFLINE_MAX_HOURS — Max batch age in hours (default: 72)
    ROBOTRACE_DATA_DIR          — Directory for queue DB (default: .robotrace/)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("robotrace.offline_queue")

# Retry backoff thresholds
_BACKOFF_MODERATE = 3    # After 3 failures: 30s backoff
_BACKOFF_SLOW = 10       # After 10 failures: 5min backoff
_BACKOFF_DEAD = 50       # After 50 failures: skip (mark dead)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS offline_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    batch_type TEXT NOT NULL DEFAULT 'telemetry',
    batch_json TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL,
    dead INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_offline_created ON offline_queue(created_at)
"""


class OfflineQueue:
    """SQLite-backed persistent queue for offline telemetry and score buffering.

    Thread-safe. Uses WAL mode for concurrent read/write.

    Parameters
    ----------
    path : str
        Path to the SQLite database file. Parent directory is created if needed.
    max_size_mb : int
        Maximum total queue size in MB. Oldest batches evicted when exceeded.
    max_age_hours : int
        Maximum batch age in hours. Expired batches evicted on each cycle.
    """

    def __init__(
        self,
        path: str | None = None,
        max_size_mb: int | None = None,
        max_age_hours: int | None = None,
    ) -> None:
        # Resolve configuration from env vars with defaults
        data_dir = os.environ.get("ROBOTRACE_DATA_DIR", os.path.join(str(Path.home()), ".robotrace"))
        self._path = path or os.path.join(data_dir, "offline_queue.db")
        self._max_size_bytes = (
            max_size_mb or int(os.environ.get("ROBOTRACE_OFFLINE_MAX_MB", "100"))
        ) * 1024 * 1024
        self._max_age_seconds = (
            max_age_hours or int(os.environ.get("ROBOTRACE_OFFLINE_MAX_HOURS", "72"))
        ) * 3600

        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._closed = False

    def open(self) -> None:
        """Open the database, create tables, enable WAL mode."""
        # Create parent directory
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            self._path,
            check_same_thread=False,  # We manage thread safety with _lock
            timeout=10.0,
        )

        # Enable WAL mode for concurrent reads/writes
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA wal_autocheckpoint=1000")
        # Synchronous NORMAL is safe with WAL and 2x faster than FULL
        self._conn.execute("PRAGMA synchronous=NORMAL")

        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.execute(_CREATE_INDEX_SQL)
        self._conn.commit()

        # Run initial eviction
        expired = self.evict_expired()
        evicted = self.evict_by_size()
        size = self.qsize()

        if size > 0:
            logger.info(
                "OfflineQueue opened: %s (%d pending batches, evicted %d expired + %d over-size)",
                self._path, size, expired, evicted,
            )
        else:
            logger.debug("OfflineQueue opened: %s (empty)", self._path)

    def put(self, batch: list[dict[str, Any]], batch_type: str = "telemetry") -> int | None:
        """Insert a failed batch into the queue.

        Parameters
        ----------
        batch : list[dict]
            The batch of telemetry samples or scores that failed to send.
        batch_type : str
            "telemetry" or "scores" — used for routing on recovery.

        Returns
        -------
        int | None
            The row ID of the inserted batch (for write-ahead delete on success),
            or None if the insert failed.
        """
        if self._closed or self._conn is None:
            return None

        batch_json = json.dumps(batch)
        size_bytes = len(batch_json.encode("utf-8"))

        with self._lock:
            try:
                cursor = self._conn.execute(
                    "INSERT INTO offline_queue (created_at, batch_type, batch_json, size_bytes) VALUES (?, ?, ?, ?)",
                    (time.time(), batch_type, batch_json, size_bytes),
                )
                self._conn.commit()
                row_id = cursor.lastrowid
                logger.debug(
                    "Offline queue: stored %d %s samples (%d bytes, id=%s)",
                    len(batch), batch_type, size_bytes, row_id,
                )
                return row_id
            except sqlite3.Error as e:
                logger.warning("OfflineQueue.put() failed: %s", e)
                return None

    def peek(self, limit: int = 5, batch_type: str | None = None) -> list[tuple[int, str, list[dict[str, Any]]]]:
        """Get oldest N batches without removing them.

        Returns list of (row_id, batch_type, batch_data) tuples.
        Skips dead batches and respects backoff timing.
        """
        if self._closed or self._conn is None:
            return []

        now = time.time()

        with self._lock:
            try:
                type_filter = ""
                params: list[Any] = []
                if batch_type:
                    type_filter = "AND batch_type = ?"
                    params.append(batch_type)
                params.append(limit)

                rows = self._conn.execute(
                    f"""
                    SELECT id, batch_type, batch_json, retry_count, created_at
                    FROM offline_queue
                    WHERE dead = 0 {type_filter}
                    ORDER BY created_at ASC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()

                result = []
                for row_id, btype, batch_json, retry_count, created_at in rows:
                    # Apply backoff
                    if retry_count >= _BACKOFF_DEAD:
                        continue
                    if retry_count >= _BACKOFF_SLOW:
                        age = now - created_at
                        if age < 300:  # 5 min backoff
                            continue
                    elif retry_count >= _BACKOFF_MODERATE:
                        age = now - created_at
                        if age < 30:  # 30s backoff
                            continue

                    try:
                        batch_data = json.loads(batch_json)
                        result.append((row_id, btype, batch_data))
                    except json.JSONDecodeError:
                        # Corrupt data — mark as dead and commit immediately
                        self._conn.execute(
                            "UPDATE offline_queue SET dead = 1 WHERE id = ?", (row_id,)
                        )
                        self._conn.commit()

                return result
            except sqlite3.Error as e:
                logger.warning("OfflineQueue.peek() failed: %s", e)
                return []

    def delete(self, row_id: int) -> None:
        """Remove a successfully-sent batch from the queue."""
        if self._closed or self._conn is None:
            return

        with self._lock:
            try:
                self._conn.execute("DELETE FROM offline_queue WHERE id = ?", (row_id,))
                self._conn.commit()
            except sqlite3.Error as e:
                logger.warning("OfflineQueue.delete(%d) failed: %s", row_id, e)

    def increment_retry(self, row_id: int) -> None:
        """Increment retry count for a batch. Marks as dead if threshold exceeded."""
        if self._closed or self._conn is None:
            return

        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE offline_queue SET retry_count = retry_count + 1 WHERE id = ?",
                    (row_id,),
                )
                # Check if it should be marked dead
                row = self._conn.execute(
                    "SELECT retry_count FROM offline_queue WHERE id = ?", (row_id,)
                ).fetchone()
                if row and row[0] >= _BACKOFF_DEAD:
                    self._conn.execute(
                        "UPDATE offline_queue SET dead = 1 WHERE id = ?", (row_id,)
                    )
                    logger.warning(
                        "OfflineQueue: batch %d marked dead after %d retries", row_id, row[0]
                    )
                self._conn.commit()
            except sqlite3.Error as e:
                logger.warning("OfflineQueue.increment_retry(%d) failed: %s", row_id, e)

    def evict_expired(self) -> int:
        """Delete batches older than max_age. Returns count deleted."""
        if self._closed or self._conn is None:
            return 0

        cutoff = time.time() - self._max_age_seconds

        with self._lock:
            try:
                cursor = self._conn.execute(
                    "DELETE FROM offline_queue WHERE created_at < ?", (cutoff,)
                )
                self._conn.commit()
                count = cursor.rowcount
                if count > 0:
                    logger.info("OfflineQueue: evicted %d expired batches", count)
                return count
            except sqlite3.Error as e:
                logger.warning("OfflineQueue.evict_expired() failed: %s", e)
                return 0

    def evict_by_size(self) -> int:
        """Delete oldest batches until total size < max_size. Returns count deleted."""
        if self._closed or self._conn is None:
            return 0

        with self._lock:
            try:
                total = self._conn.execute(
                    "SELECT COALESCE(SUM(size_bytes), 0) FROM offline_queue"
                ).fetchone()[0]

                if total <= self._max_size_bytes:
                    return 0

                # Delete oldest batches until under limit
                count = 0
                while total > self._max_size_bytes:
                    row = self._conn.execute(
                        "SELECT id, size_bytes FROM offline_queue ORDER BY created_at ASC LIMIT 1"
                    ).fetchone()
                    if row is None:
                        break
                    self._conn.execute("DELETE FROM offline_queue WHERE id = ?", (row[0],))
                    total -= row[1]
                    count += 1

                self._conn.commit()
                if count > 0:
                    logger.info("OfflineQueue: evicted %d batches by size (now %d bytes)", count, total)
                return count
            except sqlite3.Error as e:
                logger.warning("OfflineQueue.evict_by_size() failed: %s", e)
                return 0

    def qsize(self) -> int:
        """Return number of non-dead batches in queue."""
        if self._closed or self._conn is None:
            return 0

        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM offline_queue WHERE dead = 0"
                ).fetchone()
                return row[0] if row else 0
            except sqlite3.Error:
                return 0

    def total_size_bytes(self) -> int:
        """Return total size of queued data in bytes."""
        if self._closed or self._conn is None:
            return 0

        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(SUM(size_bytes), 0) FROM offline_queue"
                ).fetchone()
                return row[0] if row else 0
            except sqlite3.Error:
                return 0

    def close(self) -> None:
        """Close database connection. Data persists on disk for next startup."""
        if self._closed:
            return

        # Read size BEFORE setting _closed (qsize returns 0 when closed)
        size = self.qsize()

        self._closed = True

        if self._conn:
            try:
                # Checkpoint WAL before closing for clean state
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.close()
            except sqlite3.Error:
                pass

        if size > 0:
            logger.info("OfflineQueue closed: %d batches persisted to disk", size)
        else:
            logger.debug("OfflineQueue closed (empty)")
