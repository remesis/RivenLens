# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Find the expanded card among collapsed inventory and upgrade-menu cards."""

import re
import unicodedata

from layout import clipped_box
from parser import FOOTER, VALUE, clean_text, game_card_headers, parse_cards


def hover_controls(lines):
    """Require a pair of visible mod-menu controls, not absent rolling text."""
    for menu, labels in (
        ("inventory", ("FUSION", r"(?:RIVEN\s+)?TRANSMUTE")),
        ("inventory", (r"(?:RIVEN\s+)?TRANSMUTE", "DISSOLVE")),
        ("arsenal", ("MODS", "REMOVE ALL")),
    ):
        pair = [
            [
                line
                for line in lines
                if re.fullmatch(r"\W*" + label + r"\W*", line["text"], re.I)
            ]
            for label in labels
        ]
        if any(len(matches) != 1 for matches in pair):
            continue
        first, second = (matches[0] for matches in pair)
        if not all(
            key in line for line in (first, second) for key in ("x", "y", "w", "h")
        ):
            continue
        if abs(first["y"] - second["y"]) > max(first["h"], second["h"]) * 2:
            continue
        regions = tuple(
            (
                line["x"] - line["h"],
                line["y"] - line["h"],
                line["x"] + line["w"] + line["h"],
                line["y"] + line["h"] * 2,
            )
            for line in (first, second)
        )
        return menu, regions
    return None


def hover_regions(lines):
    controls = hover_controls(lines)
    return controls[1] if controls else None


def hover_candidates(lines, width, height):
    """Isolate one title-to-footer text block, never parse the whole mod grid."""
    headers = game_card_headers(lines)
    candidates = []
    for header, weapon in headers:
        suffix = clean_text(header["text"])[len(weapon) :].strip()
        if not suffix or any(
            not (c.isalpha() or unicodedata.category(c).startswith("M") or c == "-")
            for c in suffix
        ):
            continue
        font = header.get("fontHeight", header["h"])
        center = header["x"] + header["w"] / 2
        half = max(header["w"] * 0.7, font * 6)
        body = [
            line
            for line in lines
            if header["y"] + font * 0.6 < line["y"] < header["y"] + font * 14
            and abs(line["x"] + line["w"] / 2 - center) < half
            and line["x"] >= center - half
            and line["x"] + line["w"] <= center + half
        ]
        values = [line for line in body if VALUE.search(clean_text(line["text"]))]
        if len(values) < 2:
            continue
        last_value = max(line["y"] + line["h"] for line in values)
        footers = [
            line
            for line in body
            if FOOTER.search(clean_text(line["text"]))
            or line.get("footerBounds")
            or line.get("counterBounds")
            or (
                # As in the shared parser, an isolated right-hand counter can
                # locate the footer. It cannot prove completeness or rank;
                # those still require the physical eight-pip row and retries.
                re.fullmatch(r"[0-9]{1,7}", clean_text(line["text"]))
                and line["y"] >= last_value - font * 0.5
                and line["x"] > center + font * 0.7
                and font * 0.35 <= line["h"] <= font
                and line["w"] <= font * 5
            )
        ]
        if not footers:
            continue
        first = min(values, key=lambda line: line["y"])
        if first["y"] - header["y"] > font * 3:
            continue
        footer = min(footers, key=lambda line: line["y"])
        text = [header] + [
            line
            for line in body
            # Tilt can place the counter slightly above the mastery label on
            # the same footer row. Keep both, not the background below them.
            if line["y"] <= footer["y"] + footer["h"] * 0.5
            and not any(line is other for other, _ in headers)
        ]
        parsed = parse_cards(text, width, height)
        if len(parsed["cards"]) == 1 and len(parsed["cards"][0]["stats"]) >= 2:
            candidates.append(parsed["cards"][0])
    # A title hidden by the pointer is a missed read, not permission to choose a
    # neighboring collapsed card. Ambiguous expanded candidates stay unread.
    return candidates if len(candidates) == 1 else []


def hover_region(image, card):
    """Follow the expanded text, without repeatedly scanning neighboring mods."""
    title = card["titleBounds"]
    bounds = card.get("textBounds") or card["bounds"]
    height = title["h"]
    footer = card.get("footerBounds") or card.get("counterBounds")
    return clipped_box(
        image,
        (
            bounds["x"] - height,
            bounds["y"] - height * 1.5,
            bounds["x"] + bounds["w"] + height,
            max(
                bounds["y"] + bounds["h"] + height,
                footer["y"] + footer["h"] * 5 if footer else 0,
            ),
        ),
    )
