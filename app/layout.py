# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Screen-pixel geometry shared by OCR discovery and focused reading."""

import math
import re

from PIL import Image


def clipped_box(image, box):
    left, top, right, bottom = box
    left = max(0, min(image.width - 1, math.floor(left)))
    top = max(0, min(image.height - 1, math.floor(top)))
    return (
        left,
        top,
        max(left + 1, min(image.width, math.ceil(right))),
        max(top + 1, min(image.height, math.ceil(bottom))),
    )


def map_lines(lines, offset=(0, 0), scale=(1, 1)):
    """Map OCR and MR-word bounds back to the original captured pixels."""

    def box(value):
        return {
            "x": value["x"] / scale[0] + offset[0],
            "y": value["y"] / scale[1] + offset[1],
            "w": value["w"] / scale[0],
            "h": value["h"] / scale[1],
        }

    return [
        {
            **line,
            **box(line),
            **(
                {"footerBounds": box(line["footerBounds"])}
                if line.get("footerBounds")
                else {}
            ),
        }
        for line in lines
    ]


async def read_region(engine, image, box, scale=1.5):
    box = clipped_box(image, box)
    area = image.crop(box)
    scale = min(scale, 2400 / max(area.size))
    size = tuple(max(1, round(side * scale)) for side in area.size)
    resized = area.resize(size, Image.Resampling.LANCZOS) if size != area.size else area
    return map_lines(
        await engine.read(resized),
        box[:2],
        (resized.width / area.width, resized.height / area.height),
    )


def tile_boxes(image, side=1200, overlap=400):
    """Overlapping discovery tiles avoid shrinking an entire ultrawide desktop."""

    def positions(length):
        end = max(0, length - side)
        return sorted({*range(0, end, side - overlap), end})

    return [
        (x, y, min(x + side, image.width), min(y + side, image.height))
        for y in positions(image.height)
        for x in positions(image.width)
    ]


async def read_tiles(engine, image, *, cursor=None):
    from ocr_budget import allow_retry

    lines = []
    boxes = tile_boxes(image)
    start = getattr(engine, cursor, 0) if cursor else 0
    for index in range(start, len(boxes)):
        box = boxes[index]
        # Empty desktop margins do not need OCR, particularly on large canvases.
        extrema = image.crop(box).convert("L").getextrema()
        if extrema[1] - extrema[0] < 3:
            continue
        if cursor and not allow_retry(engine):
            setattr(engine, cursor, index)
            setattr(engine, cursor + "_pending", True)
            return lines
        for candidate in await read_region(engine, image, box):
            duplicate = next(
                (
                    line
                    for line in lines
                    if line["text"] == candidate["text"]
                    and abs(
                        line["x"] + line["w"] / 2 - candidate["x"] - candidate["w"] / 2
                    )
                    < max(line["h"], candidate["h"])
                    and abs(line["y"] - candidate["y"])
                    < max(line["h"], candidate["h"]) * 0.6
                ),
                None,
            )
            if duplicate is None:
                lines.append(candidate)
    if cursor:
        setattr(engine, cursor, 0)
        setattr(engine, cursor + "_pending", False)
    return lines


def fits_marker(lines):
    markers = [
        line
        for line in lines
        if re.fullmatch(r"\W*FITS\s*IN(?:\s+[^A-Za-z]{1,3})?\W*", line["text"], re.I)
    ]
    return markers[0] if len(markers) == 1 else None


def action_line(lines, mode):
    pattern = r"\bCYCLE\s+FOR\b" if mode == "current" else r"^\W*CONFIRM\W*$"
    found = (
        [line for line in lines if re.search(pattern, line["text"], re.I)]
        if mode in ("current", "comparison")
        else []
    )
    return (
        found[0]
        if len(found) == 1 and all(key in found[0] for key in ("x", "y", "w", "h"))
        else None
    )


def card_region(image, cards, action=None, previous=None):
    """Learn a text region with room for both the centered and comparison cards."""
    titles = [card["titleBounds"] for card in cards if card.get("titleBounds")]
    if not titles:
        return None
    width = max(max(title["w"], title["h"] * 8) for title in titles)
    height = max(title["h"] for title in titles)
    center = max(title["x"] + title["w"] / 2 for title in titles)
    left = min(title["x"] for title in titles)
    top = min(title["y"] for title in titles)
    bottom = max(card["bounds"]["y"] + card["bounds"]["h"] for card in cards)
    if action:
        center = action["x"] + action["w"] / 2
        bottom = max(bottom, action["y"] + action["h"] * 2)
    region = clipped_box(
        image,
        (
            min(left - width * 0.7, center - width * 2),
            top - height * 5,
            max(
                center + width * 1.2,
                max(card["bounds"]["x"] + card["bounds"]["w"] for card in cards),
            ),
            bottom + height * 2,
        ),
    )
    # Small OCR box differences must not change the resampling grid every frame.
    # Keep the padded crop stable, but follow actual movement or scale changes.
    tolerance = max(2, height)
    if previous and all(abs(a - b) <= tolerance for a, b in zip(region, previous)):
        return previous
    if previous:
        # The centered card and the new comparison card occupy the same area.
        # Do not change a working OCR scale just to add more empty padding when
        # the existing crop already contains every detected card and the button.
        anchors = [card["bounds"] for card in cards] + ([action] if action else [])
        if all(
            previous[0] <= box["x"]
            and previous[1] <= box["y"] - height
            and previous[2] >= box["x"] + box["w"]
            and previous[3] >= box["y"] + box["h"] + height
            for box in anchors
        ):
            return previous
    return region
