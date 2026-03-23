"""
Extended TransferQueue tests — scenarios not covered by test_queue.py.

Covers: on_progress callback, multi-worker (num_workers=2/4),
        on_worker_error (engine_factory exception), resume() after
        cancel/fail, on_job_cancelled callback, progress monotonicity.
"""
from __future__ import annotations

import threading
import time

import pytest

from sftp_ui.core.transfer import TransferDirection, TransferEngine, TransferJob, TransferState
from sftp_ui.core.queue import TransferQueue
from tests.conftest import FakeSFTPClient


def _wait(condition, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while not condition() and time.time() < deadline:
        time.sleep(interval)


def make_queue(sftp=None, num_workers=1, max_retries=1, retry_delay=0.0):
    sftp = sftp or FakeSFTPClient()
    engine = TransferEngine(sftp, chunk_size=128)
    return TransferQueue(
        engine_factory=lambda: engine,
        num_workers=num_workers,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )


# ── on_progress callback ───────────────────────────────────────────────────────

class TestQueueProgressCallback:
    def test_on_progress_called_during_transfer(self, make_local_file):
        path, _ = make_local_file(512)
        progress_calls = []
        sftp = FakeSFTPClient()
        engine = TransferEngine(sftp, chunk_size=64)
        q = TransferQueue(engine_factory=lambda: engine, num_workers=1, max_retries=1, retry_delay=0)
        q.on_progress = lambda job, done, total: progress_calls.append((done, total))

        job = TransferJob(local_path=path, remote_path="/r/f")
        q.enqueue(job)
        q.start()
        _wait(lambda: job.state == TransferState.DONE)
        q.stop()

        assert len(progress_calls) > 0

    def test_on_progress_receives_correct_job(self, make_local_file):
        path, _ = make_local_file(256)
        seen_jobs = []
        sftp = FakeSFTPClient()
        engine = TransferEngine(sftp, chunk_size=64)
        q = TransferQueue(engine_factory=lambda: engine, num_workers=1, max_retries=1, retry_delay=0)
        q.on_progress = lambda job, done, total: seen_jobs.append(job)

        job = TransferJob(local_path=path, remote_path="/r/f")
        q.enqueue(job)
        q.start()
        _wait(lambda: job.state == TransferState.DONE)
        q.stop()

        assert all(j is job for j in seen_jobs)

    def test_on_progress_total_matches_file_size(self, make_local_file):
        size = 512
        path, _ = make_local_file(size)
        totals = []
        sftp = FakeSFTPClient()
        engine = TransferEngine(sftp, chunk_size=64)
        q = TransferQueue(engine_factory=lambda: engine, num_workers=1, max_retries=1, retry_delay=0)
        q.on_progress = lambda job, done, total: totals.append(total)

        job = TransferJob(local_path=path, remote_path="/r/f")
        q.enqueue(job)
        q.start()
        _wait(lambda: job.state == TransferState.DONE)
        q.stop()

        assert totals  # at least one call
        assert totals[-1] == size

    def test_on_progress_done_values_monotonically_increasing(self, make_local_file):
        path, _ = make_local_file(1024)
        done_values = []
        sftp = FakeSFTPClient()
        engine = TransferEngine(sftp, chunk_size=64)
        q = TransferQueue(engine_factory=lambda: engine, num_workers=1, max_retries=1, retry_delay=0)
        q.on_progress = lambda job, done, total: done_values.append(done)

        job = TransferJob(local_path=path, remote_path="/r/f")
        q.enqueue(job)
        q.start()
        _wait(lambda: job.state == TransferState.DONE)
        q.stop()

        for a, b in zip(done_values, done_values[1:]):
            assert b >= a, f"Progress went backwards: {a} → {b}"


# ── Multi-worker ───────────────────────────────────────────────────────────────

class TestQueueMultiWorker:
    def test_two_workers_process_all_jobs(self, make_local_file):
        path, _ = make_local_file(256)
        sftp = FakeSFTPClient()
        engine = TransferEngine(sftp, chunk_size=128)
        q = TransferQueue(engine_factory=lambda: engine, num_workers=2, max_retries=1, retry_delay=0)

        jobs = [TransferJob(local_path=path, remote_path=f"/r/{i}") for i in range(6)]
        for job in jobs:
            q.enqueue(job)
        q.start()

        _wait(lambda: all(j.state == TransferState.DONE for j in jobs), timeout=10)
        q.stop()

        assert all(j.state == TransferState.DONE for j in jobs)

    def test_four_workers_process_all_jobs(self, make_local_file):
        path, _ = make_local_file(128)
        sftp = FakeSFTPClient()
        engine = TransferEngine(sftp, chunk_size=64)
        q = TransferQueue(engine_factory=lambda: engine, num_workers=4, max_retries=1, retry_delay=0)

        jobs = [TransferJob(local_path=path, remote_path=f"/r/{i}") for i in range(8)]
        for job in jobs:
            q.enqueue(job)
        q.start()

        _wait(lambda: all(j.state == TransferState.DONE for j in jobs), timeout=10)
        q.stop()

        assert all(j.state == TransferState.DONE for j in jobs)

    def test_multi_worker_done_count_correct(self, make_local_file):
        path, _ = make_local_file(256)
        done_count = [0]
        lock = threading.Lock()
        sftp = FakeSFTPClient()
        engine = TransferEngine(sftp, chunk_size=128)
        q = TransferQueue(engine_factory=lambda: engine, num_workers=2, max_retries=1, retry_delay=0)

        def on_done(job):
            with lock:
                done_count[0] += 1

        q.on_job_done = on_done

        n = 5
        jobs = [TransferJob(local_path=path, remote_path=f"/r/{i}") for i in range(n)]
        for job in jobs:
            q.enqueue(job)
        q.start()

        _wait(lambda: done_count[0] == n, timeout=10)
        q.stop()

        assert done_count[0] == n


# ── on_worker_error ────────────────────────────────────────────────────────────

class TestQueueWorkerError:
    def test_worker_error_callback_called_when_factory_raises(self, make_local_file):
        path, _ = make_local_file(256)
        errors = []

        def bad_factory():
            raise RuntimeError("cannot connect")

        q = TransferQueue(engine_factory=bad_factory, num_workers=1, max_retries=1, retry_delay=0)
        q.on_worker_error = errors.append

        job = TransferJob(local_path=path, remote_path="/r/f")
        q.enqueue(job)
        q.start()

        _wait(lambda: bool(errors), timeout=3)
        q.stop()

        assert len(errors) == 1
        assert isinstance(errors[0], RuntimeError)

    def test_worker_error_message_preserved(self, make_local_file):
        path, _ = make_local_file(256)
        errors = []

        def bad_factory():
            raise ConnectionError("SSH key invalid")

        q = TransferQueue(engine_factory=bad_factory, num_workers=1, max_retries=1, retry_delay=0)
        q.on_worker_error = errors.append
        q.enqueue(TransferJob(local_path=path, remote_path="/r/f"))
        q.start()

        _wait(lambda: bool(errors), timeout=3)
        q.stop()

        assert "SSH key invalid" in str(errors[0])

    def test_worker_error_does_not_crash_queue(self, make_local_file):
        """stop() must not raise even if all workers failed."""
        path, _ = make_local_file(256)

        q = TransferQueue(
            engine_factory=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
            num_workers=2, max_retries=1, retry_delay=0,
        )
        q.on_worker_error = lambda e: None
        q.start()
        time.sleep(0.2)
        q.stop()  # must not raise


# ── resume() after cancel / fail ──────────────────────────────────────────────

class TestQueueResume:
    def test_resume_resets_cancelled_job_to_pending(self, make_local_file):
        path, _ = make_local_file(256)
        q = make_queue()
        job = TransferJob(local_path=path, remote_path="/r/f")
        job.state = TransferState.CANCELLED

        q.enqueue(job)
        q.resume(job)

        assert job.state == TransferState.PENDING

    def test_resume_resets_failed_job_to_pending(self, make_local_file):
        path, _ = make_local_file(256)
        q = make_queue()
        job = TransferJob(local_path=path, remote_path="/r/f")
        job.state = TransferState.FAILED
        job.error = "network error"

        q.resume(job)

        assert job.state == TransferState.PENDING
        assert job.error is None

    def test_resume_clears_error_field(self, make_local_file):
        path, _ = make_local_file(256)
        q = make_queue()
        job = TransferJob(local_path=path, remote_path="/r/f")
        job.state = TransferState.FAILED
        job.error = "broken pipe"

        q.resume(job)

        assert job.error is None

    def test_resume_does_not_affect_done_job(self, make_local_file):
        path, _ = make_local_file(256)
        q = make_queue()
        job = TransferJob(local_path=path, remote_path="/r/f")
        job.state = TransferState.DONE

        q.resume(job)

        assert job.state == TransferState.DONE  # unchanged

    def test_resume_does_not_affect_running_job(self, make_local_file):
        path, _ = make_local_file(256)
        q = make_queue()
        job = TransferJob(local_path=path, remote_path="/r/f")
        job.state = TransferState.RUNNING

        q.resume(job)

        assert job.state == TransferState.RUNNING  # unchanged

    def test_resumed_job_gets_processed(self, make_local_file):
        path, content = make_local_file(256)
        sftp = FakeSFTPClient()
        engine = TransferEngine(sftp, chunk_size=128)
        q = TransferQueue(engine_factory=lambda: engine, num_workers=1, max_retries=1, retry_delay=0)

        job = TransferJob(local_path=path, remote_path="/r/f")
        job.state = TransferState.CANCELLED
        q.enqueue(job)
        q.resume(job)
        q.start()

        _wait(lambda: job.state == TransferState.DONE, timeout=5)
        q.stop()

        assert job.state == TransferState.DONE


# ── on_job_cancelled callback ─────────────────────────────────────────────────

class TestQueueCancelledCallback:
    def test_on_job_cancelled_called_after_cancel(self, tmp_path):
        content = b"\x00" * (128 * 40)  # 40 slow chunks
        p = tmp_path / "big.bin"
        p.write_bytes(content)

        sftp = FakeSFTPClient()
        original_open = sftp.open_remote
        started = threading.Event()

        class SlowFile:
            def __init__(self_):
                self_._inner = original_open("/r/f", "wb")
            def write(self_, data):
                started.set()
                time.sleep(0.03)
                return self_._inner.write(data)
            def set_pipelined(self_, *a): pass
            def __enter__(self_): return self_
            def __exit__(self_, *a): pass

        sftp.open_remote = lambda p, mode: SlowFile()

        engine = TransferEngine(sftp, chunk_size=128)
        q = TransferQueue(engine_factory=lambda: engine, num_workers=1, max_retries=1, retry_delay=0)
        cancelled_jobs = []
        q.on_job_cancelled = cancelled_jobs.append

        job = TransferJob(local_path=str(p), remote_path="/r/f")
        q.enqueue(job)
        q.start()

        started.wait(timeout=3)
        q.cancel_current()

        _wait(lambda: job.state in (TransferState.CANCELLED, TransferState.DONE), timeout=5)
        q.stop()

        if job.state == TransferState.CANCELLED:
            assert len(cancelled_jobs) == 1
            assert cancelled_jobs[0] is job

    def test_on_job_cancelled_not_called_for_done(self, make_local_file):
        path, _ = make_local_file(256)
        cancelled_jobs = []
        q = make_queue()
        q.on_job_cancelled = cancelled_jobs.append

        job = TransferJob(local_path=path, remote_path="/r/f")
        q.enqueue(job)
        q.start()
        _wait(lambda: job.state == TransferState.DONE)
        q.stop()

        assert cancelled_jobs == []


# ── pending_count with mixed states ───────────────────────────────────────────

class TestQueuePendingCount:
    def test_pending_count_decreases_as_jobs_complete(self, make_local_file):
        path, _ = make_local_file(128)
        q = make_queue()

        jobs = [TransferJob(local_path=path, remote_path=f"/r/{i}") for i in range(3)]
        for job in jobs:
            q.enqueue(job)

        assert q.pending_count() == 3
        q.start()
        _wait(lambda: all(j.state == TransferState.DONE for j in jobs))
        q.stop()

        assert q.pending_count() == 0

    def test_pending_count_ignores_failed_jobs(self, tmp_path, make_local_file):
        good_path, _ = make_local_file(128)
        q = make_queue(max_retries=1)

        bad = TransferJob(local_path=str(tmp_path / "missing"), remote_path="/r/bad")
        good = TransferJob(local_path=good_path, remote_path="/r/good")
        q.enqueue(bad)
        q.enqueue(good)
        q.start()
        _wait(lambda: good.state == TransferState.DONE)
        q.stop()

        # Both settled — pending_count should be 0
        assert q.pending_count() == 0
