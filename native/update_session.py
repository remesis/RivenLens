# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Launch and observe a per-update helper without blocking or owning its lifetime."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from update_package import write_json

NATIVE = Path(__file__).resolve().parent


class InstallSession:
    def __init__(self, preferences_directory, config, release):
        directory = Path(preferences_directory) / "updates"
        directory.mkdir(parents=True, exist_ok=True)
        self.job = Path(tempfile.mkdtemp(prefix="update-", dir=directory))
        self.archive = self.job / "download.zip"
        self.process = None
        self.handed_off = False
        self.spec = {
            "root": str(NATIVE.parent),
            "python": str(Path(sys.executable).with_name("pythonw.exe")),
            "base_python": str(Path(sys.base_prefix) / "python.exe"),
            "current_version": config.version,
            "repository": config.repository,
            "version": release.version,
            "sha256": release.sha256,
            "size": release.size,
        }

    def start(self):
        for filename in (
            "update_installer.py",
            "update_package.py",
            "releases.py",
            "dependencies.py",
            "instance.py",
        ):
            shutil.copy2(NATIVE / filename, self.job / filename)
        write_json(self.job / "job.json", self.spec)
        write_json(
            self.job / "status.json",
            {"state": "preparing", "message": "Preparing the update…"},
        )
        with (self.job / "install.log").open("ab") as log:
            self.process = subprocess.Popen(
                [
                    self.spec["base_python"],
                    "-B",
                    str(self.job / "update_installer.py"),
                    str(self.job),
                ],
                cwd=self.job,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_NEW_PROCESS_GROUP,
            )

    def progress(self):
        try:
            result = json.loads((self.job / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = {"state": "preparing", "message": "Preparing the update…"}
        if (
            self.process is not None
            and self.process.poll() is not None
            and result.get("state") not in ("failed", "complete", "recovery")
        ):
            return {
                "state": "failed",
                "message": f"The update helper stopped unexpectedly. Keep the backup and log in: {self.job}",
            }
        return result

    def cancel(self):
        if not self.handed_off:
            (self.job / "cancel").touch()


def update_result(directory, argument):
    """Only read updater receipts from this user's RivenLens update directory."""
    if not argument:
        return None
    job = Path(argument).resolve()
    parent = (Path(directory) / "updates").resolve()
    if job.parent != parent or not job.name.startswith("update-"):
        return None
    try:
        result = json.loads((job / "status.json").read_text(encoding="utf-8"))
        if result.get("state") in ("failed", "recovery"):
            return str(result.get("message", "The update could not be completed."))
    except (OSError, ValueError, AttributeError):
        pass
    return ""
