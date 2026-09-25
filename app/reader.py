# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See docs/LICENSE.txt for the license and warranty disclaimer.

"""Read the cycling-screen text area, with a full-frame fallback for other layouts."""

import hashlib
import re
import time
from copy import deepcopy

from PIL import Image, ImageChops, ImageOps

from parser import CATALOG, NAME_KEYS, FOOTER, clean_text, fingerprint, parse_cards
from rank_reader import detect_rank
from layout import (
    action_line,
    card_region,
    clipped_box,
    fits_marker,
    map_lines,
    read_tiles,
)

VARIANT_GROUPS = {}
for family_index, _, variants in CATALOG["weapons"]:
    family = CATALOG["families"][family_index][0]
    group_name = re.sub(
        r"\s*\((?:Primary|Secondary|Rifle|Melee)\)$", "", family, flags=re.I
    )
    VARIANT_GROUPS.setdefault(group_name, set()).update(
        [family, *[v[0] for v in variants]]
    )
VARIANT_CHOICES = {
    name: choices for choices in VARIANT_GROUPS.values() for name in choices
}


def caption_variant(lines):
    """Match a complete caption, including a wrapped name, never a fuzzy guess."""
    marker = fits_marker(lines)
    if not marker:
        return None
    captions = [
        clean_text(line["text"])
        for line in lines
        if line["y"] > marker["y"] + marker["h"]
        and (
            "x" not in marker
            or "x" not in line
            or abs(line["x"] + line["w"] / 2 - marker["x"] - marker["w"] / 2)
            <= marker["h"] * 10
        )
    ]

    def match(text):
        return NAME_KEYS.get(re.sub(r"\s*\[\d+\]$", "", text).casefold())

    combined = match(" ".join(captions))
    if combined:
        return combined
    names = {name for text in captions if (name := match(text))}
    return next(iter(names)) if len(names) == 1 else None


async def variant_hint(engine, image):
    """Read only the visible Fits In panel, without game/window introspection."""
    marker = getattr(engine, "_fits_marker", None)
    boxes = (
        [
            clipped_box(
                image,
                (
                    marker["x"] + marker["w"] / 2 - marker["h"] * 10,
                    marker["y"] + marker["h"] * top,
                    marker["x"] + marker["w"] / 2 + marker["h"] * 10,
                    marker["y"] + marker["h"] * bottom,
                ),
            )
            for top, bottom in ((-0.5, 1.5), (4, 20))
        ]
        if marker
        else [
            clipped_box(
                image,
                (
                    image.width * 0.82,
                    image.height * top,
                    image.width * 0.97,
                    image.height * bottom,
                ),
            )
            for top, bottom in ((0.65, 0.71), (0.835, 0.895))
        ]
    )
    # Exclude the illustration, which can confuse the OCR text-angle detector.
    strips = [image.crop(box) for box in boxes]
    area = Image.new(
        image.mode, (strips[0].width, sum(strip.height for strip in strips) + 20)
    )
    area.paste(strips[0], (0, 0))
    area.paste(strips[1], (0, strips[0].height + 20))
    identity = (
        area.size,
        area.mode,
        hashlib.blake2b(area.tobytes(), digest_size=16).digest(),
    )
    cached = getattr(engine, "_variant_hint_cache", None)
    if (
        cached
        and cached[0] == identity
        and (
            cached[1] is not None
            or time.monotonic() < getattr(engine, "_next_variant_search", 0)
        )
    ):
        return cached[1]
    hint = caption_variant(await engine.read(area))
    if hint is None:
        hint = caption_variant(await engine.read(isolate_ui_text(area)))
    if hint is None:
        # Small label text can merge with the icon border at one sampling scale.
        # Retry these same caption pixels only, then cache even an unknown result.
        scale = min(1.5, 1800 / max(area.size))
        if scale > 1:
            enlarged = area.resize(
                (round(area.width * scale), round(area.height * scale)),
                Image.Resampling.LANCZOS,
            )
            hint = caption_variant(await engine.read(enlarged))
    engine._variant_hint_cache = (identity, hint)
    if hint is None:
        if time.monotonic() < getattr(engine, "_next_variant_search", 0):
            return None
        engine._next_variant_search = time.monotonic() + 1
    if hint is None and not marker:
        # Discover the caption anywhere on the captured monitor or custom region.
        lines = getattr(engine, "_scene_lines", None)
        if lines is None:
            lines = await engine.read(image)
        located = fits_marker(lines)
        if located is None:
            located = fits_marker(await engine.read(isolate_ui_text(image)))
        if located is None:
            located = fits_marker(await read_tiles(engine, isolate_ui_text(image)))
        if located and "x" in located:
            engine._fits_marker = located
            return await variant_hint(engine, image)
    elif hint is None and marker:
        # A missing label may be a moved/resized window, not a changed variant.
        lines = getattr(engine, "_scene_lines", None)
        if lines is None:
            lines = await engine.read(image)
        located = fits_marker(lines)
        if located is None:
            located = fits_marker(await engine.read(isolate_ui_text(image)))
        if located is None:
            located = fits_marker(await read_tiles(engine, isolate_ui_text(image)))
        if located and located != marker:
            engine._fits_marker = located
            return await variant_hint(engine, image)
    return hint


def isolate_ui_text(image):
    """Highlight warm UI caption pixels when the illustration obscures OCR."""
    red, green, blue = image.convert("RGB").split()
    orange = ImageChops.darker(
        ImageChops.subtract(red, blue), ImageChops.subtract(green, blue)
    )
    return orange.point(lambda value: min(255, max(0, (value - 8) * 5)))


def isolate_card_text(image):
    """Keep purple trait text and white locks while suppressing colored icons.

    Only used for a failed-card retry, never to invent a label or a number.
    """
    if image.mode not in ("RGB", "RGBA"):
        return ImageOps.autocontrast(image.convert("L"))
    red, green, blue = image.convert("RGB").split()
    purple = ImageChops.darker(
        ImageChops.subtract(red, green), ImageChops.subtract(blue, green)
    )
    if purple.getextrema()[1] <= 8:
        return ImageOps.autocontrast(image.convert("L"))
    purple = purple.point(lambda v: min(255, max(0, (v - 8) * 8)))
    white = ImageChops.darker(ImageChops.darker(red, green), blue).point(
        lambda v: min(255, max(0, (v - 150) * 3))
    )
    return ImageChops.lighter(purple, white)


async def recover_card(engine, image, card):
    """Retry only a located, incomplete card at higher text scale.

    An element icon can touch a trait letter at one OCR scale. Re-reading those
    same pixels is safer than adding guessed spellings or guessing stat values.
    """
    if card["complete"] or len(card["stats"]) < 2:
        return card
    bounds = card["bounds"]
    left, top = max(0, bounds["x"] - 8), max(0, bounds["y"] - 8)
    right = min(image.width, bounds["x"] + bounds["w"] + 8)
    bottom = min(image.height, bounds["y"] + bounds["h"] + 8)
    area = isolate_card_text(image.crop((left, top, right, bottom)))
    scale = min(2, 2700 / max(area.size))
    area = area.resize(
        (round(area.width * scale), round(area.height * scale)),
        Image.Resampling.LANCZOS,
    )
    retried = parse_cards(await engine.read(area), area.width, area.height)
    if not retried["complete"] or len(retried["cards"]) != 1:
        return card
    recovered = retried["cards"][0]

    def signature(stat):
        return stat["id"], stat["value"], stat["unit"], stat["polarity"]

    known = {signature(stat) for stat in card["stats"]}
    if recovered["weapon"] != card["weapon"] or not known.issubset(
        {signature(stat) for stat in recovered["stats"]}
    ):
        return card
    recovered["bounds"] = bounds
    for name in ("footerBounds", "titleBounds"):
        recovered[name] = card.get(name)
    return recovered


async def refine_card(engine, image, card, force=False):
    """Read small or incomplete text in place, requiring agreement for repairs."""
    title = card.get("titleBounds")
    if not title or card["complete"] and title["h"] >= 22 and not force:
        return card
    bounds = card["bounds"]
    margin = max(8, title["h"] * 0.5)
    box = clipped_box(
        image,
        (
            bounds["x"] - margin,
            bounds["y"] - margin,
            bounds["x"] + bounds["w"] + margin,
            bounds["y"] + bounds["h"] + margin,
        ),
    )
    crop = image.crop(box)
    cache = getattr(engine, "_refine_cache", None) or {}
    cache_key = (
        box,
        image.mode,
        fingerprint({"cards": [card]}),
        card["complete"],
        hashlib.blake2b(crop.tobytes(), digest_size=16).digest(),
    )
    if cache_key in cache:
        return deepcopy(cache[cache_key])

    def remember(value):
        if len(cache) >= 12:
            cache.pop(next(iter(cache)))
        cache[cache_key] = deepcopy(value)
        engine._refine_cache = cache
        return value

    candidates = {}
    original = fingerprint({"cards": [card]})
    conflict = False
    for factor, treatment in (
        (2, "original"),
        (1.5, "purple"),
        (2, "purple"),
        (3, "contrast"),
        (3, "purple"),
    ):
        area = (
            isolate_card_text(crop)
            if treatment == "purple"
            else ImageOps.autocontrast(crop.convert("L"))
            if treatment == "contrast"
            else crop
        )
        scale = min(factor, 2400 / max(area.size))
        area = area.resize(
            (max(1, round(area.width * scale)), max(1, round(area.height * scale))),
            Image.Resampling.LANCZOS,
        )
        parsed = parse_cards(await engine.read(area), area.width, area.height)
        if not parsed["complete"] or len(parsed["cards"]) != 1:
            continue
        recovered = parsed["cards"][0]
        if recovered["weapon"] != card["weapon"]:
            continue
        known_traits = {(stat["id"], stat["polarity"]) for stat in card["stats"]}
        recovered_traits = {
            (stat["id"], stat["polarity"]) for stat in recovered["stats"]
        }
        # A repeatably cropped-off line is not confirmation of a shorter roll.
        if not known_traits.issubset(recovered_traits) or (
            card["complete"] and known_traits != recovered_traits
        ):
            continue
        identity = fingerprint(parsed)
        candidates[identity] = candidates.get(identity, 0) + 1
        if card["complete"] and identity == original or candidates[identity] >= 2:
            return remember(
                source_coordinates(
                    parsed,
                    box[:2],
                    (area.width / crop.width, area.height / crop.height),
                )["cards"][0]
            )
        conflict = conflict or identity != original
    if card["complete"] and conflict:
        return remember(
            {
                **card,
                "complete": False,
                "unreadable": ["Stat readings disagree at different text scales."],
            }
        )
    return remember(card)


async def refine_result(engine, image, result, force=False):
    result["cards"] = [
        await refine_card(engine, image, card, force) for card in result["cards"]
    ]
    result["complete"] = bool(result["cards"]) and all(
        card["complete"] for card in result["cards"]
    )
    if result["complete"]:
        result["reason"] = ""
    return result


def source_coordinates(result, offset, scale):
    """Return every detected box in the original captured image's coordinates."""
    for card in result["cards"]:
        for name in ("bounds", "footerBounds", "titleBounds"):
            box = card.get(name)
            if box:
                card[name] = {
                    "x": box["x"] / scale[0] + offset[0],
                    "y": box["y"] / scale[1] + offset[1],
                    "w": box["w"] / scale[0],
                    "h": box["h"] / scale[1],
                }
    return result


def reset_layout(engine):
    """Forget all pixel anchors when the selected capture source changes."""
    for name in (
        "_layout_size",
        "_card_region",
        "_fits_marker",
        "_action_region",
        "_action_mode_cache",
        "_variant_hint_cache",
        "_rank_footer_cache",
        "_refine_cache",
        "_scene_lines",
        "_frame_action",
    ):
        setattr(engine, name, None)
    engine._next_tile_search = 0
    engine._next_scene_search = 0
    engine._last_located_at = 0
    engine._next_variant_search = 0


async def read_frame(engine, image, focus=True, require_current=True, rank_image=None):
    if getattr(engine, "_layout_size", None) != image.size:
        reset_layout(engine)
        engine._layout_size = image.size
    engine._frame_action = None
    engine._scene_lines = None
    result = await read_cards(engine, image, focus, require_current)
    action = engine._frame_action
    for card in result["cards"]:
        title = card.get("titleBounds") or card["bounds"]
        if action:
            # Normalize around the visible action button, never the monitor center.
            width = max(title["w"], title["h"] * 8)
            card["screenX"] = 0.5 + (
                title["x"] + title["w"] / 2 - action["x"] - action["w"] / 2
            ) / (width * 6)
        else:
            card["screenX"] = -1
    if result["cards"] and (action or not getattr(engine, "_card_region", None)):
        engine._card_region = card_region(image, result["cards"], action)
    if any(card["complete"] for card in result["cards"]):
        engine._last_located_at = time.monotonic()
        hint = await variant_hint(
            engine, rank_image if rank_image is not None else image
        )
        for card in result["cards"]:
            card["rank"] = (
                await read_rank(
                    engine, rank_image if rank_image is not None else image, card
                )
                if card["complete"]
                else None
            )
            if hint in VARIANT_CHOICES.get(card["weapon"], set()):
                card["variantHint"] = hint
    return result


async def read_rank(engine, image, card):
    """Retry an unreadable footer locally; ordinary pip reads need no extra OCR."""
    rank = detect_rank(image, card)
    if rank is not None or not card.get("titleBounds"):
        return rank
    title, bounds = card["titleBounds"], card["bounds"]
    center = title["x"] + title["w"] / 2
    footer = card.get("footerBounds")
    box = (
        max(0, round(center - title["h"] * 5.5)),
        max(0, round(footer["y"] - footer["h"] if footer else title["y"] + title["h"])),
        min(image.width, round(center)),
        min(
            image.height,
            round(
                footer["y"] + footer["h"] * 2 if footer else bounds["y"] + bounds["h"]
            ),
        ),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    area = image.crop(box)
    identity = (box, hashlib.blake2b(area.tobytes(), digest_size=16).digest())
    cache = getattr(engine, "_rank_footer_cache", None) or {}
    if identity in cache:
        candidates = cache[identity]
    else:
        scale = min(2, 1800 / max(area.size))
        area = isolate_card_text(area).resize(
            (round(area.width * scale), round(area.height * scale)),
            Image.Resampling.LANCZOS,
        )
        sx, sy = area.width / (box[2] - box[0]), area.height / (box[3] - box[1])
        candidates = [
            {
                "x": line["x"] / sx + box[0],
                "y": line["y"] / sy + box[1],
                "w": line["w"] / sx,
                "h": line["h"] / sy,
            }
            for entry in await engine.read(area)
            if FOOTER.search(clean_text(entry["text"]))
            for line in [entry.get("footerBounds") or entry]
        ]
        if len(cache) >= 8:
            cache.pop(next(iter(cache)))
        cache[identity] = candidates
        engine._rank_footer_cache = cache
    ranks = {
        detect_rank(image, {**card, "footerBounds": candidate})
        for candidate in candidates
    }
    ranks.discard(None)
    return next(iter(ranks)) if len(ranks) == 1 else None


async def read_cards(engine, image, focus=True, require_current=True):
    focused = None
    learned = getattr(engine, "_card_region", None)
    if focus and (learned or image.width >= 1200 and image.width > image.height):
        # Ordinary image cropping only. Both cards, the MR footers and action label
        # remain inside this region in the cycling layout. No game/window access.
        # Narrower text-only strip for normal wide cycling screens. Keep the
        # broad crop for screenshots/custom layouts; full-frame fallback stays.
        wide = image.width / image.height >= 1.5
        region = (0.20, 0.50, 0.68, 0.97) if wide else (0.16, 0.44, 0.85, 0.97)
        box = learned or tuple(
            int(v * (image.width if i % 2 == 0 else image.height))
            for i, v in enumerate(region)
        )
        area = image.crop(box)
        scale = min(1.5, (1800 if wide else 2700) / area.width)
        area = area.resize(
            (round(area.width * scale), round(area.height * scale)),
            Image.Resampling.LANCZOS,
        )
        actual_scale = (area.width / (box[2] - box[0]), area.height / (box[3] - box[1]))
        lines = await engine.read(area)
        result = parse_cards(lines, area.width, area.height)
        result["mode"] = observe_mode(engine, map_lines(lines, box[:2], actual_scale))
        if result["mode"] == "unknown":
            result["mode"] = await read_action_mode(engine, image)
        if not result["complete"]:
            result["cards"] = [
                await recover_card(engine, area, card) for card in result["cards"]
            ]
            result["complete"] = bool(result["cards"]) and all(
                card["complete"] for card in result["cards"]
            )
            if result["complete"]:
                result["reason"] = ""
        focused = source_coordinates(result, box[:2], actual_scale)
        focused = await refine_result(engine, image, focused, actual_scale[0] < 1)
        initial_comparison_missing_card = (
            require_current
            and result["mode"] == "comparison"
            and len(result["cards"]) < 2
        )
        if (
            not initial_comparison_missing_card
            and (
                result["mode"] != "unknown"
                or learned
                and time.monotonic() < getattr(engine, "_next_scene_search", 0)
            )
            and (
                result["complete"]
                or not require_current
                and any(card["complete"] for card in result["cards"])
            )
        ):
            return result
    engine._next_scene_search = time.monotonic() + 0.75
    lines = await engine.read(image)
    engine._scene_lines = lines
    if marker := fits_marker(lines):
        engine._fits_marker = marker
    result = parse_cards(lines, image.width, image.height)
    result["mode"] = observe_mode(engine, lines)
    if result["mode"] == "unknown":
        result["mode"] = (
            focused["mode"] if focused else await read_action_mode(engine, image)
        )
    if (
        focused
        and focused["cards"]
        and len(focused["cards"]) == len(result["cards"])
        and all(
            a["weapon"] == b["weapon"]
            for a, b in zip(focused["cards"], result["cards"])
        )
    ):
        # Same source pixels, two OCR scales. Recover a dim old card without
        # discarding a good candidate from the focused pass.
        result["cards"] = [
            a if a["complete"] else await recover_card(engine, image, b)
            for a, b in zip(focused["cards"], result["cards"])
        ]
    else:
        result["cards"] = [
            await recover_card(engine, image, card) for card in result["cards"]
        ]
    result = await refine_result(engine, image, result, max(image.size) > 2600)
    result["complete"] = bool(result["cards"]) and all(
        card["complete"] for card in result["cards"]
    )
    if result["complete"]:
        result["reason"] = ""
    if (
        result["mode"] != "transition"
        and time.monotonic() >= getattr(engine, "_next_tile_search", 0)
        and time.monotonic() - getattr(engine, "_last_located_at", 0) > 1
        and (
            not result["complete"]
            or result["mode"] == "comparison"
            and len(result["cards"]) < 2
        )
    ):
        # Discovery is a fallback, not the per-frame hot path. Tiles preserve small
        # window text on very large or unusually shaped monitors.
        engine._next_tile_search = time.monotonic() + 2
        tiles = await read_tiles(engine, image)
        tiled = parse_cards(tiles, image.width, image.height)
        if tiled["cards"]:
            tiled["cards"] = [
                await recover_card(engine, image, card) for card in tiled["cards"]
            ]
            tiled = await refine_result(engine, image, tiled)
            tiled["complete"] = all(card["complete"] for card in tiled["cards"])
            if (
                sum(card["complete"] for card in tiled["cards"]),
                len(tiled["cards"]),
            ) > (
                sum(card["complete"] for card in result["cards"]),
                len(result["cards"]),
            ):
                tiled["mode"] = observe_mode(engine, tiles)
                if tiled["mode"] == "unknown":
                    tiled["mode"] = result["mode"]
                result = tiled
                engine._scene_lines = tiles
                if marker := fits_marker(tiles):
                    engine._fits_marker = marker
                if result["complete"]:
                    result["reason"] = ""
    return result


async def read_action_mode(engine, image):
    """Retry the bottom action caption only when card OCR missed the screen phase."""
    box = getattr(engine, "_action_region", None) or (
        int(image.width * 0.32),
        int(image.height * 0.85),
        int(image.width * 0.69),
        int(image.height * 0.98),
    )
    box = clipped_box(image, box)
    area = image.crop(box)
    identity = (
        box,
        area.mode,
        hashlib.blake2b(area.tobytes(), digest_size=16).digest(),
    )
    cached = getattr(engine, "_action_mode_cache", None)
    if cached and cached[0] == identity:
        engine._frame_action = cached[2]
        return cached[1]
    lines = await engine.read(ImageOps.autocontrast(area.convert("L")))
    positioned = (
        map_lines(lines, box[:2]) if all("x" in line for line in lines) else lines
    )
    mode = observe_mode(engine, positioned)
    engine._action_mode_cache = (identity, mode, getattr(engine, "_frame_action", None))
    return mode


def observe_mode(engine, lines):
    mode = mode_from_lines(lines)
    if line := action_line(lines, mode):
        engine._frame_action = line
        height = line["h"]
        center = line["x"] + line["w"] / 2
        engine._action_region = (
            center - height * 14,
            line["y"] - height,
            center + height * 14,
            line["y"] + height * 2,
        )
    return mode


def mode_from_lines(lines):
    texts = [clean_text(line["text"]).upper().strip() for line in lines]
    if "YES" in texts and "NO" in texts:
        return "transition"
    if any(re.search(r"\bCYCLE\s+FOR\b", text) for text in texts):
        return "current"
    if any(re.fullmatch(r"\W*CONFIRM\W*", text) for text in texts):
        return "comparison"
    return "unknown"
