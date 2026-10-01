# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Read the cycling-screen text area, with a full-frame fallback for other layouts."""

import hashlib
import re
import time
from copy import deepcopy

from PIL import Image, ImageChops, ImageFilter, ImageOps

from parser import CATALOG, NAME_KEYS, FOOTER, clean_text, fingerprint, parse_cards
from rank_reader import detect_rank
from ocr_budget import allow_retry, primary_read, reserve_verifications
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

    languages = {line.get("language", "en") for line in lines}
    if len(languages) == 1 and "en" not in languages:
        from localization import profile

        locale = profile(next(iter(languages)))
        originals = [
            line.get("displayText", line["text"])
            for line in lines
            if line["y"] > marker["y"] + marker["h"]
            and (
                "x" not in marker
                or "x" not in line
                or abs(line["x"] + line["w"] / 2 - marker["x"] - marker["w"] / 2)
                <= marker["h"] * 10
            )
        ]
        combined = locale.weapon(" ".join(originals))
        if combined:
            return combined
        candidates = {name for text in originals if (name := locale.weapon(text))}
        if len(candidates) == 1:
            return next(iter(candidates))

    combined = match(" ".join(captions))
    if combined:
        return combined
    names = {name for text in captions if (name := match(text))}
    return next(iter(names)) if len(names) == 1 else None


def same_marker(first, second):
    if not first or not second:
        return False
    tolerance = max(2, min(first["h"], second["h"]) * 0.5)
    return all(
        abs(first[key] - second[key]) <= tolerance for key in ("x", "y", "w", "h")
    )


def remember_marker(engine, marker):
    if same_marker(getattr(engine, "_fits_marker", None), marker):
        return False
    engine._fits_marker = marker
    engine._variant_hint_cache = None
    return True


def defer_variant_search(engine):
    engine._variant_search_phase = 0
    engine._variant_tile_cursor = 0
    engine._variant_tile_cursor_pending = False
    engine._next_variant_search = time.monotonic() + 1


async def variant_hint(engine, image, *, discover=True):
    """Read only the visible Fits In panel, without game/window introspection."""
    marker = getattr(engine, "_fits_marker", None)
    localized = getattr(engine, "language", "en") != "en"
    treatment_count = 4 if localized else 3
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
                    image.width * (0.72 if localized else 0.82),
                    image.height * top,
                    image.width * (0.99 if localized else 0.97),
                    image.height * bottom,
                ),
            )
            for top, bottom in (
                ((0.60, 0.75), (0.78, 0.94))
                if localized
                else ((0.65, 0.71), (0.835, 0.895))
            )
        ]
    )
    # Exclude the illustration, which can confuse the OCR text-angle detector.
    strips = [image.crop(box) for box in boxes]
    area = Image.new(
        image.mode, (strips[0].width, sum(strip.height for strip in strips) + 20)
    )
    area.paste(strips[0], (0, 0))
    area.paste(strips[1], (0, strips[0].height + 20))
    mask_box = (
        (
            (boxes[0][0], boxes[0][1], boxes[1][2], boxes[1][3])
            if marker
            else clipped_box(
                image,
                (
                    image.width * 0.82,
                    image.height * 0.63,
                    image.width * 0.97,
                    image.height * 0.90,
                ),
            )
        )
        if localized
        else None
    )
    masked_panel = image.crop(mask_box) if mask_box else None
    identity = (
        area.size,
        area.mode,
        hashlib.blake2b(area.tobytes(), digest_size=16).digest(),
        # The additional mask also reads the space between the strips. Include
        # those pixels so a caption there cannot reuse a stale variant result.
        hashlib.blake2b(masked_panel.tobytes(), digest_size=16).digest()
        if masked_panel is not None
        else None,
    )
    cached = getattr(engine, "_variant_hint_cache", None)
    same_pixels = cached and cached[0] == identity
    if same_pixels and cached[1] is not None:
        return cached[1]
    attempt = cached[2] if same_pixels else 0
    label_seen = cached[3] if same_pixels else False
    attempted = False
    if (
        same_pixels
        and attempt == treatment_count
        and time.monotonic() >= getattr(engine, "_next_variant_retry", 0)
    ):
        attempt, label_seen = 0, False
    # Localized headings can be much wider than the initial strip. Leave one
    # bounded pass for discovery instead of spending it all on that same crop.
    while attempt < treatment_count and (not localized or not attempted):
        # Changed pixels get one fast local read. Extra treatments back off even
        # when animation or a cursor keeps changing an unreadable caption.
        if attempt and time.monotonic() < getattr(engine, "_next_variant_retry", 0):
            break
        if not allow_retry(engine):
            return None
        treatment = (
            getattr(engine, "_localized_variant_treatment", 0) if localized else attempt
        )
        if localized:
            engine._localized_variant_treatment = (treatment + 1) % treatment_count
        panel_box = None
        if treatment == 0:
            sample = area
        elif treatment == 1:
            sample = (
                ImageOps.autocontrast(area.convert("L"))
                if localized
                else isolate_ui_text(area)
            )
            if localized:
                sample = sample.resize(
                    (area.width * 2, area.height * 2), Image.Resampling.LANCZOS
                )
        elif treatment == 2:
            scale = min(1.5, 1800 / max(area.size))
            sample = area.resize(
                (round(area.width * scale), round(area.height * scale)),
                Image.Resampling.LANCZOS,
            )
        else:
            # Complex captions can merge with the animated purple background.
            # A compact caption-color mask provides another exact-pixel read, not a
            # guessed heading. Keep it in the existing bounded rotation.
            panel_box = mask_box
            sample = isolate_ui_text(masked_panel, neutral=True).resize(
                (
                    max(1, round(masked_panel.width * 0.75)),
                    max(1, round(masked_panel.height * 0.75)),
                ),
                Image.Resampling.LANCZOS,
            )
        lines = await engine.read(sample)
        attempted = True
        hint = caption_variant(lines)
        located = fits_marker(lines)
        label_seen = label_seen or located is not None
        attempt += 1
        engine._variant_hint_cache = (identity, hint, attempt, label_seen)
        if hint is not None:
            engine._variant_search_phase = 0
            return hint
        if located and all(key in located for key in ("x", "y", "w", "h")):
            if panel_box:
                remember_marker(
                    engine,
                    map_lines(
                        [located],
                        panel_box[:2],
                        (
                            sample.width / (panel_box[2] - panel_box[0]),
                            sample.height / (panel_box[3] - panel_box[1]),
                        ),
                    )[0],
                )
                continue
            sx, sy = sample.width / area.width, sample.height / area.height
            # The quick strips can contain the heading while clipping the name.
            # Locate that heading in source pixels before treating the panel as
            # found. Only the first strip has this direct coordinate mapping.
            if 0 <= located["y"] and (
                located["y"] + located["h"] <= strips[0].height * sy
            ):
                mapped = map_lines([located], boxes[0][:2], (sx, sy))[0]
                # Finish any affordable treatments of this crop first. If they
                # fail, the next scan uses the heading-relative panel instead.
                remember_marker(engine, mapped)
    if attempt == treatment_count and attempted:
        engine._next_variant_retry = time.monotonic() + 1
    if label_seen:
        # The panel is already located. A missing weapon name does not justify
        # repeatedly searching the entire display for this same label.
        defer_variant_search(engine)
        return None
    if not discover or time.monotonic() < getattr(engine, "_next_variant_search", 0):
        return None
    # Only the search position survives between frames, never old tile text.
    phase = getattr(engine, "_variant_search_phase", 0)
    while phase < 3:
        lines = getattr(engine, "_scene_lines", None) if phase == 0 else None
        if phase < 2:
            if lines is None:
                if not allow_retry(engine):
                    return None
                lines = await engine.read(
                    image
                    if phase == 0
                    else (
                        ImageOps.autocontrast(image.convert("L"))
                        if localized
                        else isolate_ui_text(image)
                    )
                )
            phase += 1
        else:
            lines = await read_tiles(
                engine,
                ImageOps.autocontrast(image.convert("L"))
                if localized
                else isolate_ui_text(image),
                cursor="_variant_tile_cursor",
            )
            if not getattr(engine, "_variant_tile_cursor_pending", False):
                phase += 1
        engine._variant_search_phase = phase
        located = fits_marker(lines)
        if located and "x" in located:
            if localized and (hint := caption_variant(lines)):
                remember_marker(engine, located)
                defer_variant_search(engine)
                return hint
            moved = remember_marker(engine, located)
            defer_variant_search(engine)
            if moved:
                return await variant_hint(engine, image, discover=False)
            return None
        if phase == 2 and getattr(engine, "_variant_tile_cursor_pending", False):
            return None
    defer_variant_search(engine)
    return None


def isolate_ui_text(image, *, neutral=False):
    """Highlight caption pixels when the illustration obscures OCR."""
    red, green, blue = image.convert("RGB").split()
    orange = ImageChops.darker(
        ImageChops.subtract(red, blue), ImageChops.subtract(green, blue)
    )
    warm = orange.point(lambda value: min(255, max(0, (value - 8) * 5)))
    if not neutral:
        return warm
    # Neutral UI themes use gray/white captions instead of gold. Suppress the
    # saturated purple backdrop while retaining their actual visible lettering.
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    achromatic = ImageChops.subtract(maximum, minimum).point(
        lambda value: min(255, max(0, (30 - value) * 16))
    )
    bright = minimum.point(lambda value: min(255, max(0, (value - 65) * 3)))
    return ImageChops.lighter(warm, ImageChops.multiply(bright, achromatic))


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
    # The unselected card dims locked traits to gray. Keep those visible lines
    # in recovery images too; dropping them could falsely suggest no negative.
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    neutral = ImageChops.subtract(maximum, minimum).point(
        lambda v: min(255, max(0, (30 - v) * 32))
    )
    white = minimum.point(lambda v: min(255, max(0, (v - 40) * 3)))
    return ImageChops.lighter(purple, ImageChops.multiply(white, neutral))


def confirms_fresh_stats(engine, card, recovered, location, sample):
    """Confirm full fresh readings across changing non-text pixels, never fill gaps."""

    def values(item):
        return {(s["id"], s["value"], s["unit"], s["polarity"]) for s in item["stats"]}

    evidence = getattr(engine, "_refine_evidence", None) or {}
    scope = (location, card.get("title", ""))
    # Cross-frame agreement must preserve every number recognized in this frame.
    # Conflicting numbers still require two full confirming reads of these pixels.
    if not values(card).issubset(values(recovered)):
        evidence.pop(scope, None)
        return False
    now = time.monotonic()
    identity = fingerprint({"cards": [recovered]})
    previous = evidence.get(scope)
    samples = (
        set(previous[1])
        if previous and previous[0] == identity and now - previous[2] <= 1.5
        else set()
    )
    if len(samples) < 2:
        samples.add(sample)
    if scope not in evidence and len(evidence) >= 12:
        evidence.pop(next(iter(evidence)))
    evidence[scope] = (identity, samples, now)
    engine._refine_evidence = evidence
    return len(samples) >= 2


async def recover_card(engine, image, card):
    """Retry only a located, incomplete card at higher text scale.

    An element icon can touch a trait letter at one OCR scale. Re-reading those
    same pixels is safer than adding guessed spellings or guessing stat values.
    """
    if card["complete"] or len(card["stats"]) < 2:
        return card
    now = time.monotonic()
    evidence = getattr(engine, "_refine_evidence", None) or {}
    if any(
        scope[1] == card.get("title", "") and now - observation[2] <= 1.5
        for scope, observation in evidence.items()
    ):
        # A local text scale recently read this card in full. Give that fresh
        # verification priority over another generic color-recovery attempt.
        return card
    failed = getattr(engine, "_recovery_retry_after", None) or {}
    identity = (card.get("title", ""), fingerprint({"cards": [card]}))
    if time.monotonic() < failed.get(identity, 0):
        return card
    if not allow_retry(engine):
        return card

    def retry_later():
        # Give other scales a turn instead of spending every frame's retry
        # budget on the same unsuccessful color treatment. Store no old text.
        if identity not in failed and len(failed) >= 12:
            failed.pop(next(iter(failed)))
        failed[identity] = time.monotonic() + 1
        engine._recovery_retry_after = failed
        return card

    bounds = card.get("textBounds") or card["bounds"]
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
        return retry_later()
    recovered = retried["cards"][0]

    def signature(stat):
        return stat["id"], stat["value"], stat["unit"], stat["polarity"]

    known = {signature(stat) for stat in card["stats"]}
    if recovered["weapon"] != card["weapon"] or not known.issubset(
        {signature(stat) for stat in recovered["stats"]}
    ):
        return retry_later()
    recovered["bounds"] = card["bounds"]
    for name in ("footerBounds", "counterBounds", "titleBounds", "textBounds"):
        recovered[name] = card.get(name)
    return recovered


_RECOVERY_MODES = {
    # Scale, horizontal margin in title heights, glyph stretch, tone treatment.
    "smooth": (2, 0.5, 1, "gray"),
    "smooth-roomy": (2, 1.5, 1, "gray"),
    "color-roomy": (2, 1.5, 1, "color"),
    "color": (2, 0.5, 1, "color"),
    "denoise": (2, 0.5, 1.5, "median"),
    "denoise-inverse": (2, 0.5, 1.5, "median-inverse"),
    "smooth-wide": (2, 0.5, 1.5, "gray"),
    "smooth-wider": (2.5, 1, 1.6, "gray"),
}


def card_text_bottom(image, card):
    """Exclude background below a footer only when its location is verified."""
    footer = card.get("footerBounds")
    if (
        not footer
        and card.get("counterBounds")
        and detect_rank(image, card) is not None
    ):
        # A numeric fragment alone cannot shorten the text crop. The complete
        # pip row confirms that this counter actually belongs to the footer.
        footer = card["counterBounds"]
    bounds = card.get("textBounds") or card["bounds"]
    return footer["y"] + footer["h"] if footer else bounds["y"] + bounds["h"]


def recovery_text_box(image, card, margin):
    """Include unread numeric prefixes without expanding into the illustration."""
    bounds = card.get("textBounds") or card["bounds"]
    height = card["titleBounds"]["h"]
    bottom = card_text_bottom(image, card)
    return clipped_box(
        image,
        (
            bounds["x"] - height * margin,
            bounds["y"] - height * 0.3,
            bounds["x"] + bounds["w"] + height * margin,
            bottom + height * 0.3,
        ),
    )


def recovery_text_sample(image, card, treatment):
    """Smooth small glyphs and keep a quiet border around the OCR input."""
    factor, margin, stretch, tone = _RECOVERY_MODES[treatment]
    box = recovery_text_box(image, card, margin)
    area = image.crop(box)
    denoise = tone.startswith("median")
    if denoise:
        area = area.convert("L")
    elif tone == "gray":
        area = ImageOps.autocontrast(area.convert("L"))
    scale = min(factor, 2400 / max(area.width * stretch, area.height))
    area = area.resize(
        (
            max(1, round(area.width * scale * stretch)),
            max(1, round(area.height * scale)),
        ),
        Image.Resampling.BILINEAR,
    )
    if denoise:
        # Smooth checkerboard/compression noise after enlarging the tiny glyphs.
        # Clipping the histogram tails keeps bright trim from drowning dim text.
        area = ImageOps.autocontrast(
            area.filter(ImageFilter.MedianFilter(3)), cutoff=10
        )
        if tone == "median-inverse":
            area = ImageOps.invert(area)
    sx, sy = area.width / (box[2] - box[0]), area.height / (box[3] - box[1])
    padding = 12
    fill = (255 if tone == "median-inverse" else 0) if denoise else 15
    if area.mode != "L":
        fill = (fill, fill, fill)
    area = ImageOps.expand(area, border=padding, fill=fill)
    return area, (box[0] - padding / sx, box[1] - padding / sy), (sx, sy)


def counter_footer_complete(image, card):
    """Verify the bottom of a short card without guessing a missing stat line."""
    return bool(
        not card["complete"]
        and not card.get("unreadable")
        and card.get("counterBounds")
        and card.get("format") in ("2p0n", "2p1n", "3p0n")
        and len({stat["id"] for stat in card["stats"]}) == len(card["stats"])
        and detect_rank(image, card) is not None
    )


async def refine_card(engine, image, card, force=False):
    """Read small or incomplete text in place, requiring agreement for repairs."""
    counter_confirmed = counter_footer_complete(image, card)
    if counter_confirmed:
        card = {**card, "complete": True}
        force = True
    title = card.get("titleBounds")
    cjk = getattr(engine, "language", "en") in ("ja", "ko", "zh", "tc")
    validate = getattr(engine, "_validate_stats", None)
    plausible = validate is None or validate(card)
    needs_recovery = not card["complete"] or not plausible
    if (
        card["complete"]
        and card.get("normalizedPercent")
        and not counter_confirmed
        and not card.get("normalizedSpacing")
        and validate
        and plausible
        and not cjk
    ):
        # Only the unit glyph was restored, using the known display precision.
        # Known stat ranges must fit one whole-card rank/variant combination. The
        # tracker still requires two complete agreeing observations before it
        # publishes a new roll; a partial read cannot confirm this candidate.
        return card
    if not title or (
        card["complete"]
        and plausible
        and title["h"] >= 22
        and not force
        and not cjk
        and not card.get("normalizedPercent")
        and not card.get("normalizedSpacing")
    ):
        return card
    bounds = card.get("textBounds") or card["bounds"]
    # Follow the detected text, not a fixed fraction of the card. Large margins
    # include the changing illustration above the title and animated lower trim.
    margin = max(3, title["h"] * 0.15)
    # CJK OCR can truncate a final decimal digit even across multiple scales
    # when the row is tight to the crop edge. Preserve breathing room around
    # the full text block without including more of the animated artwork above.
    horizontal_margin = max(margin, title["h"] * 0.8) if cjk else margin
    bottom = card_text_bottom(image, card)
    box = clipped_box(
        image,
        (
            bounds["x"] - horizontal_margin,
            bounds["y"] - margin,
            bounds["x"] + bounds["w"] + horizontal_margin,
            bottom + margin,
        ),
    )
    crop = image.crop(box)
    # Missing numeric prefixes can lie outside the recognized text bounds. Retry
    # those pixels with modest horizontal room and a smooth grayscale scale;
    # changing only the original tight crop can repeatedly omit the same value.
    cache_box = box
    if needs_recovery:
        recovery_box = recovery_text_box(image, card, 1.5)
        cache_box = (
            min(box[0], recovery_box[0]),
            min(box[1], recovery_box[1]),
            max(box[2], recovery_box[2]),
            max(box[3], recovery_box[3]),
        )
    cache = getattr(engine, "_refine_cache", None) or {}
    cache_key = (
        box,
        cache_box,
        image.mode,
        fingerprint({"cards": [card]}),
        card["complete"],
        hashlib.blake2b(image.crop(cache_box).tobytes(), digest_size=16).digest(),
    )
    if cache_key in cache:
        return deepcopy(cache[cache_key])
    pending = getattr(engine, "_refine_pending", None) or {}

    def remember(value):
        if len(cache) >= 12:
            cache.pop(next(iter(cache)))
        cache[cache_key] = deepcopy(value)
        engine._refine_cache = cache
        pending.pop(cache_key, None)
        engine._refine_pending = pending
        return value

    original = fingerprint({"cards": [card]})
    treatments = (
        (1.5, "original"),
        (2.5, "original"),
        (2, "original"),
        (2.25, "original"),
        (3, "original"),
        (1.5, "purple"),
        (2, "purple"),
        (3, "contrast"),
        (3, "purple"),
    )
    language = getattr(engine, "language", "en")
    cjk = language in ("ja", "ko", "zh", "tc")
    if not needs_recovery and language != "en":
        # The confirming read needs the same help as an incomplete translated
        # card: clean, enlarged glyphs, including compact CJK lettering. This
        # changes only the retry order, never the agreement requirement.
        treatments = (
            ((2, "original"), (2, "smooth-roomy"))
            if cjk
            else ((2, "smooth-wide"), (2, "smooth-roomy"))
        ) + tuple(item for item in treatments if not (cjk and item == (2, "original")))
    if needs_recovery:
        # A fully parsed but out-of-range value can still have missing digits.
        # Give it the same pixel recovery as a missing line; acceptance still
        # requires two agreeing complete reads with plausible values.
        recovery = tuple((mode[0], name) for name, mode in _RECOVERY_MODES.items())
        if getattr(engine, "language", "en") != "en":
            # Several localized models lose numeric prefixes at normal glyph
            # widths. Try the existing pixel-only wide treatment early, while
            # retaining the same full-read agreement and numeric safeguards.
            recovery = tuple(
                sorted(
                    recovery,
                    key=lambda item: item[1] != ("smooth" if cjk else "smooth-wide"),
                )
            )
        # Smooth tiny glyphs first; larger text keeps its faster normal retries.
        treatments = (
            recovery + treatments
            if title["h"] < 22 or getattr(engine, "language", "en") != "en"
            else treatments + recovery
        )
    # Scheduling is only a hint, not OCR evidence. Animated artwork and borders
    # change crop pixels even when the text is stationary. Keep moving through
    # treatments across those frames instead of retrying the first failure forever.
    schedule = getattr(engine, "_refine_schedule", None) or {}
    location = (
        card["weapon"],
        round((title["x"] + title["w"] / 2) * 8 / image.width),
        round(title["h"] / 8),
    )
    preferred = schedule.get(location)
    cursor = treatments.index(preferred) if preferred in treatments else 0
    order = tuple(
        (cursor + offset) % len(treatments) for offset in range(len(treatments))
    )
    order, start, candidates, conflict = pending.get(cache_key, (order, 0, {}, False))
    if location not in schedule and len(schedule) >= 24:
        schedule.pop(next(iter(schedule)))
    engine._refine_schedule = schedule
    for offset in range(start, len(order)):
        index = order[offset]
        if not allow_retry(engine, verification=location):
            # Keep exact-pixel retries separate from recent full-read agreement.
            # Changed pixels retain a retry hint, never a substitute stat line.
            if cache_key not in pending and len(pending) >= 12:
                pending.pop(next(iter(pending)))
            pending[cache_key] = (order, offset, candidates, conflict)
            engine._refine_pending = pending
            return {
                **card,
                "complete": False,
                "unreadable": [
                    "Stat readings disagree at different text scales."
                    if conflict
                    else "Waiting for a confirming stat read."
                ],
            }
        factor, treatment = treatments[index]
        if treatment in _RECOVERY_MODES:
            area, sample_offset, sample_scale = recovery_text_sample(
                image, card, treatment
            )
        else:
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
            sample_offset = box[:2]
            sample_scale = (area.width / crop.width, area.height / crop.height)
        parsed = parse_cards(await engine.read(area), area.width, area.height)
        schedule[location] = treatments[(index + 1) % len(treatments)]
        if len(parsed["cards"]) == 1 and not parsed["complete"]:
            positioned = source_coordinates(
                deepcopy(parsed), sample_offset, sample_scale
            )["cards"][0]
            if counter_footer_complete(image, positioned):
                parsed["cards"][0]["complete"] = parsed["complete"] = True
        if not parsed["complete"] or len(parsed["cards"]) != 1:
            continue
        recovered = parsed["cards"][0]
        if recovered["weapon"] != card["weapon"]:
            continue
        if validate is not None and not validate(recovered):
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
        fresh_agreement = confirms_fresh_stats(
            engine,
            card,
            recovered,
            location,
            (treatment, *(round(value, 3) for value in sample_scale)),
        )
        if (
            card["complete"]
            and identity == original
            or candidates[identity] >= 2
            or fresh_agreement
        ):
            # Try the successful text scale first next time, on fresh pixels.
            schedule[location] = treatments[index]
            verified = source_coordinates(parsed, sample_offset, sample_scale)["cards"][
                0
            ]
            if not verified.get("counterBounds") and card.get("counterBounds"):
                anchored = {**verified, "counterBounds": card["counterBounds"]}
                # The confirming crop can miss a footer seen by this frame's
                # first read. Reuse its geometry only after checking the pips
                # again at the confirmed card's position, never from old frames.
                if detect_rank(image, anchored) is not None:
                    verified = anchored
            return remember(verified)
        conflict = conflict or identity != original
    if card["complete"]:
        return remember(
            {
                **card,
                "complete": False,
                "unreadable": [
                    "Stat readings disagree at different text scales."
                    if conflict
                    else "Stat lines could not be verified at another scale."
                ],
            }
        )
    return remember(card)


async def refine_result(
    engine, image, result, force=False, *, verified=(), recover=False, on_verified=None
):
    # Verify the proposed roll first, without changing the displayed card order.
    for index in reversed(range(len(result["cards"]))):
        if index in verified:
            continue
        if recover:
            result["cards"][index] = await recover_card(
                engine, image, result["cards"][index]
            )
        result["cards"][index] = await refine_card(
            engine, image, result["cards"][index], force
        )
        if on_verified and result["cards"][index]["complete"]:
            await on_verified(result["cards"][index])
    result["complete"] = bool(result["cards"]) and all(
        card["complete"] for card in result["cards"]
    )
    if result["complete"]:
        result["reason"] = ""
    return result


def source_coordinates(result, offset, scale):
    """Return every detected box in the original captured image's coordinates."""
    for card in result["cards"]:
        for name in (
            "bounds",
            "footerBounds",
            "counterBounds",
            "titleBounds",
            "textBounds",
        ):
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
        "_refine_pending",
        "_refine_schedule",
        "_refine_evidence",
        "_recovery_retry_after",
        "_scene_lines",
        "_frame_action",
        "_frame_region",
        "_session_variant_hint",
    ):
        setattr(engine, name, None)
    engine._next_tile_search = 0
    engine._next_scene_search = 0
    engine._last_located_at = 0
    engine._next_variant_search = 0
    engine._next_variant_retry = 0
    engine._next_action_retry = 0
    engine._action_retry_phase = 0
    engine._localized_variant_treatment = 0
    engine._variant_pending = False
    engine._next_variant_priority = 0
    engine._next_variant_check = 0
    engine._next_variant_range_check = 0
    engine._variant_search_phase = 0
    engine._variant_tile_cursor = engine._card_tile_cursor = 0
    engine._variant_tile_cursor_pending = engine._card_tile_cursor_pending = False


async def read_frame(
    engine,
    image,
    focus=True,
    require_current=True,
    rank_image=None,
    budget=None,
    previous_new=None,
    variant_mismatch=None,
    validate_stats=None,
    auto_rank=True,
):
    if getattr(engine, "_layout_size", None) != image.size:
        reset_layout(engine)
        engine._layout_size = image.size
    engine._frame_action = None
    engine._frame_region = None
    engine._scene_lines = None
    engine._frame_budget = budget
    # Keep one of the bounded retry passes for each card, even when a slow
    # discovery/caption call exhausts the time allowance before refinement.
    reserve_verifications(engine, 2)
    engine._validate_stats = validate_stats
    metadata_image = rank_image if rank_image is not None else image
    hint = getattr(engine, "_session_variant_hint", None)
    priority_variant = getattr(engine, "_variant_pending", False) and (
        time.monotonic() >= getattr(engine, "_next_variant_priority", 0)
    )
    if priority_variant:
        # A costly stat retry must not starve the caption indefinitely. The same
        # retry budget still applies; unresolved captions get this turn at most
        # twice per second, and the primary card read always follows.
        engine._next_variant_priority = time.monotonic() + 0.5
        hint = await variant_hint(engine, metadata_image)
        if hint:
            engine._session_variant_hint = hint
            engine._next_variant_check = time.monotonic() + 5
    variant_attempted = priority_variant

    async def annotate(card):
        nonlocal hint, variant_attempted
        card["rank"] = await read_rank(engine, metadata_image, card) if auto_rank else 8
        compatible = hint in VARIANT_CHOICES.get(card["weapon"], set())
        if not compatible:
            hint = engine._session_variant_hint = None
        now = time.monotonic()
        outside_range = bool(
            compatible and variant_mismatch and variant_mismatch(card, hint)
        )
        range_due = outside_range and now >= getattr(
            engine, "_next_variant_range_check", 0
        )
        candidate_pending = (
            previous_new is not None
            and mode_from_lines([engine._frame_action] if engine._frame_action else [])
            == "comparison"
            and fingerprint({"cards": [card]}) != previous_new
        )
        periodic_due = now >= getattr(engine, "_next_variant_check", 0)
        if variant_attempted or (
            compatible and not range_due and (not periodic_due or candidate_pending)
        ):
            return
        # An established variant survives ordinary rolls. Periodic caption work
        # waits until the proposed roll has been published; incompatible values
        # request an earlier check without inventing a different variant.
        refreshed = await variant_hint(engine, metadata_image)
        variant_attempted = True
        engine._next_variant_range_check = now + 0.5
        engine._next_variant_check = now + (5 if refreshed else 1)
        if refreshed in VARIANT_CHOICES.get(card["weapon"], set()):
            hint = engine._session_variant_hint = refreshed

    result = await read_cards(
        engine,
        image,
        focus,
        require_current,
        on_verified=annotate,
        previous_new=previous_new,
    )
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
    # A title alone can belong to the companion or a weapon selector. Only
    # physical card footers may relocate the learned region during transitions.
    located = [
        card
        for card in result["cards"]
        if detect_rank(metadata_image, card) is not None
    ]
    if located and (action or not getattr(engine, "_card_region", None)):
        engine._card_region = card_region(
            image,
            located,
            action,
            getattr(engine, "_card_region", None) or engine._frame_region,
        )
    if any(card["complete"] for card in result["cards"]):
        engine._last_located_at = time.monotonic()
        for card in reversed(result["cards"]):
            if card["complete"] and "rank" not in card:
                await annotate(card)
        for card in result["cards"]:
            if hint in VARIANT_CHOICES.get(card["weapon"], set()):
                card["variantHint"] = hint
    engine._variant_pending = bool(result["cards"]) and not any(
        hint in VARIANT_CHOICES.get(card["weapon"], set()) for card in result["cards"]
    )
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
    context_box = (
        box[0],
        max(0, round(title["y"])),
        min(image.width, round(center + title["h"] * 2)),
        box[3],
    )
    identity = (
        box,
        context_box,
        hashlib.blake2b(image.crop(context_box).tobytes(), digest_size=16).digest(),
    )
    cache = getattr(engine, "_rank_footer_cache", None) or {}
    candidates, attempt = cache.get(identity, ([], 0))

    def candidate_rank():
        ranks = {
            detect_rank(image, {**card, "footerBounds": candidate})
            for candidate in candidates
        }
        ranks.discard(None)
        return next(iter(ranks)) if len(ranks) == 1 else None

    rank = candidate_rank()
    while rank is None and attempt < 4:
        if not allow_retry(engine):
            return None
        # A short footer can disappear from a masked, half-line crop. Color
        # retries keep title context and progressively more right-hand padding
        # so OCR can resolve its lettering and orientation at two text scales.
        retry_box = (
            (
                box[0],
                max(0, round(title["y"])),
                min(image.width, round(center + title["h"] * (attempt - 1))),
                box[3],
            )
            if attempt >= 2
            else box
        )
        retry_area = image.crop(retry_box) if attempt >= 2 else area
        scale = min(1.5 if attempt == 2 else 2, 1800 / max(retry_area.size))
        sample = (
            isolate_card_text(retry_area)
            if attempt == 0
            else ImageOps.autocontrast(retry_area.convert("L"))
            if attempt == 1
            else retry_area
        ).resize(
            (round(retry_area.width * scale), round(retry_area.height * scale)),
            Image.Resampling.LANCZOS,
        )
        sx, sy = sample.width / retry_area.width, sample.height / retry_area.height
        candidates = candidates + [
            {
                "x": line["x"] / sx + retry_box[0],
                "y": line["y"] / sy + retry_box[1],
                "w": line["w"] / sx,
                "h": line["h"] / sy,
            }
            for entry in await engine.read(sample)
            if FOOTER.search(clean_text(entry["text"]))
            for line in [entry.get("footerBounds") or entry]
        ]
        attempt += 1
        if identity not in cache and len(cache) >= 8:
            cache.pop(next(iter(cache)))
        cache[identity] = (candidates, attempt)
        engine._rank_footer_cache = cache
        rank = candidate_rank()
    return rank


async def read_cards(
    engine,
    image,
    focus=True,
    require_current=True,
    *,
    on_verified=None,
    previous_new=None,
):
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
        engine._frame_region = box
        area = image.crop(box)
        # CJK recognizers often drop whole rows when already-large glyphs are
        # enlarged. Start with native pixels; local recovery still tries scales.
        preferred_scale = (
            1 if getattr(engine, "language", "en") in ("ja", "ko", "zh", "tc") else 1.5
        )
        scale = min(preferred_scale, (1800 if wide else 2700) / area.width)
        area = area.resize(
            (round(area.width * scale), round(area.height * scale)),
            Image.Resampling.LANCZOS,
        )
        actual_scale = (area.width / (box[2] - box[0]), area.height / (box[3] - box[1]))
        lines = await primary_read(engine, area)
        result = parse_cards(lines, area.width, area.height)
        reserve_verifications(engine, len(result["cards"]))
        result["mode"] = observe_mode(engine, map_lines(lines, box[:2], actual_scale))
        if result["mode"] == "unknown":
            result["mode"] = await read_action_mode(engine, image)
        # Recover and verify the proposed card before spending retries on the
        # dim old card. Preserve the focused OCR scale for the first recovery.
        new_ready = False
        discover_comparison = (
            result["mode"] == "comparison"
            and len(result["cards"]) < 2
            and (require_current or not result["complete"])
            and not (
                getattr(engine, "language", "en") != "en" and previous_new is not None
            )
        )
        for index in reversed(range(len(result["cards"]))):
            if new_ready or discover_comparison:
                card = source_coordinates(
                    {"cards": [result["cards"][index]]}, box[:2], actual_scale
                )["cards"][0]
                result["cards"][index] = {
                    **card,
                    "complete": False,
                    "unreadable": [
                        "Locating both comparison cards."
                        if discover_comparison
                        else "Current roll verification deferred for the new roll."
                    ],
                }
                continue
            card = await recover_card(engine, area, result["cards"][index])
            positioned = source_coordinates({"cards": [card]}, box[:2], actual_scale)[
                "cards"
            ][0]
            result["cards"][index] = await refine_card(
                engine, image, positioned, actual_scale[0] < 1
            )
            if on_verified and result["cards"][index]["complete"]:
                await on_verified(result["cards"][index])
            new_ready = (
                previous_new is not None
                and result["mode"] == "comparison"
                and len(result["cards"]) == 2
                and index == 1
                and result["cards"][index]["complete"]
                and fingerprint({"cards": [result["cards"][index]]}) != previous_new
            )
        result["complete"] = bool(result["cards"]) and all(
            card["complete"] for card in result["cards"]
        )
        if result["complete"]:
            result["reason"] = ""
        focused = result
        if new_ready:
            # The tracker already has a current card. Publish the newly verified
            # candidate now; resume old-card recovery after this roll is accepted.
            return result
        if (
            getattr(engine, "language", "en") != "en"
            and previous_new is not None
            and result["mode"] == "comparison"
            and len(result["cards"]) == 1
            and result["complete"]
        ):
            # A dim translated old card must not block a verified visible roll.
            # The tracker already knows the current roll and still uses its
            # identity and observed position, never title text, to assign it.
            return result
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
    if focused is not None:
        expected_count = {"current": 1, "comparison": 2}.get(focused["mode"])
        if expected_count == len(focused["cards"]) and time.monotonic() < getattr(
            engine, "_next_scene_search", 0
        ):
            # All expected cards are located. Give their local text retries time
            # to finish instead of repeatedly spending the budget on discovery.
            return focused
        if not allow_retry(engine):
            return focused
    engine._next_scene_search = time.monotonic() + 0.75
    lines = (
        await engine.read(image)
        if focused is not None
        else await primary_read(engine, image)
    )
    engine._scene_lines = lines
    if marker := fits_marker(lines):
        remember_marker(engine, marker)
    result = parse_cards(lines, image.width, image.height)
    reserve_verifications(engine, len(result["cards"]))
    result["mode"] = observe_mode(engine, lines)
    if result["mode"] == "unknown":
        result["mode"] = (
            focused["mode"] if focused else await read_action_mode(engine, image)
        )
    verified = set()
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
        verified = {
            index for index, card in enumerate(focused["cards"]) if card["complete"]
        }
        result["cards"] = [
            a if a["complete"] else b for a, b in zip(focused["cards"], result["cards"])
        ]
    # These indices already passed refinement on this exact frame. A second
    # verification can exhaust the budget and falsely invalidate the new roll.
    result = await refine_result(
        engine,
        image,
        result,
        max(image.size) > 2600,
        verified=verified,
        recover=True,
        on_verified=on_verified,
    )
    result["complete"] = bool(result["cards"]) and all(
        card["complete"] for card in result["cards"]
    )
    if result["complete"]:
        result["reason"] = ""
    if (
        result["mode"] != "transition"
        and (
            getattr(engine, "_card_tile_cursor_pending", False)
            or time.monotonic() >= getattr(engine, "_next_tile_search", 0)
        )
        and time.monotonic() - getattr(engine, "_last_located_at", 0) > 1
        and (
            not result["complete"]
            or result["mode"] == "comparison"
            and len(result["cards"]) < 2
        )
    ):
        # Discovery is a fallback, not the per-frame hot path. Tiles preserve small
        # window text on very large or unusually shaped monitors.
        tiles = await read_tiles(engine, image, cursor="_card_tile_cursor")
        if not getattr(engine, "_card_tile_cursor_pending", False):
            engine._next_tile_search = time.monotonic() + 2
        tiled = parse_cards(tiles, image.width, image.height)
        if tiled["cards"]:
            reserve_verifications(engine, len(tiled["cards"]))
            tiled = await refine_result(
                engine, image, tiled, recover=True, on_verified=on_verified
            )
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
                    remember_marker(engine, marker)
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
    if (
        cached
        and cached[0] == identity
        and (
            cached[1] != "unknown"
            or getattr(engine, "language", "en") == "en"
            or time.monotonic() < getattr(engine, "_next_action_retry", 0)
        )
    ):
        engine._frame_action = cached[2]
        return cached[1]
    lines = await primary_read(engine, ImageOps.autocontrast(area.convert("L")))
    positioned = (
        map_lines(lines, box[:2]) if all("x" in line for line in lines) else lines
    )
    mode = observe_mode(engine, positioned)
    if (
        mode == "unknown"
        and getattr(engine, "language", "en") != "en"
        and allow_retry(engine)
    ):
        # One optional caption pass per frame leaves room for card discovery.
        # Rotate treatments instead of repeatedly spending every spare retry on
        # the same small button. Each read still needs exact localized wording.
        phase = getattr(engine, "_action_retry_phase", 0)
        engine._action_retry_phase = (phase + 1) % 3
        ui_fallback = getattr(engine, "read_ui_fallback", None)
        if phase == 0:
            sample = isolate_ui_text(area)
            lines = await engine.read(sample)
        elif phase == 1 and ui_fallback:
            sample = ImageOps.autocontrast(area.convert("L"))
            lines = await ui_fallback(sample)
        else:
            scale = min(2, 2200 / max(area.size))
            sample = ImageOps.autocontrast(area.convert("L")).resize(
                (round(area.width * scale), round(area.height * scale)),
                Image.Resampling.LANCZOS,
            )
            lines = await engine.read(sample)
        positioned = map_lines(
            lines, box[:2], (sample.width / area.width, sample.height / area.height)
        )
        mode = observe_mode(engine, positioned)
    engine._action_mode_cache = (identity, mode, getattr(engine, "_frame_action", None))
    engine._next_action_retry = time.monotonic() + 0.5
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
