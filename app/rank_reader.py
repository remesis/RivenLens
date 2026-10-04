# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Read the eight rank pips from captured card pixels, never from game state."""

import numpy as np
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
    width, rows = area.size
    # Signed arrays prevent byte overflow in subtraction and paired-tip sums.
    cyan_pixels = (
        np.frombuffer(cyan.tobytes(), dtype=np.uint8)
        .reshape(rows, width)
        .astype(np.int16)
    )
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
        contrasts = (
            np.frombuffer(
                ImageChops.subtract(tips, gaps, offset=128).tobytes(), dtype=np.uint8
            )
            .reshape(rows, width)
            .astype(np.int16)
            - 128
        )
        columns = [
            xs
            for x_center in range(expected_center - 16, expected_center + 17, 2)
            if (
                xs := [
                    round(x_center + (index - 3.5) * spacing)
                    for index in range(MAX_RANK)
                ]
            )
            and xs[0] - spacing // 2 >= 0
            and xs[-1] + spacing // 2 < width
        ]
        ys = np.arange(12, min(rows - offset - 2, 57))
        if not columns or not len(ys):
            continue
        xs = np.asarray(columns)
        scores = contrasts[ys[None, :, None], xs[:, None, :]]
        weakest = scores.min(axis=2)
        merits = scores.sum(axis=2) / MAX_RANK + weakest
        valid = (weakest >= 12) & (merits >= 24)
        if not valid.any():
            continue
        merits = np.where(valid, merits, -np.inf)
        # C-order is x-center then y; argmax keeps the first exact tie.
        column, row = np.unravel_index(merits.argmax(), merits.shape)
        merit = float(merits[column, row])
        if best and merit <= best[0]:
            continue
        y = ys[row]
        colors = (
            cyan_pixels[y - offset, xs[column]] + cyan_pixels[y + offset, xs[column]]
        ) / 2
        best = (merit, colors)
    if not best:
        return None
    colors = best[1]
    # A dead band keeps anti-aliasing, faded transitions and questionable hues
    # from being silently assigned to either side of a rank boundary.
    if any(3 < value < 9 for value in colors):
        return None
    lit = [bool(value >= 9) for value in colors]
    rank = sum(lit)
    if lit != [True] * rank + [False] * (MAX_RANK - rank):
        return None
    return rank
