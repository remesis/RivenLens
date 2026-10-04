# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""An OS-released file lease shared only by RivenLens and its installer."""

import ctypes
import errno
import hashlib
import os
import sys
from pathlib import Path
from uuid import UUID


def local_appdata():
    """Use Windows' current known folder, including redirected user profiles."""
    shell = ctypes.WinDLL("shell32")
    ole = ctypes.WinDLL("ole32")
    shell.SHGetKnownFolderPath.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    folder = (ctypes.c_ubyte * 16).from_buffer_copy(
        UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le
    )
    pointer = ctypes.c_void_p()
    try:
        result = shell.SHGetKnownFolderPath(
            ctypes.byref(folder), 0, None, ctypes.byref(pointer)
        )
        if result < 0 or not pointer.value:
            raise OSError("Windows could not locate the local application data folder.")
        path = Path(ctypes.wstring_at(pointer))
        if not path.is_absolute():
            raise OSError("Windows returned an invalid application data folder.")
        return path
    finally:
        ole.CoTaskMemFree(pointer)


def data_directory():
    """The same location for application settings, leases and update recovery."""
    if sys.platform != "win32":
        configured = os.environ.get("XDG_DATA_HOME", "")
        base = (
            Path(configured)
            if configured and Path(configured).is_absolute()
            else Path.home() / ".local/share"
        )
        return base / "Arbitrations/RivenLens Native"
    return local_appdata() / "Arbitrations" / "RivenLens Native"


class InstallationLease:
    def __init__(self, root, directory=None):
        resolved = str(Path(root).resolve())
        identity = hashlib.sha256(
            (resolved.casefold() if sys.platform == "win32" else resolved).encode()
        ).hexdigest()[:24]
        directory = (
            Path(directory) if directory is not None else data_directory() / "locks"
        )
        self.path = directory / (identity + ".lease")
        self.stream = None

    def acquire(self):
        """A crashed process releases this byte lock automatically, including on reboot."""
        if self.stream is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        self.stream = stream
        return True

    def close(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()


if __name__ == "__main__":
    import argparse
    import json

    arguments = argparse.ArgumentParser(
        description="Resolve RivenLens' local data folder."
    )
    arguments.add_argument("--data-directory", action="store_true", required=True)
    arguments.parse_args()
    # ASCII JSON survives PowerShell's legacy console encodings for Unicode paths.
    print(json.dumps(str(data_directory())))
