# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Recognize viewing context from independently located, visible UI labels."""

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
    if any(
        re.fullmatch(r"\W*(?:SHOW RANKED|CANCEL)\W*", line["text"], re.I)
        for line in lines
    ):
        return None
    for label in ("EXIT", "ITEM DETAILS", "TRADEABLE"):
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
        if label == "EXIT":
            if (
                heading["y"] <= marker["y"] + marker["h"]
                or abs(heading["x"] + heading["w"] / 2 - marker["x"] - marker["w"] / 2)
                > marker["h"] * 8
            ):
                continue
        elif (
            marker["x"] <= heading["x"] + heading["w"]
            or marker["y"] <= heading["y"] + heading["h"]
        ):
            continue
        return tuple(
            (
                line["x"]
                - line["h"] * (18 if label == "EXIT" and line is heading else 1),
                line["y"] - line["h"],
                line["x"] + line["w"] + line["h"],
                line["y"] + line["h"] * 2,
            )
            for line in (heading, marker)
        )
    return None


async def read_inspection_mode(engine, image):
    """Recheck learned context labels together in one compact, pixel-keyed read."""
    return await read_context_mode(engine, image, "inspection", inspection_regions)


async def read_context_mode(engine, image, name, locate):
    """Read learned UI-label strips without retaining old context after a change."""
    regions = getattr(engine, f"_{name}_regions", None)
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
        left = area.width - strip.width
        offsets.append((left, top))
        area.paste(strip, (left, top))
        top += strip.height + 20
    identity = (
        boxes,
        area.mode,
        hashlib.blake2b(area.tobytes(), digest_size=16).digest(),
    )
    cached = getattr(engine, f"_{name}_cache", None)
    if cached and cached[0] == identity:
        return cached[1]
    if time.monotonic() < getattr(engine, f"_next_{name}_check", 0):
        return False
    # Mod-menu headings can cross neutral UI rules; preserving their color
    # avoids merging those rules into the first/last letter during contrast.
    sample = area if name == "hover" else ImageOps.autocontrast(area.convert("L"))
    lines = await primary_read(engine, sample)
    positioned = []
    for box, strip, offset in zip(boxes, strips, offsets):
        left, top = offset
        contained = [
            line
            for line in lines
            if top <= line["y"] and line["y"] + line["h"] <= top + strip.height
        ]
        positioned.extend(map_lines(contained, (box[0] - left, box[1] - top)))
    observed = locate(positioned) is not None
    setattr(engine, f"_{name}_cache", (identity, observed))
    setattr(engine, f"_next_{name}_check", time.monotonic() + (0 if observed else 0.5))
    return observed
