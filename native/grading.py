# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Displayed-value grades, including rank, curses and faction multipliers."""

import math

FORMATS = {"2p0n": (2, False), "3p0n": (3, False), "2p1n": (2, True), "3p1n": (3, True)}
GRADES = [
    ("S", 9.5, 10),
    ("A+", 7.5, 9.5),
    ("A", 5.5, 7.5),
    ("A-", 3.5, 5.5),
    ("B+", 1.5, 3.5),
    ("B", -1.5, 1.5),
    ("B-", -3.5, -1.5),
    ("C+", -5.5, -3.5),
    ("C", -7.5, -5.5),
    ("C-", -9.5, -7.5),
    ("F", -10, -9.5),
]
GRADE_NAMES = [g[0] for g in GRADES]
NEGATIVE_GRADE_NOTE = (
    "Higher negative grades mean a stronger penalty, not a better negative."
)
GRADE_COLORS = dict(
    zip(
        GRADE_NAMES,
        [
            "#39ff14",
            "#55ff00",
            "#83ff00",
            "#b5ff00",
            "#eaff00",
            "#ffe000",
            "#ffc400",
            "#ff9a00",
            "#ff6b00",
            "#ff3b00",
            "#ff1744",
        ],
    )
)


def grade_chance(name):
    return (10 - next(g[1] for g in GRADES if g[0] == name)) / 20


def grading_rank(card, preferences):
    rank = (
        preferences.get("rank")
        if preferences.get("rankMode") == "manual"
        else card.get("rank")
    )
    return rank if type(rank) is int and 0 <= rank <= 8 else None


def trait_range(trait, disposition, fmt, polarity, model, rank=8, variation=None):
    if fmt not in FORMATS or polarity not in ("positive", "negative"):
        raise ValueError("Invalid Riven layout or polarity")
    if (
        not math.isfinite(disposition)
        or disposition <= 0
        or type(rank) is not int
        or not 0 <= rank <= model["maxRank"]
    ):
        raise ValueError("Invalid disposition or rank")
    if not trait or trait.get("value") is None:
        return None
    k, has_negative = FORMATS[fmt]
    negative = polarity == "negative"
    if not trait[polarity] or (negative and not has_negative):
        return None
    attenuation = (
        model["curseByBuffCount"][k]
        if negative
        else model["buffAttenuation"][k] * (model["curseBoost"] if has_negative else 1)
    )
    mean = (
        trait["value"]
        * model["strength"]
        * (rank + 1)
        * disposition
        * model["specificFit"]
        * attenuation
        * (100 if trait["unit"] == "%" else 1)
        * (-1 if negative else 1)
        * (-1 if trait.get("reverse") else 1)
    )
    shift = 1 if trait["unit"] == "x" else 0
    step = trait.get("roundTo") or 0.1
    precision = max(0, -round(math.log10(step)))

    def rounded(value):
        scaled = value / step
        integral = (
            math.floor(scaled + 1e-8)
            if trait.get("rounding") == "RM_FLOOR"
            else math.copysign(math.floor(abs(scaled) + 0.5 + 1e-8), scaled)
        )
        return round(integral * step, precision)

    lo, hi = variation or model["variation"]
    return {
        "low": rounded(shift + mean * lo),
        "high": rounded(shift + mean * hi),
        "mean": shift + mean,
        "unit": trait["unit"],
        "precision": precision,
    }


def grade_range(trait, disposition, fmt, polarity, model, grade, rank=8):
    _, lo, hi = next(g for g in GRADES if g[0] == grade)
    variation = [1 + lo / 100, 1 + hi / 100]
    return trait_range(trait, disposition, fmt, polarity, model, rank, variation)


def format_range(value):
    if not value:
        return "Unknown"
    precision, unit = value["precision"], value["unit"]

    def show(n):
        return (
            f"{n:.{precision}f}"
            if unit == "x"
            else f"{'−' if n < 0 else '+'}{abs(n):.{precision}f}"
        )

    suffix = "×" if unit == "x" else "%" if unit == "%" else f" {unit}" if unit else ""
    return f"{show(value['low'])} to {show(value['high'])}{suffix}"


def grade_stat(stat, trait, disposition, fmt, model, rank=8):
    if type(rank) is not int or not 0 <= rank <= 8:
        return {
            "unknown": True,
            "message": "Rank unreadable: show pips or set a manual rank",
        }
    if not trait or trait.get("value") is None:
        return {"unknown": True, "message": "Baseline unknown for this weapon type"}
    if stat["unit"] != trait["unit"] and not (
        trait["unit"] == "x" and stat["unit"] == "%"
    ):
        return {"invalid": True, "message": "Check OCR stat unit"}
    value_range = trait_range(trait, disposition, fmt, stat["polarity"], model, rank)
    if not value_range:
        return {"unknown": True, "message": "Check stat polarity / layout"}
    shift = 1 if trait["unit"] == "x" else 0
    displayed = (
        1 + stat["value"] / 100
        if trait["unit"] == "x" and stat["unit"] == "%"
        else stat["value"]
    )
    mean = value_range["mean"] - shift
    quality = ((displayed - shift) / mean - 1) * 100
    step = trait.get("roundTo") or 0.1
    floor = trait.get("rounding") == "RM_FLOOR"
    lower = displayed if floor else displayed - step / 2
    upper = displayed + step if floor else displayed + step / 2
    endpoints = sorted(((v - shift) / mean - 1) * 100 for v in (lower, upper))
    if (
        endpoints[0] > 10 + 1e-7
        or endpoints[1] < -10 - 1e-7
        or not math.isfinite(quality)
    ):
        return {
            "invalid": True,
            "message": "Outside range: check OCR, variant and rank",
            "range": value_range,
            "quality": quality,
        }

    def find(q):
        return next((name for name, lo, _ in GRADES if max(-10, min(10, q)) >= lo), "F")

    grade = find(quality)
    low_grade, high_grade = find(endpoints[0] + 1e-7), find(endpoints[1] - 1e-7)
    boundary = low_grade != high_grade
    note = (
        f"{grade} is based on the displayed value. Rounding can put the underlying value in {high_grade} or {low_grade}."
        if boundary
        else ""
    )
    provisional = trait.get("baselineStatus") in ("provisional", "assumed")
    baseline_note = (
        "Temporary ordinary-stat fallback for a missing elemental/faction splice baseline."
        if trait.get("baselineStatus") == "assumed"
        else "Provisional wiki splice baseline; values and rounding may change."
        if provisional
        else ""
    )
    return {
        "grade": grade,
        "quality": quality,
        "label": grade,
        "boundary": boundary,
        "roundingNote": note,
        "range": value_range,
        "provisional": provisional,
        "baselineNote": baseline_note,
    }
