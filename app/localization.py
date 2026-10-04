# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Exact, offline game-text adapters. Grading identities always stay canonical.

No automatic language guessing and no machine translation. Unknown or ambiguous
text remains unreadable; translating a label never repairs its numeric value.
"""

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA = json.loads(
    (Path(__file__).resolve().parents[1] / "native/data/languages.json").read_text(
        "utf-8"
    )
)
LANGUAGES = DATA["languages"]
LANGUAGES.update(
    json.loads(
        (
            Path(__file__).resolve().parents[1] / "native/data/rapid_languages.json"
        ).read_text("utf-8")
    )["languages"]
)
LATIN_UI_LANGUAGES = frozenset(("de", "es", "fr", "it", "pl", "pt", "tr"))
UI_TEXT = json.loads(
    (Path(__file__).resolve().parents[1] / "native/data/ui_strings.json").read_text(
        "utf-8"
    )
)


def text_key(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.translate(
        str.maketrans({"−": "-", "–": "-", "—": "-", "‐": "-", "’": "'"})
    )
    # Never fuse two separate numbers. Spaces around a decimal separator are OK.
    text = re.sub(r"(?<=\d)\s+(?=\d)", "~", text)
    # Unrecognized punctuation inside a number is not a disposable text glyph.
    # For example, OCR's `1 „32` must not silently become the multiplier 132.
    text = re.sub(r"(?<=\d)\s*[^\w\s+%.,()×°~-]+\s*(?=\d)", "~", text)
    return "".join(
        c
        for c in text
        if c.isalnum() or unicodedata.category(c).startswith("M") or c in "+-%.,()×°~"
    )


def name_key(text):
    # Punctuation and accents still distinguish names; only OCR word spacing is
    # immaterial (particularly for CJK). Never choose between colliding aliases.
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _literal(text):
    # Fixed-label abbreviation dots can appear as commas in OCR. Numeric
    # placeholders are compiled separately and are not altered here.
    return (
        re.escape(text_key(text))
        .replace(r"\.", "[.,]")
        # Some game translations retain the Latin faction name. CJK OCR often
        # sees its capital I as a lowercase l; this is label-only, not numeric.
        .replace("infested", "[il]nfested")
    )


def control_key(text):
    # Small all-caps UI fonts often lose accent marks in OCR. This is only for
    # fixed controls, never weapon identities or numeric/stat-label matching.
    return text_key(
        "".join(
            c
            for c in unicodedata.normalize("NFD", text)
            if unicodedata.category(c) != "Mn"
        )
    ).replace("ı", "i")


def _control_literal(text):
    return re.escape(control_key(text)).replace(r"\.", "[.,]")


def _markup(text):
    return re.sub(r"<[^>]+>", "", text)


def repair_percent(text):
    # Same tightly bounded percent-glyph repair as the English parser. A whole
    # exact percentage template must still match, and the reader range-checks
    # the repaired card before accepting it without extra scale verification.
    return re.sub(r"([+\-]\s*\d+[.,]\d)(?:70|96)(?=\s|[^\W\d_])", r"\1%", text)


def repair_decimal_spacing(text, words):
    """Join two adjacent OCR tokens belonging to one signed decimal percentage.

    Require the visible sign, decimal, unit and tightly aligned word boxes.
    Callers must still verify the complete stat read at another text scale.
    """
    match = re.match(
        r"^([+\-][0-9]{1,2})\s+([0-9]{1,3}[.,][0-9])"
        r"(?=\s*(?:%|[0Oo]/[0Oo])(?:\s|$))",
        text,
    )
    if not match or len(words) < 2:
        return text
    first, second = words[:2]
    if first["text"] != match[1] or not second["text"].startswith(match[2]):
        return text
    height = min(first["h"], second["h"])
    gap = second["x"] - first["x"] - first["w"]
    overlap = min(first["y"] + first["h"], second["y"] + second["h"]) - max(
        first["y"], second["y"]
    )
    if (
        height <= 0
        or not -0.1 * height <= gap <= 0.45 * height
        or overlap < 0.7 * height
    ):
        return text
    return match[1] + match[2] + text[match.end() :]


def roll_counter_bounds(text, words):
    """Locate counter numerals separately from an OCR-decoded circular icon."""
    match = re.fullmatch(r"\s*([oOоО↻])\s+([0-9]{1,7})\s*", text)
    if not match:
        return None
    if len(words) != 2 or words[0]["text"] != match[1] or words[1]["text"] != match[2]:
        return None
    return {key: words[1][key] for key in ("x", "y", "w", "h")}


class Profile:
    def __init__(self, language):
        if language not in LANGUAGES:
            raise ValueError(f"Unsupported game language: {language}")
        self.language = language
        self.data = LANGUAGES[language]
        self.names = {}
        self.title_names = {}
        for canonical, aliases in self.data["weapons"].items():
            # Keep a type qualifier when it is visibly part of this alias;
            # otherwise it is catalog metadata, not a printed weapon name.
            plain = re.sub(r" \((Primary|Secondary|Rifle|Melee)\)$", "", canonical)
            for alias in aliases:
                observed_name = canonical if "(" in alias and ")" in alias else plain
                self.names.setdefault(name_key(alias), set()).add(observed_name)
                alias = " ".join(unicodedata.normalize("NFKC", alias).split())
                pattern = re.compile(
                    r"\s*".join(re.escape(c) for c in alias if not c.isspace())
                    + (r"\s*" if language in ("zh", "tc", "ja", "ko") else r"\s+"),
                    re.I,
                )
                self.title_names.setdefault(alias[0].casefold(), []).append(
                    (pattern, observed_name, len(alias))
                )
        for bucket in self.title_names.values():
            bucket.sort(key=lambda row: row[2], reverse=True)
        self.ui = self.data["ui"]
        column = (
            UI_TEXT["languages"].index(language)
            if language in UI_TEXT["languages"]
            else None
        )
        self.companion_headers = {
            canonical: UI_TEXT["strings"][canonical][column]
            if column is not None
            else canonical
            for canonical in ("CURRENT ROLL", "NEW ROLL")
        }
        left, right = _markup(self.ui["footer"]).split("|LEVEL|")
        self.footer = re.compile(
            r"^"
            + _control_literal(left)
            + r"(?P<level>[0-9IOil]+)"
            + _control_literal(right),
            re.I,
        )
        left, right = _markup(self.ui["cycle"]).split("|PRICE|")
        # Windows commonly puts the price in a separate OCR row across the Kuva
        # icon. A complete action phrase alone is sufficient, as in English.
        self.cycle_caption = (
            control_key(left or right) if not left or not right else None
        )
        self.cycle = re.compile(
            r"^"
            + _control_literal(left)
            + r"[^\d]{0,3}\d[\d.,~]*[^\d]{0,3}"
            + _control_literal(right)
            + r"$"
        )
        self.templates = []
        for stat in self.data["stats"]:
            template = stat["template"]
            # Only element/icon placeholders allow a short stray OCR glyph.
            template = re.sub(r"<DT_[^>]+>", "\x01", template)
            template = _markup(template)
            parts = re.split(r"\|(?:val|STAT1)\|", template, flags=re.I)
            if len(parts) != 2:
                raise ValueError("Invalid localized stat template")

            def literal(part):
                # Element pictograms can be recognized as letters/digits in the
                # selected script (for example Korean's skull -> 쬐). Only this
                # explicit icon position permits up to two stray glyphs; the
                # surrounding number, unit and complete stat label stay exact.
                return r"(?:[^\W_]|[.,]){0,2}".join(
                    _literal(piece) for piece in part.split("\x01")
                )

            before, after = map(literal, parts)
            if language == "ko":
                # The Hangul syllable 타 can be split into Latin E by OCR.
                # Restrict that ambiguity to this complete fixed label prefix.
                before = before.replace("치명타", "치명[타e]")
                after = after.replace("치명타", "치명[타e]")
                # At small sizes 체 loses its extra stroke and becomes 세.
                before = before.replace("발사체속도", "발사[체세]속도")
                after = after.replace("발사체속도", "발사[체세]속도")
            if language == "ko" and after.endswith(r"\)"):
                # The entire parenthetical must match; its thin final stroke
                # can disappear at a card edge without changing the stat.
                after += "?"
            if language == "ru" and stat["unit"] == "s" and after.startswith("c"):
                after = "[cс]" + after[1:]
            if stat["unit"] == "x":
                number = r"(?P<pre>[x×х])?(?P<value>\d+(?:[.,]\d+)?)(?P<post>[x×х])?"
            else:
                number = r"(?P<value>[+\-]\d+(?:[.,]\d+)?)"
                # Range and punch-through omit their suffix in some frames.
                if stat["unit"] == "m":
                    number += r"(?:m|м)?"
                if stat["unit"] == "°":
                    number += "°?"
            self.templates.append(
                (
                    re.compile(
                        # A lock can resemble 6, but only before an explicit
                        # signed value; never discard a leading value digit.
                        r"^(?:6(?=[+\-])|[aoqe0аое]{0,2})"
                        + before
                        + number
                        + after
                        + r"[aoqeаое]{0,2}$"
                    ),
                    stat,
                )
            )

    def weapon(self, text):
        choices = self.names.get(name_key(re.sub(r"\s*\[\d+\]$", "", text)), set())
        return next(iter(choices)) if len(choices) == 1 else None

    def title_prefix(self, text):
        key = name_key(text)
        return len(key) >= 2 and any(alias.startswith(key) for alias in self.names)

    def title(self, text):
        """Return a canonical title anchor while preserving the visible name."""
        from parser import clean_text

        text = clean_text(text)
        if self.weapon(text):
            return None
        choices, longest = set(), 0
        for pattern, canonical, length in self.title_names.get(text[:1].casefold(), ()):
            if longest and length < longest:
                break
            # Match the name exactly, but permit OCR-added spaces within it.
            match = pattern.match(text)
            if not match:
                continue
            suffix = text[match.end() :]
            if (
                len(suffix) >= 3
                and suffix[0].isalpha()
                and all(
                    c.isalpha() or unicodedata.category(c).startswith("M") or c in " -"
                    for c in suffix
                )
            ):
                choices.add((canonical, suffix))
                # Longest exact name takes priority over its base variant.
                longest = length
        return next(iter(choices)) if len(choices) == 1 else None

    def stat(self, text):
        if self.language in ("zh", "tc", "ja", "ko"):
            # CJK OCR reports a visible decimal point as U+00B7. Only accept it
            # between digits; do not infer a missing separator or join numbers.
            text = re.sub(r"(?<=\d)\s*·\s*(?=\d)", ".", text)
        if self.language in ("zh", "tc"):
            # A leading minus is the same horizontal stroke as Chinese 一.
            # Restrict this substitution to the sign of a numeric stat template.
            text = re.sub(r"^\s*一\s*(?=\d)", "-", text)
        text = repair_percent(text)
        text = re.sub(r"[0Oo]\s*/\s*[0Oo]", "%", text)
        # Same narrow glyph correction as the English parser: the decimal must
        # already be visible; never invent it or repair an arbitrary digit.
        text = re.sub(r"([xX×хХ])\s*[lI|]\s*(?=[.,])", r"\g<1>1", text)
        if self.language == "ru":
            # A Latin o embedded between Cyrillic letters is a label glyph,
            # not a numeric repair. Keep names and standalone Latin text exact.
            text = re.sub(r"(?<=[А-Яа-яЁё])[oO](?=[А-Яа-яЁё])", "о", text)
            # Russian OCR can read the leading zero of a faction multiplier as
            # Latin/Cyrillic o. Require its multiplier, decimal and two digits;
            # the complete faction template must still match below.
            text = re.sub(
                r"^(\s*[xX×хХ]\s*)[oOоО](?=\s*[.,]\s*[0-9]{2}(?![0-9]))",
                r"\g<1>0",
                text,
            )
        key = text_key(text)
        matches = {}
        for pattern, stat in self.templates:
            match = pattern.fullmatch(key)
            if not match:
                continue
            if stat["unit"] == "x" and bool(match["pre"]) == bool(match["post"]):
                continue
            value = match["value"]
            identity = (stat["id"], value, stat["unit"])
            matches[identity] = f"{value}{stat['unit']} {stat['name']}"
        return next(iter(matches.values())) if len(matches) == 1 else None

    def control(self, text):
        key = control_key(text)
        # The companion's translated headings must still exclude its own cards
        # when it is placed on the captured monitor. These are UI, not game stats.
        for canonical, heading in self.companion_headers.items():
            if control_key(text).startswith(control_key(heading)):
                return canonical
        for name, canonical in (
            ("fits", "FITS IN"),
            ("confirm", "CONFIRM"),
            ("yes", "YES"),
            ("no", "NO"),
        ):
            if control_key(text) == control_key(_markup(self.ui[name])):
                return canonical
        if key == self.cycle_caption or self.cycle.fullmatch(key):
            return "CYCLE FOR"
        if self.language == "ko" and re.fullmatch(
            r"[^\d]{0,3}\d[\d.,~]*쿠바.{0,8}순환시키기", key
        ):
            # A pointer can obscure the intervening connective. Require both
            # the currency after a price and the complete cycle verb; this
            # recognizes a control only, never a card value or missing stat.
            return "CYCLE FOR"
        match = self.footer.match(key)
        if match:
            return "MR " + match["level"]
        return None

    def footer_bounds(self, words):
        """Use the mastery label's font, excluding decorative roll-counter glyphs."""
        # Russian's numeric mastery level precedes its text. Other locales begin
        # with a textual label; find the shortest complete footer-word prefix.
        for count in range(1, min(6, len(words)) + 1):
            prefix = words[:count]
            if self.footer.match(control_key(" ".join(w["text"] for w in prefix))):
                # Text-label height can differ by script. The visible mastery
                # numeral is the most consistent anchor to the game's pip row.
                anchor = next(
                    (w for w in prefix if re.fullmatch(r"[0-9IOil]+", w["text"])),
                    prefix[0],
                )
                return {k: anchor[k] for k in ("x", "y", "w", "h")}
        return None


@lru_cache(maxsize=15)
def profile(language):
    return Profile(language)


def adapt_lines(lines, language, *, locale=None):
    """Turn exact localized observations into the existing English parser grammar.

    Preserve all pixel bounds and display titles. Whole stat templates can span
    several OCR rows, but must stay in their own card/column. Unmatched content
    is deliberately marked unreadable rather than silently dropping a trait.
    """
    if language == "en":
        return lines
    from parser import game_card_headers, clean_text, join_title_lines

    locale = locale or profile(language)
    # A long localized weapon name can wrap before its generated suffix. Join
    # only exact name prefixes, with the same geometry limits as English titles.
    joined_headers, title_rows = {}, set()
    for index, line in enumerate(lines):
        if not locale.title_prefix(line["text"]):
            continue
        header, used = dict(line), []
        for _ in range(2):
            center = header["x"] + header["w"] / 2
            nearby = [
                (n, row)
                for n, row in enumerate(lines)
                if line["h"] * -0.25
                < row["y"] - header["y"] - header["h"]
                < line["h"] * 1.1
                and abs(row["x"] + row["w"] / 2 - center)
                < max(header["w"], row["w"]) * 0.65
            ]
            if not nearby:
                break
            number, following = min(nearby, key=lambda entry: entry[1]["y"])
            header = join_title_lines(header, following)
            used.append(number)
            if locale.title(header["text"]):
                joined_headers[index] = header
                title_rows.update(used)
                break
    converted = []
    for index, line in enumerate(lines):
        if index in title_rows:
            continue
        line = joined_headers.get(index, line)
        raw = clean_text(line["text"])
        control = locale.control(raw)
        title = locale.title(raw) if not control else None
        name = locale.weapon(raw) if not title and not control else None
        text = control or (title[0] + " " + title[1] if title else name) or raw
        converted.append(
            {**line, "text": text, "displayText": raw, "language": language}
        )

    headers = game_card_headers(converted)
    if not 1 <= len(headers) <= 2:
        return converted
    headers.sort(key=lambda row: row[0]["x"])
    replacements, consumed = {}, set()
    for i, (header, _) in enumerate(headers):
        center = header["x"] + header["w"] / 2
        centers = [h["x"] + h["w"] / 2 for h, _ in headers]
        left = (center + centers[i - 1]) / 2 if i else float("-inf")
        right = (center + centers[i + 1]) / 2 if i + 1 < len(headers) else float("inf")
        font = header.get("fontHeight", header["h"])
        # A completed title includes its continuation in displayText, while its
        # geometry intentionally remains the original font-size anchor.
        remaining_title = clean_text(header.get("displayText", header["text"]))
        rows = sorted(
            [
                (n, row)
                for n, row in enumerate(converted)
                if header["y"] + header["h"] * 0.6
                < row["y"]
                < header["y"] + header["h"] * 14
                and left < row["x"] + row["w"] / 2 < right
                and abs(row["x"] + row["w"] / 2 - center)
                < max(header["w"] * 0.85, header["h"] * 5)
                and (
                    row["h"] >= font * 0.4
                    or abs(row["x"] + row["w"] / 2 - center) < font * 1.5
                    or row.get("footerBounds")
                )
            ],
            key=lambda entry: entry[1]["y"],
        )
        cursor = 0
        while cursor < len(rows):
            index, row = rows[cursor]
            if row["text"].startswith(("MR ", "CONFIRM", "CYCLE FOR")):
                break
            if row.get("counterBounds"):
                cursor += 1
                continue
            raw = row["displayText"]
            if (
                raw
                and remaining_title.endswith(raw)
                and row["y"] < header["y"] + font * 2.2
            ):
                cursor += 1
                continue
            joined = dict(row)
            best = None
            for end in range(cursor, min(cursor + 6, len(rows))):
                if end > cursor:
                    following = rows[end][1]
                    if following["text"].startswith(("MR ", "CONFIRM", "CYCLE FOR")):
                        break
                    if following.get("counterBounds"):
                        break
                    if following["y"] - joined["y"] - joined["h"] > font * 1.4:
                        break
                    joined = join_title_lines(joined, following)
                found = locale.stat(joined.get("displayText", joined["text"]))
                if found:
                    # A short label may also begin an annotated/wrapped one,
                    # e.g. Critical Chance followed by its heavy-attack note.
                    raw_text = joined.get("displayText", joined["text"])
                    best = (
                        end,
                        {
                            **joined,
                            "text": found,
                            "localizedPercentRepair": repair_percent(raw_text)
                            != raw_text,
                        },
                    )
            if best:
                end, replacements[index] = best
                consumed.update(rows[n][0] for n in range(cursor + 1, end + 1))
                cursor = end + 1
            else:
                replacements[index] = {
                    **row,
                    "localizedUnreadable": any(c.isalpha() for c in raw)
                    or bool(re.search(r"[+%−-]", raw)),
                    "text": "Unreadable localized stat " + raw
                    if any(c.isalpha() for c in raw)
                    else raw,
                }
                cursor += 1
    return [
        replacements.get(n, row) for n, row in enumerate(converted) if n not in consumed
    ]
