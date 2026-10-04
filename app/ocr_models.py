# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Pinned local models. Only the setup UI may authorize their download."""

import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys

from dependencies import lock_digest, locked_packages
from instance import data_directory
from localization import LANGUAGES, LATIN_UI_LANGUAGES

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "native/rapid-requirements.txt"
BUILD_LOCK = ROOT / "native/rapid-build-requirements.txt"
MODEL_HOST = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/"
MODELS = {
    "caption-det": (
        "PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx",
        "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    ),
    "caption-rec": (
        "PP-OCRv5/rec/en_PP-OCRv5_rec_mobile.onnx",
        "c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8",
    ),
    "det": (
        "PP-OCRv6/det/PP-OCRv6_det_small.onnx",
        "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    ),
    "rec": (
        "PP-OCRv6/rec/PP-OCRv6_rec_small.onnx",
        "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
    ),
    "ko": (
        "PP-OCRv5/rec/korean_PP-OCRv5_rec_mobile.onnx",
        "cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b",
    ),
    "ru": (
        "PP-OCRv5/rec/eslav_PP-OCRv5_rec_mobile.onnx",
        "08705d6721849b1347d26187f15a5e362c431963a2a62bfff4feac578c489aab",
    ),
    "th": (
        "PP-OCRv5/rec/th_PP-OCRv5_rec_mobile.onnx",
        "de541dd83161c241ff426f7ecfd602a0ba77d686cf3ab9a6c255ea82fd08006e",
    ),
    "cls": (
        "PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    ),
}


def selection(language):
    if language not in LANGUAGES:
        raise ValueError("Unsupported OCR language")
    recognizer, script = {
        "uk": ("ru", "eslav"),
        "ru": ("ru", "eslav"),
        "ko": ("ko", "korean"),
        "th": ("th", "th"),
    }.get(language, ("rec", "ch"))
    return "det", recognizer, script


def model_directory():
    return data_directory() / "ocr/models"


def model_path(key):
    return model_directory() / Path(MODELS[key][0]).name


def valid_model(key):
    path = model_path(key)
    try:
        if path.is_symlink() or not 0 < path.stat().st_size <= 40 * 1024 * 1024:
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == MODELS[key][1]
    except OSError:
        return False


def required_models(language):
    det, rec, _ = selection(language)
    extra = (
        ("caption-det", "caption-rec", "rec") if language in LATIN_UI_LANGUAGES else ()
    )
    return tuple(dict.fromkeys((det, rec, "cls", *extra)))


def runtime_directory():
    digest = lock_digest(BUILD_LOCK, LOCK)
    return (
        data_directory()
        / "ocr/runtime"
        / f"{sys.platform}-{sys.version_info.major}.{sys.version_info.minor}-{digest}"
    )


def runtime_packages():
    packages = locked_packages(BUILD_LOCK)
    for name, version in locked_packages(LOCK).items():
        if name in packages and packages[name] != version:
            raise ValueError("OCR dependency locks disagree.")
        packages[name] = version
    return packages


def runtime_ready(directory=None):
    directory = directory or runtime_directory()
    try:
        manifest = json.loads((directory / "installed.json").read_text("utf-8"))
        wanted = runtime_packages()
        installed = {
            re.sub(
                r"[-_.]+", "-", distribution.metadata["Name"]
            ).lower(): distribution.version
            for distribution in importlib.metadata.distributions(path=[str(directory)])
        }
        return manifest == wanted and all(
            installed.get(name) == version for name, version in wanted.items()
        )
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return False


def ready(language):
    return runtime_ready() and all(
        valid_model(key) for key in required_models(language)
    )


def activate_runtime():
    if not runtime_ready():
        raise RuntimeError(
            "RapidOCR is not installed. Choose Start OCR to set up this language."
        )
    base_lock = (
        ROOT / "native/linux-requirements.txt"
        if sys.platform == "linux"
        else ROOT / "requirements.txt"
    )
    base = locked_packages(base_lock)
    for name in ("numpy", "pillow"):
        if importlib.metadata.version(name) != base[name]:
            raise RuntimeError("The application's shared OCR dependencies need repair.")
    directory = str(runtime_directory())
    if directory not in sys.path:
        sys.path.append(directory)
