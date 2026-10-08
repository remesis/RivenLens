# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Recognize item-details context without treating a preview as a new roll."""

import hashlib
import re
import time

from PIL import Image, ImageOps

from layout import clipped_box, fits_marker, map_lines
from ocr_budget import primary_read


def inspection_regions(lines):
    marker = fits_marker(lines)
    if not marker:
        return None
    for label in ("ITEM DETAILS", "TRADEABLE"):
        headings = [
            line
            for line in lines
            if re.fullmatch(r"\W*" + label + r"\W*", line["text"], re.I)
        ]
        if len(headings) != 1:
            continue
        heading = headings[0]
        if not all(
            key in line for line in (heading, marker) for key in ("x", "y", "w", "h")
        ):
            continue
        if (
            marker["x"] <= heading["x"] + heading["w"]
            or marker["y"] <= heading["y"] + heading["h"]
        ):
            continue
        return tuple(
            (
                line["x"] - line["h"],
                line["y"] - line["h"],
                line["x"] + line["w"] + line["h"],
                line["y"] + line["h"] * 2,
            )
            for line in (heading, marker)
        )
    return None


async def read_inspection_mode(engine, image):
    """Recheck learned context labels together in one compact, pixel-keyed read."""
    regions = getattr(engine, "_inspection_regions", None)
    if not regions:
        return False
    boxes = tuple(clipped_box(image, box) for box in regions)
    strips = [image.crop(box) for box in boxes]
    area = Image.new(
        image.mode,
        (
            max(strip.width for strip in strips),
            sum(strip.height for strip in strips) + 20,
        ),
    )
    offsets, top = [], 0
    for strip in strips:
        offsets.append(top)
        area.paste(strip, (0, top))
        top += strip.height + 20
    identity = (
        boxes,
        area.mode,
        hashlib.blake2b(area.tobytes(), digest_size=16).digest(),
    )
    cached = getattr(engine, "_inspection_cache", None)
    if cached and cached[0] == identity:
        return cached[1]
    if time.monotonic() < getattr(engine, "_next_inspection_check", 0):
        return False
    lines = await primary_read(engine, ImageOps.autocontrast(area.convert("L")))
    positioned = []
    for box, strip, offset in zip(boxes, strips, offsets):
        contained = [
            line
            for line in lines
            if offset <= line["y"] and line["y"] + line["h"] <= offset + strip.height
        ]
        positioned.extend(map_lines(contained, (box[0], box[1] - offset)))
    observed = inspection_regions(positioned) is not None
    engine._inspection_cache = (identity, observed)
    engine._next_inspection_check = time.monotonic() + (0 if observed else 0.5)
    return observed
