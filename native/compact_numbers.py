# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Compact cost labels in the selected interface language, not the OS locale."""

import math

from PySide6.QtCore import QLocale


# (locale, (divisor, suffix, decimal places)). Keep suffix spaces nonbreaking.
# East Asian units group by ten thousand rather than by one thousand.
FORMATS = {
    "en": ("en_US", ((1e3, "k", 0), (1e6, "m", 2), (1e9, "b", 2), (1e12, "t", 2))),
    "de": (
        "de_DE",
        (
            (1e3, "\u00a0Tsd.", 0),
            (1e6, "\u00a0Mio.", 2),
            (1e9, "\u00a0Mrd.", 2),
            (1e12, "\u00a0Bio.", 2),
        ),
    ),
    "fr": (
        "fr_FR",
        (
            (1e3, "\u00a0k", 0),
            (1e6, "\u00a0M", 2),
            (1e9, "\u00a0Md", 2),
            (1e12, "\u00a0Bn", 2),
        ),
    ),
    "it": (
        "it_IT",
        (
            (1e3, "K", 0),
            (1e6, "\u00a0Mln", 2),
            (1e9, "\u00a0Mld", 2),
            (1e12, "\u00a0Bln", 2),
        ),
    ),
    "ru": (
        "ru_RU",
        (
            (1e3, "\u00a0тыс.", 0),
            (1e6, "\u00a0млн", 2),
            (1e9, "\u00a0млрд", 2),
            (1e12, "\u00a0трлн", 2),
        ),
    ),
    "pl": (
        "pl_PL",
        (
            (1e3, "\u00a0tys.", 0),
            (1e6, "\u00a0mln", 2),
            (1e9, "\u00a0mld", 2),
            (1e12, "\u00a0bln", 2),
        ),
    ),
    "es": ("es_ES", ((1e3, "\u00a0mil", 0), (1e6, "\u00a0M", 2), (1e12, "\u00a0B", 2))),
    "pt": (
        "pt_BR",
        (
            (1e3, "\u00a0mil", 0),
            (1e6, "\u00a0mi", 2),
            (1e9, "\u00a0bi", 2),
            (1e12, "\u00a0tri", 2),
        ),
    ),
    "tr": (
        "tr_TR",
        (
            (1e3, "\u00a0B", 0),
            (1e6, "\u00a0Mn", 2),
            (1e9, "\u00a0Mr", 2),
            (1e12, "\u00a0Tn", 2),
        ),
    ),
    "ja": ("ja_JP", ((1e4, "万", 2), (1e8, "億", 2), (1e12, "兆", 2))),
    "zh": ("zh_CN", ((1e4, "万", 2), (1e8, "亿", 2), (1e12, "万亿", 2))),
    "tc": ("zh_TW", ((1e4, "萬", 2), (1e8, "億", 2), (1e12, "兆", 2))),
    "ko": ("ko_KR", ((1e3, "천", 0), (1e4, "만", 2), (1e8, "억", 2), (1e12, "조", 2))),
    "uk": (
        "uk_UA",
        (
            (1e3, "\u00a0тис.", 0),
            (1e6, "\u00a0млн", 2),
            (1e9, "\u00a0млрд", 2),
            (1e12, "\u00a0трлн", 2),
        ),
    ),
    "th": (
        "th_TH",
        (
            (1e3, "\u00a0พัน", 0),
            (1e6, "\u00a0ล้าน", 2),
            (1e9, "\u00a0พันล้าน", 2),
            (1e12, "\u00a0ล้านล้าน", 2),
        ),
    ),
}


def compact_number(value, code):
    """Round only the display; promote rounded values at a unit boundary."""
    if not math.isfinite(value):
        return "∞"
    locale_name, compact_units = FORMATS[code]
    locale = QLocale(locale_name)
    units = ((1, "", 0), *compact_units)
    for index, (divisor, suffix, decimals) in enumerate(units):
        text = locale.toString(float(value / divisor), "f", decimals)
        if index + 1 < len(units):
            next_divisor = units[index + 1][0]
            rounded, _ = locale.toDouble(text)
            if abs(value) >= next_divisor:
                continue
            if abs(rounded) >= next_divisor / divisor:
                value = math.copysign(next_divisor, value)
                continue
        # Ten-thousand units keep up to two decimals, without redundant zeroes.
        if code in ("ja", "zh", "tc", "ko") and decimals:
            text = text.rstrip("0").rstrip(locale.decimalPoint())
        return text + suffix
