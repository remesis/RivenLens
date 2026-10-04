# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Choose an on-device recognizer without importing another platform's APIs."""

import sys

from localization import LANGUAGES


def backend_for(language):
    if language not in LANGUAGES:
        raise ValueError("Unsupported game language")
    return (
        "windows"
        if sys.platform == "win32" and language not in ("uk", "th")
        else "rapid"
    )


def available_languages():
    if sys.platform != "win32":
        return set()
    from windows_ocr import available_languages as installed

    return installed()


def recognizer_tag(language, available):
    if backend_for(language) != "windows":
        return None
    from windows_ocr import recognizer_tag as tag

    return tag(language, available)


def LocalOCR(language="en"):
    if backend_for(language) == "windows":
        from windows_ocr import LocalOCR as Recognizer
    else:
        from rapid_ocr import RapidOCR as Recognizer
    return Recognizer(language)
