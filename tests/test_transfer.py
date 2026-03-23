"""
Tests for TransferEngine — the heart of upload stability.

We test:
  - clean upload (no prior remote file)
  - resume upload (partial remote file exists)
  - skip upload (remote file already complete)
  - progress callbacks
  - cancellation mid-upload
  - retry on failure
  - retry with reconnect callback
  - exhausted retries
  - missing local file
  - zero-byte file
  - remote size > local size (corrupted partial → restart)
  - exact chunk-boundary edge cases
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, call, patch

import pytest

from sftp_ui.core.transfer import (
    CHUNK_SIZE,
    TransferEngine,
    TransferError,
    TransferJob,
    TransferState,
)
from tests.conftest import FakeSFTPClient


# ── helpers ──────────────────────────────────────────────────────────────────

def make_engine(sftp=None, chunk_size=CHUNK_SIZE):
    return TransferEngine(sftp or FakeSFTPClient(), chunk_size=chunk_size)


def make_job(local_path: str, remote_path: str = "/remote/file.bin") -> TransferJob:
    return TransferJob(local_path=local_path, remote_path=remote_path)


# ── clean upload ─────────────────────────────────────────────────────────────

class TestCleanUpload:
    def test_uploads_full_content(self, make_local_file):
        path, content = make_local_file(1024)
        sftp = FakeSFTPClient()
        engine = make_engine(sftp)
        job = make_job(path)

        engine.upload(job)

        assert sftp.get_content("/remote/file.bin") == content
        assert job.state == TransferState.DONE
        assert job.bytes_done == len(content)
        assert job.total_bytes == len(content)

    def test_state_becomes_done(self, make_local_file):
        path, _ = make_local_file(512)
        engine = make_engine()
        job = make_job(path)
        engine.upload(job)
        assert job.state == TransferState.DONE

    def test_finished_at_set(self, make_local_file):
        path, _ = make_local_file(512)
        engine = make_engine()
        job = make_job(path)
        before = time.time()
        engine.upload(job)
        assert job.finished_at is not None
        assert job.finished_at >= before

    def test_zero_byte_file(self, tmp_path):
        p = tmp_path / "empty.bin"
        p.write_bytes(b"")
        sftp = FakeSFTPClient()
        engine = make_engine(sftp)
        job = make_job(str(p))
        # zero-byte: local_size == remote_size == 0 → skip path
        engine.upload(job)
        # either DONE via skip or DONE normally — content must be empty
        assert job.state == TransferState.DONE

    def test_multi_chunk_upload(self, make_local_file):
        """File larger than chunk → multiple writes."""
        chunk = 256
        content_size = chunk * 5 + 17  # not a clean multiple
        path, content = make_local_file(content_size)
        sftp = FakeSFTPClient()
        engine = make_engine(sftp, chunk_size=chunk)
        job = make_job(path)

        engine.upload(job)

        assert sftp.get_content("/remote/file.bin") == content
        assert job.state == TransferState.DONE


# ── resume ───────────────────────────────────────────────────────────────────

class TestResumeUpload:
    def test_resumes_from_partial(self, make_local_file):
        path, content = make_local_file(1024)
        partial = content[:512]
        sftp = FakeSFTPClient(remote_files={"/remote/file.bin": bytearray(partial)})
        engine = make_engine(sftp, chunk_size=256)
        job = make_job(path)

        engine.upload(job)

        result = sftp.get_content("/remote/file.bin")
        assert result == content
        assert job.state == TransferState.DONE

    def test_resume_skips_already_done(self, make_local_file):
        path, content = make_local_file(512)
        sftp = FakeSFTPClient(remote_files={"/remote/file.bin": bytearray(content)})
        engine = make_engine(sftp)
        job = make_job(path)

        engine.upload(job)

        assert job.state == TransferState.DONE
        assert job.bytes_done == len(content)

    def test_resume_restarts_if_remote_larger(self, make_local_file):
        """If remote is somehow bigger than local (corrupt), restart from 0."""
        path, content = make_local_file(512)
        # Remote has more bytes than local — corrupted
        sftp = FakeSFTPClient(
            remote_files={"/remote/file.bin": bytearray(b"X" * 1024)}
        )
        engine = make_engine(sftp, chunk_size=128)
        job = make_job(path)

        engine.upload(job)

        assert sftp.get_content("/remote/file.bin") == content
        assert job.state == TransferState.DONE

    def test_resume_preserves_first_half(self, make_local_file):
        """The already-uploaded bytes are NOT re-sent."""
        path, content = make_local_file(200)
        written_calls = []

        sftp = FakeSFTPClient(remote_files={"/remote/file.bin": bytearray(content[:100])})
        original_open = sftp.open_remote

        chunks_written = []

        class TrackingFile:
            def __init__(self_, inner):
                self_._inner = inner

            def write(self_, data):
                chunks_written.append(data)
                return self_._inner.write(data)

            def set_pipelined(self_, *a):
                pass

            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                pass

        def tracked_open(path, mode):
            f = original_open(path, mode)
            return TrackingFile(f)

        sftp.open_remote = tracked_open
        engine = make_engine(sftp, chunk_size=50)
        job = make_job(path)
        engine.upload(job)

        # Only the second 100 bytes should have been written
        all_written = b"".join(chunks_written)
        assert all_written == content[100:]


# ── progress callback ─────────────────────────────────────────────────────────

class TestProgressCallback:
    def test_progress_called_at_least_once(self, make_local_file):
        path, content = make_local_file(1024)
        calls = []
        engine = make_engine(chunk_size=256)
        job = make_job(path)

        engine.upload(job, progress_callback=lambda d, t: calls.append((d, t)))

        assert len(calls) > 0

    def test_progress_final_equals_total(self, make_local_file):
        path, content = make_local_file(1024)
        calls = []
        engine = make_engine(chunk_size=256)
        job = make_job(path)

        engine.upload(job, progress_callback=lambda d, t: calls.append((d, t)))

        last_done, last_total = calls[-1]
        assert last_total == len(content)
        assert last_done == len(content)

    def test_progress_monotonically_increasing(self, make_local_file):
        path, _ = make_local_file(2048)
        calls = []
        engine = make_engine(chunk_size=256)
        job = make_job(path)

        engine.upload(job, progress_callback=lambda d, t: calls.append(d))

        for a, b in zip(calls, calls[1:]):
            assert b >= a

    def test_skip_calls_progress_once(self, make_local_file):
        path, content = make_local_file(512)
        sftp = FakeSFTPClient(remote_files={"/remote/file.bin": bytearray(content)})
        calls = []
        engine = make_engine(sftp)
        job = make_job(path)

        engine.upload(job, progress_callback=lambda d, t: calls.append((d, t)))

        assert calls == [(len(content), len(content))]


# ── cancellation ──────────────────────────────────────────────────────────────

class TestCancellation:
    def test_cancel_before_start(self, make_local_file):
        path, _ = make_local_file(4096)
        engine = make_engine(chunk_size=256)
        job = make_job(path)

        engine.upload(job, cancel_flag=lambda: True)

        assert job.state == TransferState.CANCELLED

    def test_cancel_mid_upload(self, make_local_file):
        path, content = make_local_file(2048)
        engine = make_engine(chunk_size=256)
        job = make_job(path)

        call_count = [0]

        def cancel_after_2():
            call_count[0] += 1
            return call_count[0] > 2

        engine.upload(job, cancel_flag=cancel_after_2)

        assert job.state == TransferState.CANCELLED
        assert job.bytes_done < len(content)

    def test_cancel_does_not_raise(self, make_local_file):
        path, _ = make_local_file(512)
        engine = make_engine(chunk_size=64)
        job = make_job(path)
        # Should not raise TransferError
        engine.upload(job, cancel_flag=lambda: True)


# ── error handling ────────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_missing_local_file(self, tmp_path):
        engine = make_engine()
        job = make_job(str(tmp_path / "nope.bin"))

        with pytest.raises(TransferError, match="not found"):
            engine.upload(job)

        assert job.state == TransferState.FAILED
        assert "not found" in job.error

    def test_sftp_write_error_raises_transfer_error(self, make_local_file):
        path, _ = make_local_file(512)

        class ErrorSFTP(FakeSFTPClient):
            def open_remote(self, path, mode):
                raise OSError("disk full on remote")

        engine = make_engine(ErrorSFTP(), chunk_size=64)
        job = make_job(path)

        with pytest.raises(TransferError):
            engine.upload(job)

        assert job.state == TransferState.FAILED

    def test_sftp_stat_error_raises_transfer_error(self, make_local_file):
        path, _ = make_local_file(512)

        class StatErrorSFTP(FakeSFTPClient):
            def remote_size(self, remote_path):
                raise OSError("network timeout")

        engine = make_engine(StatErrorSFTP())
        job = make_job(path)

        with pytest.raises(TransferError):
            engine.upload(job)

        assert job.state == TransferState.FAILED

    def test_failed_job_error_message_set(self, tmp_path):
        engine = make_engine()
        job = make_job(str(tmp_path / "missing.bin"))

        try:
            engine.upload(job)
        except TransferError:
            pass

        assert job.error is not None
        assert len(job.error) > 0


# ── retry ─────────────────────────────────────────────────────────────────────

class TestRetry:
    def test_succeeds_on_second_attempt(self, make_local_file):
        path, content = make_local_file(512)
        attempt = [0]
        sftp = FakeSFTPClient()
        original_open = sftp.open_remote

        def flaky_open(p, mode):
            attempt[0] += 1
            if attempt[0] == 1:
                raise OSError("transient error")
            return original_open(p, mode)

        sftp.open_remote = flaky_open
        engine = make_engine(sftp, chunk_size=128)
        job = make_job(path)

        engine.upload_with_retry(job, max_retries=3, retry_delay=0)

        assert job.state == TransferState.DONE
        assert sftp.get_content("/remote/file.bin") == content

    def test_fails_after_max_retries(self, make_local_file):
        path, _ = make_local_file(512)

        class AlwaysErrorSFTP(FakeSFTPClient):
            def open_remote(self, p, mode):
                raise OSError("always broken")

        engine = make_engine(AlwaysErrorSFTP())
        job = make_job(path)

        with pytest.raises(TransferError):
            engine.upload_with_retry(job, max_retries=3, retry_delay=0)

        assert job.state == TransferState.FAILED

    def test_retry_count_respected(self, make_local_file):
        path, _ = make_local_file(256)
        attempts = [0]

        class CountingSFTP(FakeSFTPClient):
            def open_remote(self, p, mode):
                attempts[0] += 1
                raise OSError("nope")

        engine = make_engine(CountingSFTP())
        job = make_job(path)

        with pytest.raises(TransferError):
            engine.upload_with_retry(job, max_retries=4, retry_delay=0)

        assert attempts[0] == 4

    def test_reconnect_callback_called_between_retries(self, make_local_file):
        path, content = make_local_file(256)
        reconnect_calls = [0]
        attempt = [0]
        sftp = FakeSFTPClient()
        original_open = sftp.open_remote

        def flaky_open(p, mode):
            attempt[0] += 1
            if attempt[0] < 3:
                raise OSError("drop")
            return original_open(p, mode)

        sftp.open_remote = flaky_open
        engine = make_engine(sftp)
        job = make_job(path)

        def reconnect():
            reconnect_calls[0] += 1

        engine.upload_with_retry(
            job, max_retries=5, retry_delay=0, reconnect_callback=reconnect
        )

        assert reconnect_calls[0] == 2  # called after attempt 1 and 2
        assert job.state == TransferState.DONE

    def test_cancel_during_retry_stops_loop(self, make_local_file):
        path, _ = make_local_file(256)

        class AlwaysErrorSFTP(FakeSFTPClient):
            def open_remote(self, p, mode):
                raise OSError("broken")

        engine = make_engine(AlwaysErrorSFTP())
        job = make_job(path)

        cancel_after = [1]

        def cancel_flag():
            cancel_after[0] -= 1
            return cancel_after[0] < 0

        # Should not exhaust retries — cancelled first
        engine.upload_with_retry(
            job, max_retries=100, retry_delay=0, cancel_flag=cancel_flag
        )
        assert job.state in (TransferState.CANCELLED, TransferState.FAILED)

    def test_retry_resumes_where_left_off(self, make_local_file):
        """
        First attempt uploads half, fails; second attempt should resume
        (remote partial file contains first half).
        """
        path, content = make_local_file(400)
        chunk = 100
        sftp = FakeSFTPClient()
        original_open = sftp.open_remote
        attempt = [0]

        def partial_open(p, mode):
            attempt[0] += 1
            if attempt[0] == 1:
                # Write first 200 bytes then explode
                class PartialFile:
                    def __init__(self_):
                        self_._written = 0

                    def write(self_, data):
                        if self_._written >= 200:
                            raise OSError("network cut")
                        take = min(len(data), 200 - self_._written)
                        sftp._files.setdefault(p, bytearray()).extend(data[:take])
                        self_._written += take
                        if self_._written >= 200:
                            raise OSError("network cut")
                        return take

                    def set_pipelined(self_, *a):
                        pass

                    def __enter__(self_):
                        return self_

                    def __exit__(self_, *a):
                        pass

                sftp._files[p] = bytearray(content[:0])  # start fresh
                return PartialFile()
            # Second attempt: use real fake
            return original_open(p, mode)

        sftp.open_remote = partial_open
        engine = make_engine(sftp, chunk_size=chunk)
        job = make_job(path)

        engine.upload_with_retry(job, max_retries=3, retry_delay=0)

        final = sftp.get_content("/remote/file.bin")
        assert final == content
        assert job.state == TransferState.DONE


# ── edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_exactly_one_chunk(self, tmp_path):
        content = os.urandom(CHUNK_SIZE)
        p = tmp_path / "exact.bin"
        p.write_bytes(content)
        sftp = FakeSFTPClient()
        engine = make_engine(sftp)
        job = make_job(str(p))
        engine.upload(job)
        assert sftp.get_content("/remote/file.bin") == content

    def test_one_byte_file(self, tmp_path):
        content = b"\x42"
        p = tmp_path / "one.bin"
        p.write_bytes(content)
        sftp = FakeSFTPClient()
        engine = make_engine(sftp)
        job = make_job(str(p))
        engine.upload(job)
        assert sftp.get_content("/remote/file.bin") == content

    def test_progress_attribute_after_done(self, make_local_file):
        path, content = make_local_file(512)
        engine = make_engine(chunk_size=128)
        job = make_job(path)
        engine.upload(job)
        assert job.progress == 1.0

    def test_progress_attribute_before_upload(self, make_local_file):
        path, _ = make_local_file(512)
        job = make_job(path)
        assert job.progress == 0.0

    def test_filename_property(self, tmp_path):
        p = tmp_path / "myfile.tar.gz"
        p.write_bytes(b"x")
        job = make_job(str(p))
        assert job.filename == "myfile.tar.gz"


# ── upload permission preservation ────────────────────────────────────────────

class TestUploadPermissions:
    def test_chmod_called_after_upload(self, tmp_path):
        """After a successful upload chmod is called with the local file's mode."""
        p = tmp_path / "script.sh"
        p.write_bytes(b"#!/bin/sh\necho hi\n")
        p.chmod(0o755)

        sftp = FakeSFTPClient()
        engine = make_engine(sftp)
        job = make_job(str(p))
        engine.upload(job)

        import stat
        expected_mode = stat.S_IMODE(p.stat().st_mode)
        assert sftp.get_chmod("/remote/file.bin") == expected_mode

    def test_chmod_not_called_on_skip(self, make_local_file):
        """When the file is already complete (skip path), chmod is not called."""
        path, content = make_local_file(512)
        sftp = FakeSFTPClient(remote_files={"/remote/file.bin": bytearray(content)})
        engine = make_engine(sftp)
        job = make_job(path)
        engine.upload(job)
        # File was skipped (already done) — chmod should not have been called
        assert sftp.get_chmod("/remote/file.bin") is None

    def test_chmod_failure_does_not_fail_job(self, make_local_file):
        """A chmod error must not change the job state from DONE to FAILED."""
        path, content = make_local_file(256)

        class ChmodErrorSFTP(FakeSFTPClient):
            def chmod(self, remote_path: str, mode: int) -> None:
                raise OSError("permission denied")

        sftp = ChmodErrorSFTP()
        engine = make_engine(sftp)
        job = make_job(path)
        engine.upload(job)   # must not raise
        assert job.state.name == "DONE"
