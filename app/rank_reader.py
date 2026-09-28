# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Read the eight rank pips from captured card pixels, never from game state."""

from PIL import Image, ImageChops, ImageFilter

MAX_RANK = 8


def detect_rank(image, card):
    """Return a rank only when all eight regularly spaced pip shapes are visible.

    Bright cyan marks an upgraded pip; neutral diamonds are unlit. Requiring the
    entire row prevents a cropped or obscured max-rank card becoming rank zero.
    Geometry is anchored to the OCR footer's mastery label or roll counter,
    not to a monitor or game window. The counter's value does not imply rank.
    """
    footer = card.get("footerBounds") or card.get("counterBounds")
    title = card.get("titleBounds")
    if not footer or not title or footer["h"] < 5 or image.mode not in ("RGB", "RGBA"):
        return None
    height = footer["h"]
    center = title["x"] + title["w"] / 2
    box = (
        max(0, round(center - 9 * height)),
        max(0, round(footer["y"] + 2.5 * height)),
        min(image.width, round(center + 9 * height)),
        min(image.height, round(footer["y"] + 4.8 * height)),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    scale = 28 / height
    area = image.crop(box).convert("RGB")
    area = area.resize(
        (max(1, round(area.width * scale)), max(1, round(area.height * scale))),
        Image.Resampling.BILINEAR,
    )
    red, green, blue = area.split()
    luminance = ImageChops.lighter(ImageChops.lighter(red, green), blue).filter(
        ImageFilter.BoxBlur(1)
    )
    cyan = ImageChops.darker(
        ImageChops.subtract(green, red), ImageChops.subtract(blue, red)
    ).filter(ImageFilter.BoxBlur(1))
    cyan_pixels = cyan.tobytes()
    width, rows = area.size
    expected_center = round((center - box[0]) * scale)
    best = None
    for spacing in range(24, 36):
        offset = max(3, round(spacing * 0.16))
        tips = ImageChops.add(
            ImageChops.offset(luminance, 0, offset),
            ImageChops.offset(luminance, 0, -offset),
            scale=2,
        )
        gaps = ImageChops.add(
            ImageChops.offset(tips, spacing // 2, 0),
            ImageChops.offset(tips, -spacing // 2, 0),
            scale=2,
        )
        contrasts = ImageChops.subtract(tips, gaps, offset=128).tobytes()
        for x_center in range(expected_center - 16, expected_center + 17, 2):
            xs = [
                round(x_center + (index - 3.5) * spacing) for index in range(MAX_RANK)
            ]
            if xs[0] - spacing // 2 < 0 or xs[-1] + spacing // 2 >= width:
                continue
            for y in range(12, min(rows - offset - 2, 57)):
                scores = [contrasts[y * width + x] - 128 for x in xs]
                weakest = min(scores)
                merit = sum(scores) / MAX_RANK + weakest
                if weakest < 12 or merit < 24 or best and merit <= best[0]:
                    continue
                colors = [
                    (
                        cyan_pixels[(y - offset) * width + x]
                        + cyan_pixels[(y + offset) * width + x]
                    )
                    / 2
                    for x in xs
                ]
                best = (merit, colors)
    if not best:
        return None
    colors = best[1]
    # A dead band keeps anti-aliasing, faded transitions and questionable hues
    # from being silently assigned to either side of a rank boundary.
    if any(3 < value < 9 for value in colors):
        return None
    lit = [value >= 9 for value in colors]
    rank = sum(lit)
    if lit != [True] * rank + [False] * (MAX_RANK - rank):
        return None
    return rank
