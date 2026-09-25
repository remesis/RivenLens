# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Branding and Windows taskbar identity for RivenLens' own process."""

import ctypes
import sys
from pathlib import Path

ICON = Path(__file__).resolve().parent / "data" / "arbi-logo.png"
APP_USER_MODEL_ID = "Arbitrations.RivenLens"


def set_taskbar_identity():
    """Separate this app from Python's taskbar group before creating any windows."""
    if sys.platform != "win32":
        return False
    try:
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        setter = shell.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        return setter(APP_USER_MODEL_ID) == 0
    except (AttributeError, OSError):
        # A cosmetic shell failure must not prevent the companion from opening.
        return False
