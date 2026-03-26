"""
Tests for TransferQueue — integration-level tests covering full job lifecycle.

These tests verify multi-job batch processing, callback sequencing,
configurable retry, and queue state transitions end-to-end.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from sftp_ui.core.transfer import (
    TransferDirection,
    TransferEngine,
    TransferError,
    TransferJob,
    TransferState,
)
from sftp_ui.core.queue import TransferQueue
from tests.conftest import FakeSFTPClient


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_engine_factory(sftp=None):
    """Returns a factory that creates TransferEngines with a shared FakeSFTPClient."""
    shared_sftp = sftp or FakeSFTPClient()
    def factory():
        return TransferEngine(shared_sftp)
    return factory


def _make_upload_job(tmp_path, name: str = "file.bin", size: int = 512) -> TransferJob:
    """Create a real local file and return a TransferJob for it."""
    content = os.urandom(size)
    p = tmp_path / name
    p.write_bytes(content)
    return TransferJob(
        local_path=str(p),
        remote_path=f"/remote/{name}",
        direction=TransferDirection.UPLOAD,
    )


def _wait_settled(queue, timeout: float = 5.0) -> None:
    """Block until all jobs are settled (no PENDING/RUNNING)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = queue.jobs()
        if jobs and all(j.state not in (TransferState.PENDING, TransferState.RUNNING) for j in jobs):
            return
        time.sleep(0.05)
    raise TimeoutError("Queue did not settle in time")


# ── Single job lifecycle ─────────────────────────────────────────────────────

class TestQueueSingleJob:
    def test_single_upload_completes(self, tmp_path):
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)
        job = _make_upload_job(tmp_path)
        queue.enqueue(job)
        queue.start()
        _wait_settled(queue)
        queue.stop()
        assert job.state == TransferState.DONE

    def test_callbacks_fire_in_order(self, tmp_path):
        events = []
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)
        queue.on_job_started = lambda j: events.append("started")
        queue.on_job_done = lambda j: events.append("done")

        job = _make_upload_job(tmp_path)
        queue.enqueue(job)
        queue.start()
        _wait_settled(queue)
        queue.stop()

        assert events == ["started", "done"]

    def test_progress_callback_fires(self, tmp_path):
        progress_calls = []
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)
        queue.on_progress = lambda j, done, total: progress_calls.append((done, total))

        job = _make_upload_job(tmp_path, size=2048)
        queue.enqueue(job)
        queue.start()
        _wait_settled(queue)
        queue.stop()

        assert len(progress_calls) > 0
        last_done, last_total = progress_calls[-1]
        assert last_done == last_total


# ── Batch processing ────────────────────────────────────────────────────────

class TestQueueBatch:
    def test_multiple_jobs_all_complete(self, tmp_path):
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=2)

        jobs = []
        for i in range(5):
            job = _make_upload_job(tmp_path, name=f"batch_{i}.bin", size=256)
            jobs.append(job)
            queue.enqueue(job)

        queue.start()
        _wait_settled(queue)
        queue.stop()

        for job in jobs:
            assert job.state == TransferState.DONE

    def test_batch_done_callback_count(self, tmp_path):
        done_count = []
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=2)
        queue.on_job_done = lambda j: done_count.append(1)

        for i in range(4):
            queue.enqueue(_make_upload_job(tmp_path, name=f"b{i}.bin", size=128))

        queue.start()
        _wait_settled(queue)
        queue.stop()

        assert len(done_count) == 4

    def test_parallel_workers_used(self, tmp_path):
        """With multiple workers, jobs should run concurrently."""
        worker_ids = []
        lock = threading.Lock()
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=4)

        def on_started(job):
            with lock:
                worker_ids.append(threading.current_thread().ident)

        queue.on_job_started = on_started

        for i in range(8):
            queue.enqueue(_make_upload_job(tmp_path, name=f"p{i}.bin", size=128))

        queue.start()
        _wait_settled(queue)
        queue.stop()

        # At least 2 different thread IDs should have processed jobs
        assert len(set(worker_ids)) >= 2


# ── Cancel mid-batch ────────────────────────────────────────────────────────

class TestQueueCancelBatch:
    def test_cancel_stops_running_jobs(self, tmp_path):
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)

        # Create a large file so transfer takes time
        job = _make_upload_job(tmp_path, name="big.bin", size=1024 * 1024)
        queue.enqueue(job)
        queue.start()
        time.sleep(0.1)
        queue.cancel_current()
        _wait_settled(queue, timeout=3.0)
        queue.stop()

        assert job.state in (TransferState.CANCELLED, TransferState.DONE)

    def test_cancel_does_not_affect_new_jobs(self, tmp_path):
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)

        job1 = _make_upload_job(tmp_path, name="first.bin", size=1024 * 1024)
        queue.enqueue(job1)
        queue.start()
        time.sleep(0.05)
        queue.cancel_current()
        time.sleep(0.1)

        # Enqueue after cancel
        job2 = _make_upload_job(tmp_path, name="second.bin", size=256)
        queue.enqueue(job2)
        _wait_settled(queue, timeout=3.0)
        queue.stop()

        assert job2.state == TransferState.DONE


# ── Resume failed job ───────────────────────────────────────────────────────

class TestQueueResume:
    def test_resume_requeues_failed_job(self, tmp_path):
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)

        job = _make_upload_job(tmp_path, size=256)
        job.state = TransferState.FAILED
        job.error = "simulated failure"
        queue.enqueue(job)

        queue.resume(job)
        assert job.state == TransferState.PENDING
        assert job.error is None

        queue.start()
        _wait_settled(queue)
        queue.stop()
        assert job.state == TransferState.DONE

    def test_resume_cancelled_job(self, tmp_path):
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)

        job = _make_upload_job(tmp_path, size=256)
        job.state = TransferState.CANCELLED
        queue.enqueue(job)

        queue.resume(job)
        queue.start()
        _wait_settled(queue)
        queue.stop()
        assert job.state == TransferState.DONE


# ── Configurable retry ──────────────────────────────────────────────────────

class TestQueueConfigurableRetry:
    def test_max_retries_is_configurable(self):
        queue = TransferQueue(lambda: None, max_retries=3)
        assert queue._max_retries == 3

    def test_retry_delay_is_configurable(self):
        queue = TransferQueue(lambda: None, retry_delay=1.5)
        assert queue._retry_delay == 1.5

    def test_default_max_retries_is_5(self):
        queue = TransferQueue(lambda: None)
        assert queue._max_retries == 5

    def test_default_retry_delay_is_2(self):
        queue = TransferQueue(lambda: None)
        assert queue._retry_delay == 2.0


# ── Queue state queries ─────────────────────────────────────────────────────

class TestQueueStateQueries:
    def test_pending_count_reflects_queue(self, tmp_path):
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)

        for i in range(3):
            queue.enqueue(_make_upload_job(tmp_path, name=f"q{i}.bin", size=64))
        assert queue.pending_count() == 3

    def test_jobs_returns_snapshot(self, tmp_path):
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)

        job = _make_upload_job(tmp_path, size=64)
        queue.enqueue(job)

        jobs = queue.jobs()
        assert len(jobs) == 1
        assert jobs[0] is job

    def test_clear_done_removes_completed(self, tmp_path):
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)

        job1 = _make_upload_job(tmp_path, name="done.bin", size=64)
        job1.state = TransferState.DONE
        job2 = _make_upload_job(tmp_path, name="pending.bin", size=64)
        queue.enqueue(job1)
        queue.enqueue(job2)

        queue.clear_done()
        jobs = queue.jobs()
        assert len(jobs) == 1
        assert jobs[0].local_path.endswith("pending.bin")


# ── Stop behavior ───────────────────────────────────────────────────────────

class TestQueueStop:
    def test_stop_cancels_running_transfers(self, tmp_path):
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)

        job = _make_upload_job(tmp_path, name="big.bin", size=1024 * 1024)
        queue.enqueue(job)
        queue.start()
        time.sleep(0.05)
        queue.stop(timeout=2.0)

        assert job.state in (TransferState.CANCELLED, TransferState.DONE)

    def test_start_after_stop_works(self, tmp_path):
        sftp = FakeSFTPClient()
        queue = TransferQueue(_make_engine_factory(sftp), num_workers=1)

        job1 = _make_upload_job(tmp_path, name="first.bin", size=128)
        queue.enqueue(job1)
        queue.start()
        _wait_settled(queue)
        queue.stop()
        assert job1.state == TransferState.DONE

        job2 = _make_upload_job(tmp_path, name="second.bin", size=128)
        queue.enqueue(job2)
        queue.start()
        _wait_settled(queue)
        queue.stop()
        assert job2.state == TransferState.DONE
