"""Tests for OfflineQueue — SQLite-backed persistent queue."""

import json
import sqlite3
import time

import pytest

from robotrace.offline_queue import OfflineQueue


class TestOfflineQueue:
    """Tests for OfflineQueue class using tmp_path fixture."""

    def test_open_creates_database_file(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        q = OfflineQueue(path=db_path)
        q.open()

        assert (tmp_path / "test.db").exists()
        q.close()

    def test_put_and_peek(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()

        row_id = q.put([{"key": "value"}], batch_type="telemetry")
        assert row_id is not None

        batches = q.peek(limit=5, batch_type="telemetry")
        assert len(batches) == 1
        assert batches[0][0] == row_id
        assert batches[0][1] == "telemetry"
        assert batches[0][2] == [{"key": "value"}]

        q.close()

    def test_put_returns_row_id(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()

        id1 = q.put([{"a": 1}])
        id2 = q.put([{"b": 2}])

        assert id1 is not None
        assert id2 is not None
        assert id2 > id1

        q.close()

    def test_peek_returns_oldest_first(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()

        q.put([{"order": 1}])
        q.put([{"order": 2}])
        q.put([{"order": 3}])

        batches = q.peek(limit=2)
        assert len(batches) == 2
        assert batches[0][2] == [{"order": 1}]
        assert batches[1][2] == [{"order": 2}]

        q.close()

    def test_delete_removes_batch(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()

        row_id = q.put([{"data": "to_delete"}])
        assert q.qsize() == 1

        q.delete(row_id)
        assert q.qsize() == 0

        q.close()

    def test_qsize_returns_correct_count(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()

        assert q.qsize() == 0
        q.put([{"a": 1}])
        assert q.qsize() == 1
        q.put([{"b": 2}])
        assert q.qsize() == 2

        q.close()

    def test_total_size_bytes_nonzero_after_put(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()

        assert q.total_size_bytes() == 0
        q.put([{"key": "value", "data": "x" * 100}])
        assert q.total_size_bytes() > 0

        q.close()

    def test_increment_retry(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()

        row_id = q.put([{"data": "retry_me"}])

        q.increment_retry(row_id)
        q.increment_retry(row_id)

        # Check retry count in DB directly
        row = q._conn.execute(
            "SELECT retry_count FROM offline_queue WHERE id = ?", (row_id,)
        ).fetchone()
        assert row[0] == 2

        q.close()

    def test_batch_type_filtering(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()

        q.put([{"type": "tel"}], batch_type="telemetry")
        q.put([{"type": "score"}], batch_type="scores")
        q.put([{"type": "tel2"}], batch_type="telemetry")

        tel_batches = q.peek(limit=10, batch_type="telemetry")
        score_batches = q.peek(limit=10, batch_type="scores")

        assert len(tel_batches) == 2
        assert len(score_batches) == 1
        assert score_batches[0][2] == [{"type": "score"}]

        q.close()

    def test_evict_expired_removes_old_batches(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"), max_age_hours=1)
        q.open()

        # Insert a batch and manually backdate it
        q.put([{"old": True}])
        # Backdate the created_at to 2 hours ago
        q._conn.execute(
            "UPDATE offline_queue SET created_at = ?",
            (time.time() - 7200,),
        )
        q._conn.commit()

        assert q.qsize() == 1
        evicted = q.evict_expired()
        assert evicted == 1
        assert q.qsize() == 0

        q.close()

    def test_close_logs_correct_count(self, tmp_path, caplog):
        import logging

        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()
        q.put([{"a": 1}])
        q.put([{"b": 2}])

        with caplog.at_level(logging.INFO, logger="robotrace.offline_queue"):
            q.close()

        assert "2 batches persisted" in caplog.text

    def test_peek_marks_corrupt_json_as_dead(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()

        # Insert corrupt JSON directly
        q._conn.execute(
            "INSERT INTO offline_queue (created_at, batch_type, batch_json, size_bytes) VALUES (?, ?, ?, ?)",
            (time.time(), "telemetry", "NOT VALID JSON {{{", 20),
        )
        q._conn.commit()

        # peek should handle corrupt data gracefully
        batches = q.peek(limit=5)
        assert len(batches) == 0

        # The corrupt batch should be marked as dead
        dead_count = q._conn.execute(
            "SELECT COUNT(*) FROM offline_queue WHERE dead = 1"
        ).fetchone()[0]
        assert dead_count == 1

        q.close()

    def test_default_path_uses_home_directory(self):
        q = OfflineQueue()
        assert ".robotrace" in q._path
        assert "offline_queue.db" in q._path

    def test_put_on_closed_queue_returns_none(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()
        q.close()

        result = q.put([{"data": "after_close"}])
        assert result is None

    def test_peek_on_closed_queue_returns_empty(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()
        q.close()

        result = q.peek(limit=5)
        assert result == []

    def test_qsize_on_closed_queue_returns_zero(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()
        q.put([{"x": 1}])
        q.close()

        assert q.qsize() == 0

    def test_dead_batch_after_max_retries(self, tmp_path):
        q = OfflineQueue(path=str(tmp_path / "test.db"))
        q.open()

        row_id = q.put([{"data": "will_die"}])

        # Increment retry count to 50 (the dead threshold)
        for _ in range(50):
            q.increment_retry(row_id)

        # Should be marked dead
        row = q._conn.execute(
            "SELECT dead FROM offline_queue WHERE id = ?", (row_id,)
        ).fetchone()
        assert row[0] == 1

        # Should not appear in peek
        batches = q.peek(limit=10)
        assert len(batches) == 0

        q.close()

    def test_multiple_open_close_cycles(self, tmp_path):
        db_path = str(tmp_path / "test.db")

        q = OfflineQueue(path=db_path)
        q.open()
        q.put([{"cycle": 1}])
        q.close()

        # Reopen — data should persist
        q2 = OfflineQueue(path=db_path)
        q2.open()
        assert q2.qsize() == 1
        batches = q2.peek(limit=5)
        assert batches[0][2] == [{"cycle": 1}]
        q2.close()
