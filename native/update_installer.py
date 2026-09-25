# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Separate update worker. Its files and log live outside the installation."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from contextlib import contextmanager

from dependencies import INSTALL_FLAGS, locked_packages
from instance import InstallationLease
from releases import ReleaseConfig
from update_package import (
    UpdateError,
    apply_release,
    backup_installation,
    contained_file,
    extract_release,
    ensure_installable,
    installed_files,
    validate_targets,
    restore_release,
    write_json,
    checksum,
)

HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class UpdateBusy(UpdateError):
    pass


@contextmanager
def job_guard(job):
    """Installation and recovery may never replace files at the same time."""
    import msvcrt

    with (job / "helper.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise UpdateBusy(
                "This update is still running. Wait for it to finish before recovery."
            ) from exc
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def clear_pending(root, job):
    marker = contained_file(root, "native/update-pending.json")
    if not marker.exists():
        return
    saved = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or saved.get("job") != str(job):
        raise UpdateError(
            "The pending update belongs to another job. Its marker was left untouched."
        )
    marker.unlink()


def claim_pending(root, job):
    """Exclusively claim this installation, including across different update jobs."""
    marker = contained_file(root, "native/update-pending.json")
    try:
        with marker.open("x", encoding="utf-8") as stream:
            json.dump({"job": str(job)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise UpdateError("Another update is already in progress.") from exc


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


def wait_for_app(root, job, timeout=60):
    """Acquire RivenLens' file lease, without inspecting any running process."""
    lease = InstallationLease(root)
    try:
        deadline = time.monotonic() + timeout
        while True:
            if timeout:
                cancelled(job)
            if lease.acquire():
                return lease
            if time.monotonic() >= deadline:
                raise UpdateBusy(
                    "RivenLens is still open or being updated. No files were replaced."
                )
            time.sleep(0.2)
    except BaseException:
        lease.close()
        raise


def runtime_for_update(root, stage, spec, job):
    locked_packages(stage / "requirements.txt")
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
                *INSTALL_FLAGS,
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
        [
            str(python),
            "-B",
            str(root / "native/bootstrap.py"),
            "--update-result",
            str(job),
        ],
        cwd=root / "native",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=HIDDEN,
    )


def install(job):
    job = Path(job).resolve(strict=True)
    try:
        with job_guard(job):
            return _install(job)
    except UpdateBusy:
        return 1  # The active helper owns the status file as well as the lock.
    except Exception as exc:
        status(job, "failed", f"The update could not start: {exc}")
        return 1


def _install(job):
    job = Path(job).resolve(strict=True)
    spec = json.loads((job / "job.json").read_text(encoding="utf-8"))
    root = Path(spec["root"]).resolve(strict=True)
    stage, backup = job / "release", job / "backup"
    journal = None
    replacing = False
    app_closed = False
    lease = None
    committed = False
    try:
        ensure_installable(root)
        installed_files(root)
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
        digest, size = spec.get("sha256"), spec.get("size")
        archive = job / "download.zip"
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or type(size) is not int
            or not 0 < size <= 256 * 1024 * 1024
            or archive.stat().st_size != size
            or checksum(archive) != digest
        ):
            raise UpdateError(
                "The downloaded release failed its integrity check. No files were replaced."
            )
        files = extract_release(job / "download.zip", stage, config, spec["version"])
        validate_targets(root, files)
        runtime = runtime_for_update(root, stage, spec, job)
        cancelled(job)
        # The marker makes the normal launcher stop instead of opening halfway
        # through a replacement. It also points to the recovery journal.
        claim_pending(root, job)
        status(job, "ready", "Ready. Closing RivenLens to install the update…")
        lease = wait_for_app(root, job)
        app_closed = True
        status(job, "backing_up", "Saving the current version…")
        journal = backup_installation(root, stage, files, backup)
        status(job, "installing", "Installing the update…")
        replacing = True
        apply_release(root, stage, journal, runtime)
        status(job, "complete", f"RivenLens {spec['version']} was installed.")
        committed = True
        clear_pending(root, job)
        lease.close()
        lease = None
        restart(root, root / "native" / runtime / "Scripts/pythonw.exe", job)
    except Exception as exc:
        if committed:
            # A completed replacement is never undone for a cleanup/restart error.
            status(
                job,
                "complete",
                f"RivenLens {spec['version']} was installed. Reopen it with the launcher. {exc}",
            )
            return 1
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
        try:
            clear_pending(root, job)
        except (OSError, ValueError, UpdateError) as marker_error:
            message += f" The pending marker needs attention: {marker_error}. Recovery folder: {job}"
        status(job, "failed", message)
        if app_closed:
            if lease is not None:
                lease.close()
                lease = None
            try:
                restart(root, spec["python"], job)
            except OSError:
                pass
        return 1
    finally:
        if lease is not None:
            lease.close()
    return 0


def recover(job, expected_root, *, inspect_only=False):
    """Explicit launcher recovery, using only this installation's verified journal."""
    job = Path(job).resolve(strict=True)
    root = Path(expected_root).resolve(strict=True)
    with job_guard(job):
        spec = json.loads((job / "job.json").read_text(encoding="utf-8"))
        if Path(spec["root"]).resolve(strict=True) != root:
            raise UpdateError("This recovery job belongs to another installation.")
        ensure_installable(root)
        marker = contained_file(root, "native/update-pending.json")
        saved = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(saved, dict) or saved.get("job") != str(job):
            raise UpdateError("The pending marker does not identify this recovery job.")
        lease = wait_for_app(root, job, timeout=0)
        try:
            state = json.loads((job / "status.json").read_text(encoding="utf-8"))[
                "state"
            ]
            if state == "complete":
                verify_version(root, spec, "version")
                clear_pending(root, job)
                return 0
            if inspect_only:
                return 2  # Recovery needs the user's confirmation.
            backup = job / "backup"
            if (backup / "journal.json").is_file():
                journal = json.loads(
                    (backup / "journal.json").read_text(encoding="utf-8")
                )
                restore_release(root, backup, journal)
            else:
                if state not in ("preparing", "ready", "backing_up", "failed"):
                    raise UpdateError(
                        "The backup journal is missing. Leave the recovery folder intact."
                    )
                verify_version(root, spec, "current_version")
            clear_pending(root, job)
            status(
                job,
                "failed",
                "The interrupted update was recovered. Your previous version is ready.",
            )
        finally:
            lease.close()
    return 0


def verify_version(root, spec, version_key):
    installed_files(root)
    config = ReleaseConfig.load(root / "native/data/release.json")
    if (
        config.version != spec[version_key]
        or config.repository.casefold() != spec["repository"].casefold()
    ):
        raise UpdateError("The installed version no longer matches this recovery job.")


if __name__ == "__main__":
    try:
        result = (
            recover(
                Path(sys.argv[2]),
                Path(sys.argv[3]),
                inspect_only=sys.argv[1] == "--inspect",
            )
            if sys.argv[1] in ("--recover", "--inspect")
            else install(Path(sys.argv[1]))
        )
    except Exception as exc:
        print(f"RivenLens update: {exc}", file=sys.stderr)
        result = 1
    sys.exit(result)
