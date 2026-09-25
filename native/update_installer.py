# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Separate update worker. Its files and log live outside the installation."""

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from releases import ReleaseConfig
from update_package import (
    UpdateError,
    apply_release,
    backup_installation,
    contained_file,
    extract_release,
    restore_release,
    write_json,
)

HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def status(job, state, message):
    write_json(job / "status.json", {"state": state, "message": message})


def cancelled(job):
    if (job / "cancel").exists():
        raise UpdateError("Update cancelled. Your current version is unchanged.")


def run_command(command, job, timeout=600, cwd=None):
    with (job / "install.log").open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=HIDDEN,
        )
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                cancelled(job)
                if time.monotonic() > deadline:
                    raise UpdateError(
                        "Preparing the update took too long. Please try again."
                    )
                time.sleep(0.2)
            if process.returncode:
                raise UpdateError(
                    "The update could not be prepared. Your current version is unchanged. Details are in the update log."
                )
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


def wait_for_app(pid, job, timeout=60):
    """Wait on this RivenLens PID only. Never terminate the running application."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise UpdateError(
            "Could not wait for RivenLens to close. No files were replaced."
        )
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cancelled(job)
            result = kernel.WaitForSingleObject(handle, 200)
            if result == 0:
                return
            if result != 258:
                break
        raise UpdateError("RivenLens did not finish closing. No files were replaced.")
    finally:
        kernel.CloseHandle(handle)


def runtime_for_update(root, stage, spec, job):
    current = Path(spec["python"]).resolve(strict=True)
    native = root / "native"
    if not current.is_relative_to(native) or current.name.lower() not in (
        "python.exe",
        "pythonw.exe",
    ):
        raise UpdateError(
            "The current Python environment is outside this installation."
        )
    directory = current.parent.parent
    if (root / "requirements.txt").read_bytes() != (
        stage / "requirements.txt"
    ).read_bytes():
        status(job, "preparing", "Preparing the updated Python packages…")
        directory = contained_file(root, "native/.runtimes/" + job.name)
        if directory.exists():
            raise UpdateError(
                "The update environment already exists. Please retry the update."
            )
        directory.parent.mkdir(exist_ok=True)
        run_command([spec["base_python"], "-m", "venv", str(directory)], job)
        run_command(
            [
                str(directory / "Scripts/python.exe"),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--only-binary=:all:",
                "-r",
                str(stage / "requirements.txt"),
            ],
            job,
        )
    executable = directory / "Scripts/python.exe"
    run_command([str(executable), "-m", "pip", "check"], job, timeout=60)
    run_command(
        [
            str(executable),
            "-B",
            "-c",
            "import main; from catalog import Catalog; Catalog()",
        ],
        job,
        timeout=60,
        cwd=stage / "native",
    )
    return directory.relative_to(native).as_posix()


def restart(root, python, job):
    return subprocess.Popen(
        [str(python), "-B", str(root / "native/main.py"), "--update-result", str(job)],
        cwd=root / "native",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=HIDDEN,
    )


def install(job):
    job = Path(job).resolve(strict=True)
    spec = json.loads((job / "job.json").read_text(encoding="utf-8"))
    root = Path(spec["root"]).resolve(strict=True)
    stage, backup = job / "release", job / "backup"
    journal = None
    replacing = False
    app_closed = False
    try:
        if (
            not (root / "native/main.py").is_file()
            or not (root / "Start Riven Lens.cmd").is_file()
        ):
            raise UpdateError("The RivenLens installation could not be identified.")
        config = ReleaseConfig.load(root / "native/data/release.json")
        if (
            config.version != spec["current_version"]
            or config.repository != spec["repository"]
        ):
            raise UpdateError(
                "The installed version changed. Please restart RivenLens and try again."
            )
        cancelled(job)
        status(job, "preparing", "Checking and preparing the update…")
        files = extract_release(job / "download.zip", stage, config, spec["version"])
        runtime = runtime_for_update(root, stage, spec, job)
        cancelled(job)
        # The marker makes the normal launcher stop instead of opening halfway
        # through a replacement. It also points to the recovery journal.
        marker = contained_file(root, "native/update-pending.json")
        if marker.exists():
            raise UpdateError("Another update is already in progress.")
        write_json(marker, {"job": str(job)})
        status(job, "ready", "Ready. Closing RivenLens to install the update…")
        wait_for_app(spec["pid"], job)
        app_closed = True
        status(job, "installing", "Installing the update…")
        journal = backup_installation(root, stage, files, backup)
        replacing = True
        apply_release(root, stage, journal, runtime)
        status(job, "complete", f"RivenLens {spec['version']} was installed.")
        restart(root, root / "native" / runtime / "Scripts/pythonw.exe", job)
    except Exception as exc:
        message = str(exc) or "The update could not be installed."
        if replacing:
            try:
                restore_release(root, backup, journal)
                message = "The update could not be installed. Your previous version was restored."
            except Exception as restore_error:
                status(
                    job,
                    "recovery",
                    f"Automatic recovery needs attention: {restore_error}. Backup: {backup}",
                )
                return 1
        marker = root / "native/update-pending.json"
        if marker.is_file():
            saved = json.loads(marker.read_text(encoding="utf-8"))
            if saved.get("job") == str(job):
                marker.unlink()
        status(job, "failed", message)
        if app_closed:
            try:
                restart(root, spec["python"], job)
            except OSError:
                pass
        return 1
    # Cleanup must never roll back code after the updated app has been launched.
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(install(Path(sys.argv[1])))
