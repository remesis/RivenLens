# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Preserve separately detected text in its independently anchored card column."""

from copy import deepcopy
import math
import re

from localization import control_key
from parser import FOOTER, NAME_KEYS, clean_text
from rapid_text import caption_namespace, observe_caption, weapon_key
from variants import VARIANT_CHOICES


def bounds(row):
    values = tuple(row.get(key) for key in ("x", "y", "w", "h"))
    return (
        values
        if (
            all(
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                for v in values
            )
            and values[2] > 0
            and values[3] > 0
        )
        else None
    )


def footer(row, locale):
    return bool(
        bounds(row)
        and FOOTER.search(clean_text(locale.control(row["text"]) or row["text"]))
    )


def anchors_for(title, rows, locale):
    x, y, width, _ = bounds(title)
    height = title.get("fontHeight", title.get("glyphHeight", title["h"]))
    center = x + width / 2
    footers = [
        row
        for row in rows
        if footer(row, locale)
        and 2 * height < row["y"] - y < 16 * height
        and abs(row["x"] + row["w"] / 2 - center) < width * 0.85
        and 0.25 * height < row["h"] < 1.5 * height
    ]
    if len(footers) != 1:
        return None, []
    end = footers[0]
    body = [
        row
        for row in rows
        if bounds(row)
        and y + height * 0.6 < row["y"] < row["y"] + row["h"] <= end["y"]
        and abs(row["x"] + row["w"] / 2 - center) < width * 0.65
    ]
    return end, body


def independent_stats(rows, locale):
    complete = [row for row in rows if locale.stat(row["text"])]
    return len({(r["y"], r["text"]) for r in complete}) >= 2 and any(
        abs(a["y"] - b["y"]) >= min(a["h"], b["h"]) * 0.5
        for a in complete
        for b in complete
    )


def restored_piece(piece):
    return {
        **deepcopy(piece),
        "rawPieces": [deepcopy(piece)],
        "glyphHeight": piece["h"],
    }


def split_columns(rows, locale):
    """Undo detector grouping only when independent anchors prove ownership.

    No text is discarded, and a separated stat still passes ordinary localized
    parsing and card verification. URLs and other off-card pieces are retained.
    """
    titles = [row for row in rows if bounds(row) and locale.title(row["text"])]
    columns = [
        (title, end)
        for title in titles
        if (end := anchors_for(title, rows, locale)[0]) is not None
    ]
    if len(columns) == 2:
        columns.sort(key=lambda item: item[0]["x"])
        first, second = (item[0] for item in columns)
        if (
            columns[0][1] is columns[1][1]
            or first["x"] + first["w"] >= second["x"]
            or abs(first["y"] - second["y"]) > 2 * max(first["h"], second["h"])
        ):
            columns = []
    else:
        columns = []

    def column(piece):
        matches = [
            index
            for index, (title, end) in enumerate(columns)
            if title["y"] + 0.6 * title["h"]
            < piece["y"]
            < piece["y"] + piece["h"]
            <= end["y"]
            and abs(piece["x"] + piece["w"] / 2 - title["x"] - title["w"] / 2)
            < title["w"] * 0.65
        ]
        return matches[0] if len(matches) == 1 else None

    replacements = {}
    for index, row in enumerate(rows):
        pieces = row.get("rawPieces", [])
        if len(pieces) < 2 or not all(bounds(piece) for piece in pieces):
            continue
        if columns and len(pieces) == 2 and all(locale.stat(p["text"]) for p in pieces):
            left, right = sorted(pieces, key=lambda p: p["x"])
            if (
                column(left) == 0
                and column(right) == 1
                and right["x"] - left["x"] - left["w"] >= max(left["h"], right["h"])
            ):
                replacements[index] = [restored_piece(p) for p in (left, right)]
                continue
        raw_titles = [piece for piece in pieces if locale.title(piece["text"])]
        if len(raw_titles) == 1 and not locale.title(row["text"]):
            title = raw_titles[0]
            end, body = anchors_for(title, rows, locale)
            center = title["x"] + title["w"] / 2
            separate = all(
                max(
                    title["x"] - piece["x"] - piece["w"],
                    piece["x"] - title["x"] - title["w"],
                )
                >= max(title["h"], piece["h"])
                and abs(piece["x"] + piece["w"] / 2 - center) >= title["w"] * 0.65
                and not footer(piece, locale)
                and not locale.stat(piece["text"])
                for piece in pieces
                if piece is not title
            )
            if end is not None and separate and independent_stats(body, locale):
                replacements[index] = [restored_piece(p) for p in pieces]
                continue
        if len(pieces) != 2 or locale.stat(row["text"]):
            continue
        urls = [
            piece
            for piece in pieces
            if re.fullmatch(r"https?://[A-Za-z0-9]\S*", piece["text"], re.I)
        ]
        if len(urls) == 1:
            url = urls[0]
            piece = next(p for p in pieces if p is not url)
            candidates = []
            for title in titles:
                end, body = anchors_for(title, rows, locale)
                center = title["x"] + title["w"] / 2
                if (
                    end is None
                    or abs(piece["x"] + piece["w"] / 2 - center) >= title["w"] * 0.65
                    or abs(url["x"] + url["w"] / 2 - center) < title["w"] * 0.85
                    or max(
                        piece["x"] - url["x"] - url["w"],
                        url["x"] - piece["x"] - piece["w"],
                    )
                    < min(piece["h"], url["h"])
                    or not title["y"] + 0.6 * title["h"]
                    < piece["y"]
                    < piece["y"] + piece["h"]
                    <= end["y"]
                    or not independent_stats([r for r in body if r is not row], locale)
                ):
                    continue
                text, bottom = piece["text"], piece["y"] + piece["h"]
                following = sorted(
                    (r for r in body if r["y"] > piece["y"]), key=lambda r: r["y"]
                )
                for continuation in following[:2]:
                    if locale.stat(text) or locale.stat(continuation["text"]):
                        break
                    if (
                        continuation["y"] - bottom > title["h"] * 1.4
                        or continuation["y"]
                        < bottom - min(piece["h"], continuation["h"]) * 0.5
                        or locale.title(continuation["text"])
                    ):
                        break
                    text += " " + continuation["text"]
                    bottom = max(bottom, continuation["y"] + continuation["h"])
                if locale.stat(text):
                    candidates.append(title)
            if len(candidates) == 1:
                replacements[index] = [restored_piece(p) for p in pieces]
    if not replacements:
        return rows
    return sorted(
        [
            item
            for index, row in enumerate(rows)
            for item in replacements.get(index, [row])
        ],
        key=lambda r: (r["y"], r["x"]),
    )


def join_names(rows, locale):
    """Join an observed wrapped known name, never a generated title suffix."""
    consumed, output = set(), []
    for index, row in enumerate(rows):
        if index in consumed:
            continue
        changed = dict(row)
        if (
            bounds(row)
            and locale.title_prefix(row["text"])
            and not locale.title(row["text"])
        ):
            current = row
            text, used = row["text"], []
            for _ in range(2):
                candidates = [
                    (n, other)
                    for n, other in enumerate(rows)
                    if n != index
                    and n not in consumed
                    and bounds(other)
                    and -0.25 * row["h"]
                    < other["y"] - current["y"] - current["h"]
                    < row["h"] * 1.1
                    and abs(other["x"] + other["w"] / 2 - row["x"] - row["w"] / 2)
                    < max(row["w"], other["w"]) * 0.65
                ]
                if not candidates:
                    break
                n, following = min(candidates, key=lambda pair: pair[1]["y"])
                text += " " + following["text"]
                used.append(n)
                if name := locale.weapon(text):
                    changed.update(text=name, displayText=text)
                    consumed.update(used)
                    break
                if not locale.title_prefix(text):
                    break
                current = following
        output.append(changed)
    return output


def observe_captions(rows, locale):
    """Only complete names in the visible weapon panel establish context."""

    def name(text):
        return locale.weapon(text) or NAME_KEYS.get(clean_text(text).casefold())

    fits = [
        r
        for r in rows
        if bounds(r) and (locale.control(r["text"]) or r["text"]) == "FITS IN"
    ]
    if len(fits) == 1:
        anchor = fits[0]
        nearby = sorted(
            (
                r
                for r in rows
                if bounds(r)
                and anchor["y"] + anchor["h"] < r["y"] < anchor["y"] + 24 * anchor["h"]
                and abs(r["x"] + r["w"] / 2 - anchor["x"] - anchor["w"] / 2)
                <= 10 * anchor["h"]
            ),
            key=lambda r: r["y"],
        )
        choices = {n for r in nearby if (n := name(r["text"]))}
        combined = name(" ".join(r["text"] for r in nearby))
        if combined:
            choices.add(combined)
        for choice in choices:
            observe_caption(choice)
    ranked = control_key(re.sub(r"<[^>]+>", "", locale.ui["ranked"]))
    anchors = [r for r in rows if bounds(r) and control_key(r["text"]) == ranked]
    if len(anchors) == 1:
        anchor = anchors[0]
        nearby = [
            r
            for r in rows
            if bounds(r)
            and anchor["y"] - 12 * anchor["h"]
            < r["y"]
            < anchor["y"] - 0.5 * anchor["h"]
            and abs(r["x"] + r["w"] / 2 - anchor["x"] - anchor["w"] / 2)
            <= 6 * anchor["h"]
        ]
        if not any(footer(r, locale) or r.get("footerBounds") for r in nearby):
            choices = {n for r in nearby if (n := name(r["text"]))}
            for choice in choices:
                observe_caption(choice)


def contextual_titles(rows, locale, allowed=None):
    """A same-frame caption can corroborate a complete Russian weapon prefix.

    Generated suffixes and all stat quantities remain literal observations.
    Neither saved variants nor grading ranges participate in recognition.
    """
    if locale.language != "ru":
        return rows
    if allowed is None:
        captions = caption_namespace()
        if len(captions) != 1:
            return rows
        allowed = VARIANT_CHOICES.get(captions[0], ())
    aliases = [
        (canonical, alias)
        for canonical, names in locale.data["weapons"].items()
        if canonical in allowed
        for alias in names
    ]
    extended = str.maketrans({"f": "ф", "F": "Ф", "n": "н", "N": "Н"})
    output = deepcopy(rows)
    for row in output:
        text = row["text"]
        if locale.title(text):
            continue
        candidates = []
        for match in re.finditer(r"\s+", text):
            prefix, suffix = text[: match.start()], text[match.end() :]
            if (
                len(suffix) < 3
                or not suffix[0].isalpha()
                or not all(c.isalpha() or c in " -" for c in suffix)
            ):
                continue
            key = weapon_key(prefix.translate(extended), "ru")
            for canonical, alias in aliases:
                wanted = weapon_key(alias.translate(extended), "ru")
                differences = [(a, b) for a, b in zip(key, wanted) if a != b]
                glyph = (
                    len(key) == len(wanted) >= 4
                    and len(differences) == 1
                    and set(differences[0]) == {"п", "н"}
                )
                if key == wanted or glyph:
                    proposed = alias + text[match.start() :]
                    if (title := locale.original.title(proposed)) and title[
                        0
                    ] == canonical:
                        candidates.append((len(wanted), canonical, proposed))
        if candidates:
            longest = max(c[0] for c in candidates)
            unique = {c[1:] for c in candidates if c[0] == longest}
            if len(unique) == 1:
                row.setdefault("displayText", text)
                row["text"] = next(iter(unique))[1]
    return output


def companion_titles(rows, locale):
    """Corroborate a prefix only beside one independently identified card.

    Distinct physical footer observations anchor the two non-overlapping
    columns. This does not determine current/new roles or change stat text.
    """
    if locale.language != "ru":
        return rows
    footers = [r for r in rows if footer(r, locale)]

    def owner(row):
        if not bounds(row):
            return None
        matches = [
            end
            for end in footers
            if 2 * row["h"] < end["y"] - row["y"] < 16 * row["h"]
            and abs(end["x"] + end["w"] / 2 - row["x"] - row["w"] / 2) < row["w"] / 2
            and 0.25 * row["h"] < end["h"] < 1.5 * row["h"]
        ]
        return matches[0] if len(matches) == 1 else None

    anchors = [
        (r, title, end)
        for r in rows
        if (title := locale.title(r["text"])) and (end := owner(r)) is not None
    ]
    if len(anchors) != 1:
        return rows
    anchor, title, anchor_footer = anchors[0]
    output = []
    for row in rows:
        end = owner(row)
        if (
            row is not anchor
            and not locale.title(row["text"])
            and bounds(row)
            and end is not None
            and end is not anchor_footer
            and abs(row["y"] - anchor["y"]) <= 2 * max(row["h"], anchor["h"])
            and 0.5 <= row["h"] / anchor["h"] <= 2
            and (
                row["x"] + row["w"] < anchor["x"]
                or anchor["x"] + anchor["w"] < row["x"]
            )
        ):
            output.extend(
                contextual_titles([row], locale, VARIANT_CHOICES.get(title[0], ()))
            )
        else:
            output.append(row)
    return output


def normalize_controls(rows, locale):
    """Fixed UI-label recovery does not supply card roles or numeric evidence."""
    expected = control_key(re.sub(r"<[^>]+>", "", locale.ui["fits"]))
    output = deepcopy(rows)
    for row in output:
        text = row["text"]
        observed = control_key(text)
        words = [word for piece in row.get("rawPieces", ()) for word in piece["words"]]
        if (
            len(words) == 2
            and bounds(words[0])
            and bounds(words[1])
            and len(words[0]["text"]) == 1
            and not words[0]["text"].isalpha()
            and words[0]["w"] <= words[1]["h"]
            and words[0]["x"] + words[0]["w"] <= words[1]["x"]
            and locale.control(words[1]["text"]) == "TRADEABLE"
        ):
            # The trade glyph can be recognized as a separate character. Keep
            # the complete, independently boxed label, not a substring guess.
            row.setdefault("displayText", text)
            row.update(
                text="TRADEABLE",
                **{key: words[1][key] for key in ("x", "y", "w", "h")},
            )
        if (
            len(expected) >= 8
            and len(observed) == len(expected)
            and observed.isalpha()
            and sum(a != b for a, b in zip(expected, observed)) == 1
            and not locale.control(text)
        ):
            row.setdefault("displayText", text)
            row["text"] = "FITS IN"
        if locale.language == "en":
            match = re.fullmatch(
                r"([+-]\s*\d+(?:\s*[.,]\s*\d+)?\s*%)\s*(?:Irpact|Irepact)", text, re.I
            )
            if match:
                row.update(text=match[1] + " Impact", displayText=text)
    if locale.language in ("en", "ru"):
        if locale.language == "en":
            action_pattern = r"CYCLE\s+FOR\d+(?:[.,]\d+)*"
            remaining_pattern = r"Remaining\s+Kuva:\s*\d+(?:[.,]\d+)*"

            def key(text):
                return text
        else:
            action_pattern = r"(?:измениьза|изменитза)[^\d]{0,3}\d[\d.,~]*[^\d]{0,3}"
            remaining_pattern = r"кувыосталось:?\d[\d.,~]*"
            key = control_key
        actions = [
            r
            for r in output
            if bounds(r) and re.fullmatch(action_pattern, key(r["text"]), re.I)
        ]
        remaining = [
            r
            for r in output
            if bounds(r) and re.fullmatch(remaining_pattern, key(r["text"]), re.I)
        ]
        if len(actions) == len(remaining) == 1 and not any(
            locale.control(r["text"]) in ("CONFIRM", "YES", "NO") for r in output
        ):
            action, companion = actions[0], remaining[0]
            if (
                0.5 * action["h"] <= companion["y"] - action["y"] <= 3 * action["h"]
                and abs(
                    companion["x"] + companion["w"] / 2 - action["x"] - action["w"] / 2
                )
                <= 2 * action["h"]
            ):
                action.setdefault("displayText", action["text"])
                action["text"] = "CYCLE FOR"
    return output
