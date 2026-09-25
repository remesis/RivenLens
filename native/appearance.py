# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Window-relative sizing in Qt logical pixels, independent of display DPI."""

import re
from functools import lru_cache
from pathlib import Path

PIXELS = re.compile(r"(-?\d+(?:\.\d+)?)px\b")


def scaled_pixels(text, scale):
    def replace(match):
        value = float(match[1])
        pixels = max(1, round(abs(value) * scale)) if value else 0
        return f"{-pixels if value < 0 else pixels}px"

    return PIXELS.sub(replace, text)


@lru_cache(maxsize=1)
def base_theme():
    directory = Path(__file__).resolve().parent
    theme = (directory / "theme.qss").read_text(encoding="utf-8")
    for name in ("ARROW", "CHECK", "UP", "PURPLE_ARROW"):
        theme = theme.replace(
            f"__{name}__", (directory / "data" / f"{name.lower()}.svg").as_posix()
        )
    return theme


def theme_for(scale=1):
    return scaled_pixels(base_theme(), scale)


def window_scale(width, height):
    # Width drives legibility; the height cap leaves room for all three stages.
    # Qt already handles monitor DPI, so these are logical, not physical pixels.
    return round(
        max(0.85, min(1.65, (width / 660) ** 0.5, height / 780)),
        2,
    )
