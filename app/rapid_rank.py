# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Rank annotation for neural OCR's inflated text boxes, using eight visible pips."""

from copy import deepcopy
import math
import statistics

import numpy as np

from rank_reader import detect_rank


def measured_footer(image, card, *, grayscale=False):
    """Measure text height inside an observed footer, not its printed number."""
    import cv2

    footer = card.get("footerBounds")
    if not isinstance(footer, dict) or image.mode not in ("RGB", "RGBA"):
        return None
    values = [footer.get(k) for k in ("x", "y", "w", "h")]
    if any(
        not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
        for v in values
    ):
        return None
    x, y, width, height = values
    if (
        x < 0
        or y < 0
        or width <= 0
        or height < 8
        or not math.isfinite(x + width)
        or not math.isfinite(y + height)
    ):
        return None
    padding = 0 if grayscale else 0.2 * height
    left, top = math.floor(x - padding), math.floor(y)
    right, bottom = math.ceil(x + width + padding), math.ceil(y + height)
    if left < 0 or top < 0 or right > image.width or bottom > image.height:
        return None
    pixels = np.asarray(image.crop((left, top, right, bottom)).convert("RGB"))
    if grayscale:
        gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
        _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    else:
        pixels = pixels.astype(np.int16)
        mask = (
            (pixels[:, :, 2] - pixels[:, :, 1] > 12)
            & (pixels[:, :, 2] > 90)
            & (pixels[:, :, 0] > 60)
        ).astype(np.uint8)
    _, _, components, _ = cv2.connectedComponentsWithStats(mask, 8)
    glyphs = []
    for gx, gy, gw, gh, area in components[1:]:
        if (
            0.35 * height <= gh <= 0.95 * height
            and gh >= 8
            and 0.1 * gh <= gw <= math.ceil(1.1 * gh)
            and 0.15 <= area / (gw * gh) <= 0.85
            and gx > 0
            and gy > 0
            and gx + gw < right - left
            and gy + gh < bottom - top
        ):
            if grayscale and (
                left + gx < x
                or left + gx + gw > x + width
                or top + gy < y
                or top + gy + gh > y + height
            ):
                return None
            glyphs.append((int(gx), int(gy), int(gw), int(gh)))
    if not (2 if grayscale else 1) <= len(glyphs) <= 9:
        return None
    ink_height = statistics.median(row[3] for row in glyphs)
    baseline = statistics.median(row[1] + row[3] for row in glyphs)
    if any(
        abs(row[3] - ink_height) > 0.2 * ink_height
        or abs(row[1] + row[3] - baseline) > 0.2 * ink_height
        for row in glyphs
    ):
        return None
    first, last = min(r[0] for r in glyphs), max(r[0] + r[2] for r in glyphs)
    if len(glyphs) == 1:
        gx, gy, gw, gh = glyphs[0]
        if (
            left + gx < x
            or left + gx + gw > x + width
            or top + gy < y
            or top + gy + gh > y + height
            or width > 1.5 * ink_height
            or last - first < 0.25 * ink_height
            or left + first - x > 0.6 * ink_height
            or x + width - left - last > 0.6 * ink_height
        ):
            return None
    elif not 0.7 * ink_height <= last - first <= 8 * ink_height:
        return None
    adjusted = deepcopy(card)
    adjusted["footerBounds"] = dict(footer, y=top + baseline - ink_height, h=ink_height)
    return adjusted


def annotation_rank(image, card):
    """Read all eight pips with a measured text anchor; never assume a rank.

    Only rank annotation uses the adjusted height. Physical card-completeness
    checks keep their existing detector and cannot be bypassed by this helper.
    An existing measured anchor's refusal cannot fall back to another row.
    """
    adjusted = measured_footer(image, card)
    if adjusted is not None:
        return detect_rank(image, adjusted)
    adjusted = measured_footer(image, card, grayscale=True)
    rank = detect_rank(image, adjusted or card)
    # A hue-free or unmeasured anchor cannot introduce an ornamental rank zero.
    return rank if rank is not None and rank > 0 else None
