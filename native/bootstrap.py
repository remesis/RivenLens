# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""A small standard-library supervisor for visible, locally logged launch failures."""

import ctypes
import datetime
import faulthandler
import os
import runpy
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

NATIVE = Path(__file__).resolve().parent
LOG_LIMIT = 2 * 1024 * 1024


class DiagnosticLog:
    """Drain diagnostic output without unbounded files or in-memory buffering."""

    encoding = "utf-8"

    def __init__(self, path):
        self.stream = Path(path).open("ab", buffering=0)
        self.guard = threading.Lock()

    def write_bytes(self, data):
        with self.guard:
            remaining = max(0, LOG_LIMIT - os.fstat(self.stream.fileno()).st_size)
            if remaining:
                self.stream.write(data[:remaining])

    def write(self, message):
        self.write_bytes(str(message).encode("utf-8", errors="replace"))
        return len(message)

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return False

    def writable(self):
        return True

    def fileno(self):
        return self.stream.fileno()

    def close(self):
        self.stream.close()


class ErrorReporter:
    """Report once, including under reentrant Qt callbacks; throttle tracebacks."""

    def __init__(self, destination):
        self.destination = destination
        self.notified = False
        self.records = 0
        self.next_record = 0
        self.suppressed = 0
        self.guard = threading.Lock()

    def __call__(self, kind, value, tb):
        with self.guard:
            notify = not self.notified
            self.notified = True
            log = self.records < 25 and time.monotonic() >= self.next_record
            suppressed = self.suppressed
            if log:
                self.records += 1
                self.next_record = time.monotonic() + 10
                self.suppressed = 0
            else:
                self.suppressed += 1
        if log:
            if suppressed:
                print(
                    f"Suppressed {suppressed} additional callback errors.",
                    file=sys.stderr,
                )
            text = "".join(traceback.format_exception(kind, value, tb, limit=20))
            sys.stderr.write(text[:16384] + "\n")
        if notify:
            show_error(
                "RivenLens encountered an error and may not update correctly. "
                "Check any displayed results and restart the tool when convenient.\n\n"
                f"Details are saved locally in:\n{self.destination}"
            )


def log_path():
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    return base / "Arbitrations/RivenLens Native/logs/application.log"


def show_error(message):
    """No Qt dependency: this also works when a Qt import or plugin fails."""
    if os.name == "nt":
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.MessageBoxW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
        ]
        user.MessageBoxW.restype = ctypes.c_int
        user.MessageBoxW(None, message, "RivenLens", 0x10)
    elif sys.stderr:
        print(message, file=sys.stderr)


def run_application(arguments):
    # pythonw may expose None streams even with inherited Windows handles.
    stream = DiagnosticLog(log_path())
    sys.stdout = sys.stderr = stream
    faulthandler.enable(file=stream)

    sys.excepthook = ErrorReporter(log_path())
    sys.argv = [str(NATIVE / "main.py"), *arguments]
    runpy.run_path(str(NATIVE / "main.py"), run_name="__main__")


def supervise(arguments):
    destination = log_path()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.stat().st_size >= LOG_LIMIT:
            try:
                destination.replace(destination.with_suffix(".previous.log"))
            except PermissionError:
                # A running copy owns the log. Let the instance lease report that
                # normally instead of treating it as a startup permissions error.
                pass
        log = DiagnosticLog(destination)
        try:
            log.write(
                f"\nRivenLens launch {datetime.datetime.now().isoformat(timespec='seconds')}\n"
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(NATIVE / "bootstrap.py"),
                    "--run",
                    *arguments,
                ],
                cwd=NATIVE,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            with process.stdout:
                while chunk := process.stdout.read(65536):
                    log.write_bytes(chunk)
            process.wait()
        finally:
            log.close()
        if process.returncode:
            show_error(
                f"RivenLens could not start or stopped unexpectedly.\n\nDetails are saved locally in:\n{destination}"
            )
        return process.returncode
    except Exception as exc:
        show_error(
            f"RivenLens could not start.\n\n{exc}\n\nCheck folder permissions and try again."
        )
        return 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--run"]:
        try:
            run_application(sys.argv[2:])
        except Exception:
            traceback.print_exc()
            sys.exit(1)
    else:
        sys.exit(supervise(sys.argv[1:]))
