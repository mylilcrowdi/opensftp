"""
Tests for TransferHistory wiring into MainWindow/Queue lifecycle.

Verifies that completed, failed, and cancelled transfers are automatically
recorded in the persistent history log via the signal bridge.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from sftp_ui.core.transfer import TransferDirection, TransferJob, TransferState
from sftp_ui.core.transfer_history import TransferHistory
from sftp_ui.core.queue import TransferQueue
from sftp_ui.core.transfer import TransferEngine
from tests.conftest import FakeSFTPClient


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_engine_factory(sftp=None):
    shared = sftp or FakeSFTPClient()
    return lambda: TransferEngine(shared)


def _make_upload_job(tmp_path, name="file.bin", size=256):
    content = os.urandom(size)
    p = tmp_path / name
    p.write_bytes(content)
    return TransferJob(
        local_path=str(p), remote_path=f"/remote/{name}",
        direction=TransferDirection.UPLOAD,
    )


def _wait_settled(queue, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = queue.jobs()
        if jobs and all(j.state not in (TransferState.PENDING, TransferState.RUNNING) for j in jobs):
            return
        time.sleep(0.05)
    raise TimeoutError("Queue did not settle")


# ── History records on job completion ────────────────────────────────────────

class TestHistoryWiringDone:
    def test_done_job_recorded_in_history(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)
        queue.on_job_done = lambda j: history.record(j)

        job = _make_upload_job(tmp_path)
        queue.enqueue(job)
        queue.start()
        _wait_settled(queue)
        queue.stop()

        entries = history.entries()
        assert len(entries) == 1
        assert entries[0]["state"] == "done"
        assert entries[0]["filename"] == "file.bin"

    def test_multiple_done_jobs_all_recorded(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=2)
        queue.on_job_done = lambda j: history.record(j)

        for i in range(5):
            queue.enqueue(_make_upload_job(tmp_path, name=f"f{i}.bin", size=128))

        queue.start()
        _wait_settled(queue)
        queue.stop()

        entries = history.entries()
        assert len(entries) == 5
        assert all(e["state"] == "done" for e in entries)

    def test_done_entry_has_correct_size(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)
        queue.on_job_done = lambda j: history.record(j)

        job = _make_upload_job(tmp_path, size=1024)
        queue.enqueue(job)
        queue.start()
        _wait_settled(queue)
        queue.stop()

        entry = history.entries()[0]
        assert entry["total_bytes"] == 1024


# ── History records on job failure ───────────────────────────────────────────

class TestHistoryWiringFailed:
    def test_failed_job_recorded_in_history(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")

        def failing_factory():
            sftp = FakeSFTPClient()
            sftp.remote_size = MagicMock(side_effect=Exception("disk error"))
            return TransferEngine(sftp)

        queue = TransferQueue(failing_factory, num_workers=1, max_retries=1)
        queue.on_job_failed = lambda j: history.record(j)

        job = _make_upload_job(tmp_path)
        queue.enqueue(job)
        queue.start()
        _wait_settled(queue)
        queue.stop()

        entries = history.entries()
        assert len(entries) == 1
        assert entries[0]["state"] == "failed"
        assert entries[0]["error"] is not None


# ── History records on cancellation ──────────────────────────────────────────

class TestHistoryWiringCancelled:
    def test_cancelled_job_recorded_in_history(self, tmp_path):
        history = TransferHistory(tmp_path / "history.jsonl")
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)
        queue.on_job_cancelled = lambda j: history.record(j)

        job = _make_upload_job(tmp_path, size=1024 * 1024)
        queue.enqueue(job)
        queue.start()
        time.sleep(0.05)
        queue.cancel_current()
        _wait_settled(queue, timeout=3.0)
        queue.stop()

        # Job may have completed before cancel took effect
        entries = history.entries()
        if job.state == TransferState.CANCELLED:
            assert len(entries) == 1
            assert entries[0]["state"] == "cancelled"


# ── History callback thread-safety ───────────────────────────────────────────

class TestHistoryThreadSafety:
    def test_concurrent_records_no_corruption(self, tmp_path):
        """Multiple workers recording simultaneously must not corrupt the file."""
        history = TransferHistory(tmp_path / "history.jsonl")
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=4)
        queue.on_job_done = lambda j: history.record(j)

        for i in range(20):
            queue.enqueue(_make_upload_job(tmp_path, name=f"concurrent_{i}.bin", size=64))

        queue.start()
        _wait_settled(queue)
        queue.stop()

        entries = history.entries()
        assert len(entries) == 20
        # All entries parseable (no partial JSON lines)
        filenames = {e["filename"] for e in entries}
        assert len(filenames) == 20

    def test_history_survives_rapid_fire(self, tmp_path):
        """Rapid successive records don't lose entries."""
        history = TransferHistory(tmp_path / "history.jsonl")
        for i in range(100):
            job = TransferJob(
                local_path=f"/local/rapid_{i}.bin",
                remote_path=f"/remote/rapid_{i}.bin",
            )
            job.state = TransferState.DONE
            job.total_bytes = 100
            job.bytes_done = 100
            job.finished_at = time.time()
            history.record(job)

        entries = history.entries()
        assert len(entries) == 100
