#!/usr/bin/env python3
"""
openSFTP launcher. Creates a virtual environment, installs dependencies,
and starts the application. One command, all platforms.

Usage:
    python3 run.py              # Launch the app
    python3 run.py --test       # Run the test suite
    python3 run.py --setup      # Only create venv + install deps
    python3 run.py --clean      # Remove the virtual environment
"""
import os
import platform
import shutil
import subprocess
import sys
import venv

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(ROOT, ".venv")
IS_WIN = platform.system() == "Windows"
PYTHON = os.path.join(VENV_DIR, "Scripts" if IS_WIN else "bin", "python")
PIP = os.path.join(VENV_DIR, "Scripts" if IS_WIN else "bin", "pip")


def _print(msg: str) -> None:
    print(f"[openSFTP] {msg}")


def ensure_venv() -> None:
    if os.path.isfile(PYTHON):
        return
    _print("Creating virtual environment ...")
    venv.create(VENV_DIR, with_pip=True, clear=True)
    _print("Virtual environment ready.")


def install_deps(dev: bool = False) -> None:
    req_file = "requirements-dev.txt" if dev else "requirements.txt"
    req_path = os.path.join(ROOT, req_file)

    # Check if deps are already installed by looking for a stamp file
    stamp = os.path.join(VENV_DIR, f".{req_file}.stamp")
    if os.path.isfile(stamp):
        current_hash = _file_hash(req_path)
        try:
            with open(stamp) as f:
                if f.read().strip() == current_hash:
                    return
        except OSError:
            pass

    _print(f"Installing dependencies from {req_file} ...")
    subprocess.check_call(
        [PIP, "install", "-q", "-r", req_path],
        cwd=ROOT,
    )
    # Write stamp so we skip next time
    with open(stamp, "w") as f:
        f.write(_file_hash(req_path))
    _print("Dependencies installed.")


def _file_hash(path: str) -> str:
    import hashlib
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_app() -> int:
    _print("Starting openSFTP ...")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(ROOT, "src")
    return subprocess.call(
        [PYTHON, "-m", "sftp_ui.app"],
        cwd=ROOT,
        env=env,
    )


def run_tests(extra_args: list[str]) -> int:
    _print("Running tests ...")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(ROOT, "src")
    pytest = os.path.join(VENV_DIR, "Scripts" if IS_WIN else "bin", "pytest")
    cmd = [pytest, "tests/", "--ignore=tests/test_e2e_screenshots.py", "-q"]
    cmd.extend(extra_args)
    return subprocess.call(cmd, cwd=ROOT, env=env)


def clean() -> None:
    if os.path.isdir(VENV_DIR):
        _print("Removing virtual environment ...")
        shutil.rmtree(VENV_DIR)
        _print("Clean.")
    else:
        _print("Nothing to clean.")


def main() -> int:
    args = sys.argv[1:]

    if "--clean" in args:
        clean()
        return 0

    if "--help" in args or "-h" in args:
        print(__doc__)
        return 0

    ensure_venv()

    is_test = "--test" in args
    is_setup = "--setup" in args

    install_deps(dev=is_test)

    if is_setup:
        _print("Setup complete. Run 'python3 run.py' to start.")
        return 0

    if is_test:
        extra = [a for a in args if a != "--test"]
        return run_tests(extra)

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
