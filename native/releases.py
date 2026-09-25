# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Public release metadata. An empty repository disables update connections."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

CONFIG_PATH = Path(__file__).resolve().parent / "data" / "release.json"
DOWNLOAD_HOSTS = frozenset(
    {
        "github.com",
        "api.github.com",
        "codeload.github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
)


def version_number(value):
    """Compare stable release numbers numerically, never lexicographically."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"v?(0|[1-9]\d{0,7})\.(0|[1-9]\d{0,7})\.(0|[1-9]\d{0,7})", value
    )
    return tuple(map(int, match.groups())) if match else None


def github_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        url = urlsplit(value)
        return (
            url.scheme == "https"
            and url.hostname in DOWNLOAD_HOSTS
            and url.port in (None, 443)
            and not url.username
            and not url.password
            and not url.fragment
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class ReleaseConfig:
    version: str
    repository: str = ""
    asset_name: str = ""

    @property
    def enabled(self):
        return bool(
            self.repository and self.asset_name and version_number(self.version)
        )

    @property
    def api_url(self):
        return f"https://api.github.com/repos/{self.repository}/releases/latest"

    @classmethod
    def load(cls, path=CONFIG_PATH):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version = data["version"]
            repository = data.get("repository", "")
            asset_name = data.get("asset_name", "")
            if not version_number(version):
                raise ValueError("Invalid version")
            if not isinstance(repository, str) or (
                repository
                and not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}",
                    repository,
                )
            ):
                raise ValueError("Invalid repository")
            if not isinstance(asset_name, str) or (
                asset_name
                and not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.zip", asset_name
                )
            ):
                raise ValueError("Invalid asset name")
            return cls(version, repository, asset_name)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return cls("0.0.0")


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    filename: str
    sha256: str = ""
    size: int = 0


def newer_release(config, payload):
    """Use only a newer stable release from the configured public repository."""
    if not config.enabled or not isinstance(payload, dict):
        return None
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        return None
    tag = payload.get("tag_name")
    number = version_number(tag)
    if number is None or number <= version_number(config.version):
        return None
    version = ".".join(map(str, number))
    filename = f"RivenLens-{version}.zip"
    assets = payload.get("assets", [])
    if not isinstance(assets, list):
        return None
    expected = f"https://github.com/{config.repository}/releases/download/{quote(tag)}/{quote(config.asset_name)}"
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != config.asset_name:
            continue
        if (
            asset.get("state") != "uploaded"
            or asset.get("browser_download_url") != expected
        ):
            continue
        size = asset.get("size")
        if type(size) is not int or size <= 0:
            return None
        digest = asset.get("digest") or ""
        if not isinstance(digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", digest
        ):
            return None
        return Release(
            version, expected, filename, digest.removeprefix("sha256:"), size
        )
    return None
