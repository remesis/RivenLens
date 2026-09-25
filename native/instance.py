# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""An OS-released file lease shared only by RivenLens and its installer."""

import errno
import hashlib
import os
from pathlib import Path


class InstallationLease:
    def __init__(self, root, directory=None):
        identity = hashlib.sha256(
            str(Path(root).resolve()).casefold().encode()
        ).hexdigest()[:24]
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        directory = (
            Path(directory)
            if directory
            else base / "Arbitrations/RivenLens Native/locks"
        )
        self.path = directory / (identity + ".lease")
        self.stream = None

    def acquire(self):
        """A crashed process releases this byte lock automatically, including on reboot."""
        import msvcrt

        if self.stream is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            stream.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        self.stream = stream
        return True

    def close(self):
        import msvcrt

        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                stream.close()
