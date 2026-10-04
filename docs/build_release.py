# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Build a managed RivenLens.zip from a clean, committed source checkout."""

import argparse
import hashlib
import io
import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "native"))
from update_package import (  # noqa: E402
    MANIFEST,
    required_files_present,
    source_path,
)


def build(destination):
    destination = Path(destination).resolve()
    if destination.is_relative_to(ROOT):
        raise ValueError("Choose an output ZIP outside the source checkout.")

    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(ROOT), *arguments])

    if git("status", "--porcelain").strip():
        raise ValueError("Commit and review all changes before building a release.")
    files = {}
    with zipfile.ZipFile(
        io.BytesIO(git("archive", "--format=zip", "HEAD"))
    ) as committed:
        for entry in committed.infolist():
            name = entry.filename
            if entry.is_dir() or name == MANIFEST or not source_path(name):
                continue
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Release files must not be symbolic links.")
            files[name] = committed.read(entry)
    if not required_files_present(files, manifest=False):
        raise ValueError("Required application files are not committed.")
    hashes = {name: hashlib.sha256(files[name]).hexdigest() for name in sorted(files)}
    files[MANIFEST] = (
        json.dumps({"schema": 1, "files": hashes}, indent=2) + "\n"
    ).encode("utf-8")
    with zipfile.ZipFile(destination, "x") as archive:
        for name in sorted(files):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (
                stat.S_IFREG | (0o755 if name.endswith(".sh") else 0o644)
            ) << 16
            archive.writestr(entry, files[name], zipfile.ZIP_DEFLATED, compresslevel=9)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    options = parser.parse_args()
    print(build(options.destination))
