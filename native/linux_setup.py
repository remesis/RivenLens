# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Linux launcher: a private user runtime, with no administrator commands."""

import os
from pathlib import Path
import platform
import subprocess
import sys
import venv

from dependencies import INSTALL_FLAGS, lock_digest
from instance import InstallationLease, data_directory

NATIVE = Path(__file__).resolve().parent


def approve(message):
    try:
        return input(message + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        print(
            "Setup needs your approval. Run the launcher from a terminal.",
            file=sys.stderr,
        )
        return False


def pip_ready(python):
    return (
        python.is_file()
        and subprocess.run(
            [str(python), "-m", "pip", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        ).returncode
        == 0
    )


def main():
    if sys.platform != "linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        raise RuntimeError("This launcher currently supports 64-bit Linux x86-64 only.")
    if sys.version_info[:2] not in ((3, 12), (3, 13), (3, 14)):
        raise RuntimeError(
            "Install Python 3.12, 3.13 or 3.14 with its venv package, then retry."
        )
    if (
        os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    ):
        raise RuntimeError(
            "Live capture currently needs an X11 desktop session, not Wayland."
        )
    if not os.environ.get("DISPLAY", "").strip():
        raise RuntimeError("No X11 display is available.")
    lock = NATIVE / "linux-requirements.txt"
    digest = lock_digest(lock)
    runtime = data_directory() / "runtime" / f"linux-{sys.version_info.minor}-{digest}"
    python = runtime / "bin/python"
    lease = InstallationLease(runtime)
    if not lease.acquire():
        raise RuntimeError(
            "Another copy is setting up the Python runtime. Retry once it finishes."
        )
    try:
        approved = False
        if not pip_ready(python):
            if not approve(
                "Set up RivenLens' Python dependencies in your user folder?"
            ):
                return 0
            approved = True
            # Repair an interrupted ensurepip step in place. Do not clear the
            # runtime: a user may have installed the missing venv package since.
            try:
                venv.EnvBuilder(with_pip=True, symlinks=True).create(runtime)
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError(
                    "Python environment setup failed. Install this Python version's "
                    "venv package, then run the launcher again."
                ) from exc
            if not pip_ready(python):
                raise RuntimeError(
                    "Python's pip is unavailable. Install its venv package and retry."
                )
        check = subprocess.run(
            [str(python), "-B", str(NATIVE / "dependencies.py"), str(lock)], check=False
        )
        if check.returncode:
            if not approved:
                if not approve("Download the missing RivenLens dependencies?"):
                    return 0
            subprocess.run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    *INSTALL_FLAGS,
                    "--retries",
                    "0",
                    "--timeout",
                    "30",
                    "--index-url",
                    "https://pypi.org/simple",
                    "-r",
                    str(lock),
                ],
                check=True,
                timeout=1200,
            )
    finally:
        lease.close()
    return subprocess.call([str(python), "-B", str(NATIVE / "bootstrap.py")])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"RivenLens could not start: {exc}", file=sys.stderr)
        sys.exit(1)
