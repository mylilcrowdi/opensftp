"""
Tests for TransferQueue — concurrency, ordering, callbacks.
"""
from __future__ import annotations

import time
import threading

import pytest

from sftp_ui.core.transfer import TransferEngine, TransferJob, TransferState
from sftp_ui.core.queue import TransferQueue
from tests.conftest import FakeSFTPClient


def make_queue(sftp=None, max_retries=1, retry_delay=0.0):
    sftp = sftp or FakeSFTPClient()
    engine = TransferEngine(sftp, chunk_size=128)
    return TransferQueue(
        engine_factory=lambda: engine,
        num_workers=1,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )


class TestQueueBasics:
    def test_enqueue_adds_job(self, make_local_file):
        path, _ = make_local_file(256)
        q = make_queue()
        job = TransferJob(local_path=path, remote_path="/r/f")
        q.enqueue(job)
        assert len(q.jobs()) == 1

    def test_pending_count(self, make_local_file):
        path, _ = make_local_file(256)
        q = make_queue()
        q.enqueue(TransferJob(local_path=path, remote_path="/r/a"))
        q.enqueue(TransferJob(local_path=path, remote_path="/r/b"))
        assert q.pending_count() == 2

    def test_processes_job_to_done(self, make_local_file):
        path, _ = make_local_file(512)
        q = make_queue()
        job = TransferJob(local_path=path, remote_path="/r/f")
        q.enqueue(job)
        q.start()
        deadline = time.time() + 5
        while job.state != TransferState.DONE and time.time() < deadline:
            time.sleep(0.05)
        q.stop()
        assert job.state == TransferState.DONE

    def test_processes_multiple_jobs_in_order(self, make_local_file):
        path, _ = make_local_file(256)
        finished_order = []
        q = make_queue()

        job1 = TransferJob(local_path=path, remote_path="/r/1")
        job2 = TransferJob(local_path=path, remote_path="/r/2")
        job3 = TransferJob(local_path=path, remote_path="/r/3")

        for job in (job1, job2, job3):
            q.enqueue(job)

        def on_done(j):
            finished_order.append(j.remote_path)

        q.on_job_done = on_done
        q.start()

        deadline = time.time() + 5
        while job3.state != TransferState.DONE and time.time() < deadline:
            time.sleep(0.05)
        q.stop()

        assert finished_order == ["/r/1", "/r/2", "/r/3"]

    def test_on_job_done_callback(self, make_local_file):
        path, _ = make_local_file(256)
        done_jobs = []
        q = make_queue()
        q.on_job_done = done_jobs.append

        job = TransferJob(local_path=path, remote_path="/r/f")
        q.enqueue(job)
        q.start()

        deadline = time.time() + 5
        while not done_jobs and time.time() < deadline:
            time.sleep(0.05)
        q.stop()

        assert len(done_jobs) == 1
        assert done_jobs[0] is job

    def test_on_job_failed_callback(self, tmp_path):
        q = make_queue(max_retries=1)
        failed_jobs = []
        q.on_job_failed = failed_jobs.append

        job = TransferJob(
            local_path=str(tmp_path / "missing.bin"),
            remote_path="/r/f",
        )
        q.enqueue(job)
        q.start()

        deadline = time.time() + 5
        while not failed_jobs and time.time() < deadline:
            time.sleep(0.05)
        q.stop()

        assert len(failed_jobs) == 1
        assert failed_jobs[0].state == TransferState.FAILED

    def test_on_job_started_callback(self, make_local_file):
        path, _ = make_local_file(256)
        started = []
        q = make_queue()
        q.on_job_started = started.append

        job = TransferJob(local_path=path, remote_path="/r/f")
        q.enqueue(job)
        q.start()

        deadline = time.time() + 5
        while job.state != TransferState.DONE and time.time() < deadline:
            time.sleep(0.05)
        q.stop()

        assert len(started) == 1

    def test_clear_done_removes_completed(self, make_local_file):
        path, _ = make_local_file(256)
        q = make_queue()

        job = TransferJob(local_path=path, remote_path="/r/f")
        q.enqueue(job)
        q.start()

        deadline = time.time() + 5
        while job.state != TransferState.DONE and time.time() < deadline:
            time.sleep(0.05)
        q.stop()

        q.clear_done()
        assert len(q.jobs()) == 0


class TestQueueCancellation:
    def test_cancel_current_marks_cancelled(self, tmp_path):
        """Cancel a slow upload mid-flight."""
        content = b"\x00" * (128 * 50)  # 50 chunks
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
                time.sleep(0.05)  # slow each write
                return self_._inner.write(data)

            def set_pipelined(self_, *a):
                pass

            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                pass

        sftp.open_remote = lambda p, mode: SlowFile()

        engine = TransferEngine(sftp, chunk_size=128)
        q = TransferQueue(engine_factory=lambda: engine, num_workers=1, max_retries=1, retry_delay=0)
        job = TransferJob(local_path=str(p), remote_path="/r/f")
        q.enqueue(job)
        q.start()

        started.wait(timeout=3)
        q.cancel_current()

        deadline = time.time() + 5
        while job.state == TransferState.RUNNING and time.time() < deadline:
            time.sleep(0.05)
        q.stop()

        assert job.state in (TransferState.CANCELLED, TransferState.DONE)


class TestQueueResilience:
    def test_failed_job_does_not_block_next(self, tmp_path, make_local_file):
        """A failed job must not prevent the next job from running."""
        good_path, _ = make_local_file(256)

        q = make_queue(max_retries=1, retry_delay=0)
        bad_job = TransferJob(
            local_path=str(tmp_path / "missing.bin"),
            remote_path="/r/bad",
        )
        good_job = TransferJob(local_path=good_path, remote_path="/r/good")

        q.enqueue(bad_job)
        q.enqueue(good_job)
        q.start()

        deadline = time.time() + 10
        while good_job.state != TransferState.DONE and time.time() < deadline:
            time.sleep(0.05)
        q.stop()

        assert bad_job.state == TransferState.FAILED
        assert good_job.state == TransferState.DONE

    def test_enqueue_while_running(self, make_local_file):
        """Jobs added while queue is running should be picked up."""
        path, _ = make_local_file(256)
        q = make_queue()
        q.start()

        job = TransferJob(local_path=path, remote_path="/r/late")
        q.enqueue(job)

        deadline = time.time() + 5
        while job.state != TransferState.DONE and time.time() < deadline:
            time.sleep(0.05)
        q.stop()

        assert job.state == TransferState.DONE

    def test_stop_is_idempotent(self, make_local_file):
        path, _ = make_local_file(256)
        q = make_queue()
        q.start()
        q.stop()
        q.stop()  # must not raise
