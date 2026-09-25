# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Validate the wheel lock and check installed versions without importing Qt."""

import importlib.metadata as metadata
import re
import sys
from pathlib import Path

INSTALL_FLAGS = (
    "--disable-pip-version-check",
    "--no-input",
    "--only-binary=:all:",
    "--require-hashes",
)


def locked_packages(path):
    text = Path(path).read_text(encoding="utf-8").replace("\\\n", " ")
    packages = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)((?:\s+--hash=sha256:[0-9a-f]{64})+)",
            line,
        )
        if not match:
            raise ValueError(
                "Dependencies must be pinned and include SHA-256 wheel hashes."
            )
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        if name in packages:
            raise ValueError("Duplicate dependency in the wheel lock.")
        packages[name] = match[2]
    if not packages:
        raise ValueError("The dependency lock is empty.")
    return packages


def installed(path):
    try:
        return all(
            metadata.version(name) == version
            for name, version in locked_packages(path).items()
        )
    except (OSError, ValueError, metadata.PackageNotFoundError):
        return False


if __name__ == "__main__":
    sys.exit(0 if installed(sys.argv[1]) else 1)
