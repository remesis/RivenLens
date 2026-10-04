# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Validated release extraction and reversible replacement of application files."""

import filecmp
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from releases import ReleaseConfig, version_number

WINDOWS_LAUNCHERS = frozenset(
    {"Start Riven Lens.cmd", "Launch RivenLens - Windows.cmd"}
)
ROOT_FILES = (
    frozenset(
        {"README.md", "LICENSE", "requirements.txt", "Launch RivenLens - Linux.sh"}
    )
    | WINDOWS_LAUNCHERS
)
SOURCE_DIRS = frozenset({"app", "native", "docs"})
LOCAL_NAMES = frozenset({".venv", ".runtimes", "__pycache__", ".git", ".ruff_cache"})
STATE_FILES = frozenset({"native/runtime.json", "native/update-pending.json"})
MANIFEST = "native/data/source-manifest.json"
REQUIRED_FILES = {
    "README.md",
    "LICENSE",
    "requirements.txt",
    MANIFEST,
    "native/bootstrap.py",
    "native/dependencies.py",
    "native/instance.py",
    "native/main.py",
    "native/launch.ps1",
    "native/data/release.json",
    "native/data/catalog.json",
    "app/parser.py",
    "app/reader.py",
}
MAX_FILES = 4096
MAX_EXPANDED = 256 * 1024 * 1024


class UpdateError(Exception):
    pass


def required_files_present(files, *, manifest=True):
    required = REQUIRED_FILES if manifest else REQUIRED_FILES - {MANIFEST}
    return required.issubset(files) and bool(WINDOWS_LAUNCHERS.intersection(files))


def write_json(path, data):
    path = Path(path)
    descriptor, name = tempfile.mkstemp(prefix=".riven-update-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_relative(name):
    if not isinstance(name, str) or "\\" in name or name.startswith("/"):
        raise UpdateError("The update contains an unsafe path.")
    parts = name.rstrip("/").split("/")
    for part in parts:
        if (
            not part
            or part in (".", "..")
            or part.endswith((".", " "))
            or re.search(r'[<>:"|?*\x00-\x1f]', part)
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
        ):
            raise UpdateError("The update contains an unsafe Windows filename.")
    return PurePosixPath(*parts)


def source_path(relative):
    path = safe_relative(relative)
    if any(part.casefold() in LOCAL_NAMES for part in path.parts):
        return False
    if path.as_posix().casefold() in STATE_FILES:
        return False
    return path.as_posix() in ROOT_FILES or path.parts[0] in SOURCE_DIRS


def contained_file(root, relative):
    """Reject reparse points and escaped paths before any replacement or removal."""
    root = Path(root).resolve(strict=True)
    path = root.joinpath(*safe_relative(relative).parts)
    if not path.resolve().is_relative_to(root):
        raise UpdateError("An update path points outside the application folder.")
    current = path
    while current != root:
        if current.is_symlink() or current.is_junction():
            raise UpdateError("Updates cannot replace linked files or folders.")
        current = current.parent
    return path


def extract_release(archive, destination, config, version):
    """Accept a source ZIP with one repository wrapper, or a flat release ZIP."""
    destination = Path(destination)
    destination.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > MAX_FILES or sum(i.file_size for i in entries) > MAX_EXPANDED:
            raise UpdateError("The release archive exceeds the allowed size.")
        names = {}
        for entry in entries:
            if entry.orig_filename != entry.filename:
                raise UpdateError("The release contains an invalid archive filename.")
            name = safe_relative(entry.filename).as_posix()
            mode = entry.external_attr >> 16
            if (
                entry.flag_bits & 1
                or stat.S_ISLNK(mode)
                or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))
            ):
                raise UpdateError("The release contains an unsupported archive entry.")
            if name.casefold() in names:
                raise UpdateError("The release contains duplicate filenames.")
            names[name.casefold()] = (name, entry)
        candidates = [
            name[: -len("native/data/release.json")]
            for name, entry in names.values()
            if not entry.is_dir()
            and (
                name == "native/data/release.json"
                or name.endswith("/native/data/release.json")
            )
        ]
        if len(candidates) != 1 or len(PurePosixPath(candidates[0]).parts) > 1:
            raise UpdateError("This ZIP is not a RivenLens release.")
        prefix = candidates[0]
        extracted = []
        for name, entry in names.values():
            if entry.is_dir() or not name.startswith(prefix):
                continue
            relative = name[len(prefix) :]
            if not source_path(relative):
                continue
            output = contained_file(destination, relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as source, output.open("xb") as target:
                shutil.copyfileobj(source, target, 64 * 1024)
            extracted.append(relative)
    if not required_files_present(extracted):
        raise UpdateError("The release is missing required application files.")
    bundled = ReleaseConfig.load(destination / "native/data/release.json")
    if (
        bundled.version != version
        or bundled.repository.casefold() != config.repository.casefold()
    ):
        raise UpdateError(
            "The release version or repository does not match the update."
        )
    if not version_number(bundled.version):
        raise UpdateError("The release version is invalid.")
    manifest = release_manifest(destination)
    if set(manifest) | {MANIFEST} != set(extracted):
        raise UpdateError("The archive does not match its managed-file list.")
    verify_contents(destination, manifest)
    for name in extracted:
        if name.endswith(".py"):
            compile((destination / name).read_bytes(), name, "exec")
    return sorted(extracted)


def checksum(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def release_manifest(root):
    try:
        data = json.loads(contained_file(root, MANIFEST).read_text(encoding="utf-8"))
        files = data["files"]
        if (
            data["schema"] != 1
            or not isinstance(files, dict)
            or not 0 < len(files) <= MAX_FILES
        ):
            raise ValueError("Invalid manifest")
        if not required_files_present(files, manifest=False):
            raise ValueError("Incomplete manifest")
        seen = set()
        for name, digest in files.items():
            if (
                not source_path(name)
                or name == MANIFEST
                or name.casefold() in seen
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise ValueError("Invalid managed file")
            contained_file(root, name)
            seen.add(name.casefold())
        return files
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UpdateError(
            "The managed-file list is missing or damaged. Install a release into a new folder."
        ) from exc


def verify_contents(root, files):
    for name, digest in files.items():
        path = contained_file(root, name)
        if not path.is_file() or checksum(path) != digest:
            raise UpdateError(
                f"A managed file is missing or modified: {name}. No update was installed."
            )


def ensure_installable(root):
    if (Path(root) / ".git").exists():
        raise UpdateError(
            "This is a Git checkout. Update it through Git, or use a release ZIP in a separate folder."
        )


def installed_files(root):
    ensure_installable(root)
    files = release_manifest(root)
    verify_contents(root, files)
    return sorted([*files, MANIFEST])


def validate_targets(root, files):
    previous = installed_files(root)
    for relative in set(files) - set(previous):
        if contained_file(root, relative).exists():
            raise UpdateError(
                f"The update would replace an unrelated local file: {relative}."
            )
    return previous


def backup_installation(root, stage, files, backup):
    root = Path(root).resolve(strict=True)
    backup = Path(backup)
    backup.mkdir(exist_ok=False)
    previous = validate_targets(root, files)
    if (root / "native/runtime.json").exists():
        previous.append("native/runtime.json")
    touched = sorted(set(previous) | set(files) | {"native/runtime.json"})
    for relative in touched:
        target = contained_file(root, relative)
        if target.exists() and not target.is_file():
            raise UpdateError("A folder conflicts with an updated file.")
        if target.is_file():
            copy = backup / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, copy)
    journal = {
        "previous": previous,
        "touched": touched,
        "files": files,
        "hashes": {name: checksum(backup / name) for name in previous},
    }
    write_json(backup / "journal.json", journal)
    return journal


def replace_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".riven-update-", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def apply_release(root, stage, journal, runtime):
    for relative in journal["files"]:
        replace_file(Path(stage) / relative, contained_file(root, relative))
    for relative in (
        set(journal["previous"]) - set(journal["files"]) - {"native/runtime.json"}
    ):
        contained_file(root, relative).unlink(missing_ok=True)
    write_json(contained_file(root, "native/runtime.json"), {"directory": runtime})


def restore_release(root, backup, journal):
    """Restore all old files, and remove only new files recorded in this transaction."""
    validate_journal(root, backup, journal)
    for relative in journal["touched"]:
        target = contained_file(root, relative)
        previous = Path(backup) / relative
        if relative in journal["previous"]:
            if not target.is_file() or not filecmp.cmp(previous, target, shallow=False):
                replace_file(previous, target)
        else:
            target.unlink(missing_ok=True)


def validate_journal(root, backup, journal):
    if not isinstance(journal, dict):
        raise UpdateError("The recovery journal is damaged.")
    for key in ("previous", "touched", "files"):
        names = journal.get(key)
        if not isinstance(names, list) or len(names) > MAX_FILES + 1:
            raise UpdateError("The recovery journal is damaged.")
        seen = set()
        for name in names:
            if not source_path(name) and name != "native/runtime.json":
                raise UpdateError("The recovery journal contains an unmanaged path.")
            if name.casefold() in seen:
                raise UpdateError("The recovery journal contains duplicate paths.")
            seen.add(name.casefold())
            contained_file(root, name)
            contained_file(backup, name)
    if set(journal["touched"]) != set(journal["previous"]) | set(journal["files"]) | {
        "native/runtime.json"
    }:
        raise UpdateError("The recovery journal is inconsistent.")
    for name in journal["previous"]:
        saved = contained_file(backup, name)
        if not saved.is_file():
            raise UpdateError(f"Recovery backup is missing: {name}")
    hashes = journal.get("hashes")
    if (
        not isinstance(hashes, dict)
        or set(hashes) != set(journal["previous"])
        or any(
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in hashes.values()
        )
    ):
        raise UpdateError("The backup checksums are missing or incomplete.")
    verify_contents(backup, hashes)
