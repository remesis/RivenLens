# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Parse visible card text; reject ambiguous or incomplete OCR readings."""

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

CATALOG = json.loads(
    (Path(__file__).resolve().parents[1] / "native/data/catalog.json").read_text(
        encoding="utf-8"
    )
)
NAMES = sorted(
    {row[0] for row in CATALOG["families"]}
    | {v[0] for w in CATALOG["weapons"] for v in w[2]},
    key=len,
    reverse=True,
)
TRAITS = {}
COMBINED_IDS = set()
DEFINITIONS = {}
for family in CATALOG["families"]:
    DEFINITIONS[family[0]] = family[1]
for family_index, _, variants in CATALOG["weapons"]:
    for variant in variants:
        DEFINITIONS[variant[0]] = CATALOG["families"][family_index][1]
TRAITS_BY_KIND = {
    kind: {trait["id"]: trait for trait in definition}
    for kind, definition in CATALOG["definitions"].items()
}
TRAIT_UNITS = {}
for definition in CATALOG["definitions"].values():
    for trait in definition:
        TRAIT_UNITS.setdefault(trait["id"], set()).add(trait["unit"])


def clean_text(text):
    text = unicodedata.normalize("NFKC", text)
    return re.sub(
        r"\s+",
        " ",
        text.translate(
            str.maketrans(
                {
                    "−": "-",
                    "–": "-",
                    "\u2014": "-",
                    "‐": "-",
                    "‑": "-",
                    "﹣": "-",
                    "’": "'",
                    "‘": "'",
                    "％": "%",
                    "✕": "×",
                    "⨯": "×",
                }
            )
        ),
    ).strip()


NAME_KEYS = {clean_text(name).casefold(): name for name in NAMES}
NAME_BUCKETS = {}
NAME_PREFIXES = set()
for name_key, canonical in NAME_KEYS.items():
    NAME_BUCKETS.setdefault(name_key.split()[0], []).append((name_key, canonical))
    words = name_key.split()
    NAME_PREFIXES.update(" ".join(words[:end]) for end in range(1, len(words) + 1))
_KIND_GROUPS = {}
for name, kind in DEFINITIONS.items():
    base = re.sub(r"\s*\((?:Primary|Secondary|Rifle|Melee)\)$", "", name, flags=re.I)
    _KIND_GROUPS.setdefault(base, set()).add(kind)
DEFINITION_CHOICES = {
    name: ({kind} if "(" in name else _KIND_GROUPS.get(name, {kind}))
    for name, kind in DEFINITIONS.items()
}


def normalize(text):
    return re.sub(r"[^a-z]", "", text.lower())


for definition in CATALOG["definitions"].values():
    for trait in definition:
        TRAITS[normalize(trait["name"])] = (trait["id"], trait["name"])
for alias, original in {
    "Attack Speed": "Fire Rate",
    "Ammo Max": "Ammo Maximum",
    "Damage vs Corpus": "Damage to Corpus",
    "Damage vs Grineer": "Damage to Grineer",
    "Damage vs Infested": "Damage to Infested",
    "Electric": "Electricity",
    "Additional Combo Count": "Additional Combo Count Chance",
    "Chance to Gain Additional Combo Count": "Additional Combo Count Chance",
    "Chance to Gain Extra Combo Count": "Additional Combo Count Chance",
    "Slide Attack Critical Chance": "Critical Chance for Slide Attack",
    "Damage vs Orokin": "Damage to Orokin",
}.items():
    if normalize(original) in TRAITS:
        TRAITS[normalize(alias)] = TRAITS[normalize(original)]
for name in [
    "Blast",
    "Corrosive",
    "Gas",
    "Magnetic",
    "Radiation",
    "Viral",
    "Damage to Orokin",
    "Damage to Techrot",
    "Damage to Scaldra",
    "Weakpoint Damage",
    "Weakpoint Critical Chance",
    "Ammo Efficiency",
    "Magazine Reload While Holstered",
    "Status Damage",
    "Heavy Attack Damage",
    "Heavy Attack Windup Speed",
    "Parry Angle",
    "Slam Damage",
]:
    TRAITS[normalize(name)] = (name.lower().replace(" ", "-"), name)
    COMBINED_IDS.add(name.lower().replace(" ", "-"))
for alias, original in {
    **{
        name + " Damage": name
        for name in ("Blast", "Corrosive", "Gas", "Magnetic", "Radiation", "Viral")
    },
    "Melee Damage On Heavy Attack": "Heavy Attack Damage",
    "Slam Attack Damage": "Slam Damage",
    "Reload While Holstered": "Magazine Reload While Holstered",
}.items():
    TRAITS[normalize(alias)] = TRAITS[normalize(original)]
for identity in COMBINED_IDS - {"parry-angle"}:
    TRAIT_UNITS[identity] = {"x" if identity.startswith("damage-to-") else "%"}
TRAIT_UNITS["parry-angle"] = {""}
TRAITS[normalize("Chance to gain additional combo count")] = TRAITS[
    normalize("Additional Combo Count Chance")
]
for faction in ("Corpus", "Grineer", "Infested", "Orokin", "Techrot", "Scaldra"):
    TRAITS[normalize("Damage vs " + faction)] = TRAITS[
        normalize("Damage to " + faction)
    ]

VALUE = re.compile(
    r"(?P<sign>[+\-])\s*(?P<value>\d(?:[\d\s]*\d)?(?:\s*[.,]\s*\d+)?)\s*(?P<unit>%|[xX×°]|[ms](?=\s|[^a-z]|$))?"
)
MULTIPLIER = re.compile(
    r"(?:[x×]\s*(?P<prefix>\d+(?:\s*[.,]\s*\d+)?)|(?P<suffix>\d+(?:\s*[.,]\s*\d+)?)\s*[x×])\s*",
    re.I,
)
MULTIPLIER_NOTE = re.compile(r"\(\s*(?:[x×]\s*2|2\s*[x×])\s+for[^)]*\)", re.I)
OPEN_MULTIPLIER_NOTE = re.compile(
    r"\(\s*(?:[x×](?:\s*2)?|2(?:\s*[x×])?)(?=\s|$)[^)]*$", re.I
)
FOOTER = re.compile(r"^[\W_]*M\s*R\s*[0-9IOil]+(?:\s|$)", re.I)


@lru_cache(maxsize=1024)
def weapon_title(text):
    text = clean_text(text).casefold()
    if text in NAME_KEYS or not text:
        return None
    if re.search(r"\b(?:show ranked|cancel|confirm|cycle for|fits in)\b", text):
        return None
    for name, canonical in NAME_BUCKETS.get(text.split()[0], []):
        if text.startswith(name + " "):
            suffix = text[len(name) :].strip()
            # A card has a generated suffix, not just the weapon's FITS IN label.
            if (
                len(suffix) >= 3
                and suffix[0].isalpha()
                and all(
                    c.isalpha() or unicodedata.category(c).startswith("M") or c in " -"
                    for c in suffix
                )
            ):
                return canonical
    return None


@lru_cache(maxsize=2048)
def identify_trait(text):
    text = clean_text(text)
    text = MULTIPLIER_NOTE.sub("", text)
    key = normalize(text)
    if key in TRAITS:
        return TRAITS[key]
    # Element icons become isolated letters (e.g. the Toxin skull -> "Z").
    # Only strip a short separate leading token, and require an exact known label.
    iconless = re.sub(r"^\s*[^\s]{1,2}\s+", "", text)
    if normalize(iconless) in TRAITS:
        return TRAITS[normalize(iconless)]
    # Match lock glyphs around the whole label without trimming its letters.
    # Exact matching preserves names ending in glyph-like characters.
    keys = {key, normalize(iconless)}
    candidates = {
        v
        for k, v in TRAITS.items()
        if any(
            re.fullmatch(r"[aoqe]{0,2}" + re.escape(k) + r"[aoqe]{0,2}", candidate)
            for candidate in keys
        )
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def join_title_lines(first, following):
    left = min(first["x"], following["x"])
    right = max(first["x"] + first["w"], following["x"] + following["w"])
    text = clean_text(first["text"])
    separator = "" if text.endswith("-") else " "
    return {
        **first,
        "text": text + separator + clean_text(following["text"]),
        **(
            {
                "displayText": clean_text(first["displayText"])
                + ("" if clean_text(first["displayText"]).endswith("-") else " ")
                + clean_text(following.get("displayText", following["text"]))
            }
            if "displayText" in first
            else {}
        ),
        "fontHeight": first.get("fontHeight", first["h"]),
        "x": left,
        "w": right - left,
        "h": following["y"] + following["h"] - first["y"],
    }


def complete_title(header, lines):
    """Join a visibly wrapped suffix, without guessing from traits or past rolls."""
    anchor = header
    while clean_text(header["text"]).endswith("-"):
        font = header.get("fontHeight", header["h"])
        center = header["x"] + header["w"] / 2
        nearby = [
            line
            for line in lines
            if -font * 0.25 < line["y"] - header["y"] - header["h"] < font * 1.1
            and font * 0.55 <= line["h"] <= font * 1.6
            and abs(line["x"] + line["w"] / 2 - center)
            < max(header["w"], line["w"]) * 0.35
        ]
        if not nearby:
            break
        following = min(nearby, key=lambda line: line["y"])
        text = clean_text(following["text"])
        if (
            not text
            or not text[0].isalpha()
            or not all(
                c.isalpha() or unicodedata.category(c).startswith("M") or c == "-"
                for c in text
            )
            or identify_trait(text)
        ):
            break
        joined = join_title_lines(header, {**following, "text": text})
        if not weapon_title(joined["text"]):
            break
        header = joined
    # A continuation completes the display name, not the title's font size.
    # Keep the existing anchor so stat crops and card positions do not expand.
    return {
        **anchor,
        "text": header["text"],
        **({"displayText": header["displayText"]} if "displayText" in header else {}),
    }


def card_headers(lines):
    headers = []
    for line in lines:
        name = weapon_title(line["text"])
        if name:
            headers.append((complete_title(line, lines), name))
            continue
        if clean_text(line["text"]).casefold() not in NAME_PREFIXES:
            continue
        # Long weapon names can wrap before the generated Riven suffix.
        joined, last = dict(line), line
        for _ in range(2):
            center = last["x"] + last["w"] / 2
            nearby = [
                other
                for other in lines
                if last["h"] * 0.5 < other["y"] - last["y"] < last["h"] * 2.1
                and abs(other["x"] + other["w"] / 2 - center)
                < max(last["w"], other["w"]) * 0.65
            ]
            if not nearby:
                break
            following = min(nearby, key=lambda other: other["y"])
            joined = join_title_lines(joined, following)
            name = weapon_title(joined["text"])
            if name:
                headers.append((complete_title(joined, lines), name))
                break
            last = following
    return headers


def companion_regions(lines):
    """Bound the companion using two visible, language-independent anchors."""
    brands = [line for line in lines if re.search(r"\bRivenLens\b", line["text"], re.I)]
    links = [
        line
        for line in lines
        if normalize(line["text"]) in ("httpsarbiguide", "discordggarbitrations")
    ]
    regions = []
    for brand in brands:
        font = brand["h"]
        nearby = [
            link
            for link in links
            if font * 8 < link["y"] - brand["y"] < font * 65
            and abs(link["x"] - brand["x"]) < font * 40
        ]
        # A brand alone says nothing about the window's size or position.
        if nearby:
            anchors = [brand, *nearby]
            regions.append(
                (
                    min(row["x"] for row in anchors) - font * 2,
                    brand["y"] - font * 2,
                    max(row["x"] + row["w"] for row in anchors) + font * 2,
                    max(row["y"] + row["h"] for row in nearby) + font * 2,
                )
            )
    return regions


def game_card_headers(lines):
    headers = card_headers(lines)
    # If the companion was left on the captured monitor, do not re-read its
    # own displayed card titles as additional game cards (pixels only).
    companion_labels = [
        line
        for line in lines
        if re.search(
            r"\bROLL\b|\b(?:CURRENT|NEW)\s+RO(?:U|LI|L1|IL|L)\b",
            line["text"],
            re.I,
        )
    ]
    regions = companion_regions(lines)
    headers = [
        (header, name)
        for header, name in headers
        if not any(
            left < header["x"] + header["w"] / 2 < right
            and top < header["y"] + header["h"] / 2 < bottom
            for left, top, right, bottom in regions
        )
        and not any(
            (
                0 < header["y"] - label["y"] < max(100, header["h"] * 5)
                and abs(header["x"] + header["w"] / 2 - label["x"] - label["w"] / 2)
                < max(header["w"], label["w"])
            )
            or (
                # OCR may lose one of the adjacent companion headings. Its
                # matching-sized title still shares the other panel's row.
                0 < header["y"] - label["y"] < label["h"] * 4
                and header["h"] <= label["h"] * 2
                and abs(header["x"] + header["w"] / 2 - label["x"] - label["w"] / 2)
                < label["h"] * 32
            )
            for label in companion_labels
        )
    ]
    headers.sort(key=lambda entry: entry[0]["x"])
    return headers


def parse_cards(lines, width, height):
    headers = game_card_headers(lines)
    if not 1 <= len(headers) <= 2:
        return {
            "cards": [],
            "complete": False,
            "reason": "Waiting for a readable Riven card.",
        }
    cards = []
    for header, name in headers:
        center = header["x"] + header["w"] / 2
        half_width = max(header["w"] * 0.85, header["h"] * 5)
        font_height = header.get("fontHeight", header["h"])
        # Four long, locked stats can wrap onto substantially more than 8 lines.
        lower_limit = min(height, header["y"] + header["h"] * 14)
        neighbor = next(
            (other for other, _ in headers if other["x"] > header["x"]), None
        )
        left_neighbor = next(
            (other for other, _ in reversed(headers) if other["x"] < header["x"]), None
        )
        right_boundary = (
            (center + neighbor["x"] + neighbor["w"] / 2) / 2 if neighbor else width
        )
        left_boundary = (
            (center + left_neighbor["x"] + left_neighbor["w"] / 2) / 2
            if left_neighbor
            else 0
        )
        candidates = sorted(
            [
                line
                for line in lines
                if header["y"] + header["h"] * 0.6 < line["y"] < lower_limit
                and abs(line["x"] + line["w"] / 2 - center) < half_width
                and left_boundary < line["x"] + line["w"] / 2 < right_boundary
                # Small neighboring UI labels are not card text. Keep centered
                # fragments and footers even when OCR gives them short boxes.
                and (
                    line["h"] >= font_height * 0.4
                    or abs(line["x"] + line["w"] / 2 - center) < font_height * 1.5
                    or line.get("footerBounds")
                )
            ],
            key=lambda line: line["y"],
        )
        stats, pending, footer, invalid = [], None, False, False
        normalized_percent = normalized_spacing = False
        footer_bounds = None
        counter_candidates = []
        unreadable = []
        text_rows = [header]
        stat_rows = []

        def finish():
            nonlocal pending, invalid, normalized_percent
            if pending is None:
                return
            trait = identify_trait(pending["label"])
            if not trait:
                invalid = True
                unreadable.append(pending["raw"])
            else:
                trait_id, trait_name = trait
                family_definition = DEFINITIONS.get(name)
                if trait_id == "damage" and family_definition in ("Melee", "Zaw"):
                    trait_id, trait_name = "melee-damage", "Melee Damage"
                definition = TRAITS_BY_KIND.get(family_definition, {}).get(trait_id)
                if definition is None:
                    definition = next(
                        (
                            TRAITS_BY_KIND[kind][trait_id]
                            for kind in DEFINITION_CHOICES.get(name, ())
                            if trait_id in TRAITS_BY_KIND[kind]
                        ),
                        None,
                    )
                # A known trait newly enabled for a category can still be read;
                # the frontend must mark its missing category baseline unknown.
                expected_unit = definition["unit"] if definition else None
                if expected_unit is None and len(TRAIT_UNITS.get(trait_id, ())) == 1:
                    expected_unit = next(iter(TRAIT_UNITS[trait_id]))
                # Percentage rolls display at most one decimal. Windows OCR can
                # fuse the percent glyph into a trailing 70 or 96. Only restore
                # that exact shape for a known percentage trait with no unit;
                # never truncate ordinary values or touch faction multipliers.
                percent = re.fullmatch(
                    r"([+-]?\d+[.,]\d)(?:70|96)", pending.get("valueText", "")
                )
                if not pending["unit"] and expected_unit == "%" and percent:
                    pending["value"] = float(percent[1].replace(",", "."))
                    pending["valueText"] = percent[1]
                    pending["unit"] = "%"
                    normalized_percent = True
                # The game omits the suffix for e.g. "+2 Punch Through".
                # Its unit comes from the exact recognized trait, not a guess.
                if not pending["unit"] and expected_unit in ("m", "s"):
                    pending["unit"] = expected_unit
                valid_unit = (
                    expected_unit == pending["unit"]
                    or expected_unit == "x"
                    and pending["unit"] == "%"
                    or trait_id in COMBINED_IDS
                    and expected_unit is None
                    and pending["unit"] in ("%", "x", "m", "s", "°", "")
                )
                precision = {"%": 1, "x": 2}.get(pending["unit"])
                decimal = re.search(r"[.,](\d+)$", pending.get("valueText", ""))
                valid_precision = (
                    precision is None or decimal is None or len(decimal[1]) <= precision
                )
                if not valid_unit or not valid_precision:
                    invalid = True
                    unreadable.append(pending["raw"])
                    pending = None
                    return
                if definition:
                    trait_name = definition["name"]
                value = pending["value"]
                negative = (
                    value < 1
                    if pending["unit"] == "x"
                    else pending.get("negative", value < 0)
                )
                if trait_id == "weapon-recoil":
                    negative = not negative
                if trait_id in COMBINED_IDS:
                    negative = False
                stats.append(
                    {
                        "id": trait_id,
                        "name": trait_name,
                        "value": value,
                        "unit": pending["unit"],
                        "polarity": "negative" if negative else "positive",
                        "raw": pending["raw"],
                    }
                )
            pending = None

        for line in candidates:
            text = clean_text(line["text"])
            # A nearby companion's planner can enter the wide recovery crop.
            # Its roll counts are UI labels, not malformed Riven stat lines.
            if re.fullmatch(r"\W*\d[\d,.]*\s+(?:avg\s+)?rolls\W*", text, re.I):
                continue
            if re.match(r"^(?:CONFIRM|CYCLE\s+FOR|Remaining Kuva)", text, re.I):
                finish()
                break
            text_rows.append(line)
            if FOOTER.search(text) or line.get("footerBounds"):
                footer = True
                footer_bounds = line.get("footerBounds") or {
                    key: line[key] for key in ("x", "y", "w", "h")
                }
                lower_limit = min(lower_limit, line["y"] + line["h"] + header["h"])
                finish()
                break
            normalized_percent = normalized_percent or line.get(
                "localizedPercentRepair", False
            )
            normalized_spacing = normalized_spacing or line.get(
                "localizedNumberJoin", False
            )
            # The isolated roll counter is another footer-font anchor when OCR
            # misses the mastery label. It is not completeness evidence alone;
            # rank detection must still verify the entire visible eight-pip row.
            counter = line.get("counterBounds") or line
            if (
                (line.get("counterBounds") or re.fullmatch(r"[0-9]{1,7}", text))
                and len(stats) + (pending is not None) >= 2
                and counter["x"] > center + font_height * 0.7
                and font_height * 0.35 <= counter["h"] <= font_height
                and counter["w"] <= font_height * 5
            ):
                counter_candidates.append(
                    {key: counter[key] for key in ("x", "y", "w", "h")}
                )
                finish()
                continue
            if line.get("localizedUnreadable"):
                finish()
                invalid = True
                unreadable.append(line.get("displayText", text))
                continue
            # Percent glyphs are sometimes decoded as 0/0. Do not alter digits
            # or guess a missing decimal; just restore this recognizable symbol.
            text = re.sub(r"[0Oo]\s*/\s*[0Oo]", "%", text)
            text = re.sub(r"([x×])\s*[lI|]\s*(?=[.,])", r"\g<1>1", text)
            # A bow/heavy-attack multiplier note is not a separate trait value.
            # It can begin after a wrapped label ("Critical" / "Chance (x2 for
            # Heavy" / "Attacks)"). Keep that unfinished note with the pending
            # label rather than treating its x2 as a new faction multiplier.
            note_continuation = pending is not None and bool(
                OPEN_MULTIPLIER_NOTE.search(pending["label"] + " " + text)
            )
            if not note_continuation and re.match(
                r"^\(?\s*(?:[x×]\s*2|2\s*[x×])\s+for\b", text, re.I
            ):
                continue
            text = MULTIPLIER_NOTE.sub("", text).strip()
            match = VALUE.search(text)
            multiplier = None if match or note_continuation else MULTIPLIER.search(text)
            if multiplier:
                finish()
                stat_rows.append(line)
                value_text = multiplier.group("prefix") or multiplier.group("suffix")
                pending = {
                    "value": float(re.sub(r"\s", "", value_text).replace(",", ".")),
                    "valueText": re.sub(r"\s", "", value_text),
                    "unit": "x",
                    "label": text[multiplier.end() :],
                    "raw": line["text"],
                }
                continue
            if match:
                finish()
                stat_rows.append(line)
                groups = match.groupdict()
                value_text = groups["value"].strip()
                if re.search(r"\d\s+\d", value_text) and not re.search(
                    r"[.,]", value_text
                ):
                    invalid = True
                    unreadable.append(line["text"])
                    continue
                value = float(re.sub(r"\s", "", value_text).replace(",", "."))
                if groups.get("sign") in ("-", "−", "–"):
                    value = -value
                unit = (groups.get("unit") or "").lower().replace("×", "x")
                pending = {
                    "value": value,
                    "valueText": groups["sign"] + re.sub(r"\s", "", value_text),
                    "negative": groups["sign"] == "-",
                    "unit": unit,
                    "label": text[match.end() :],
                    "raw": text,
                }
            elif pending is not None and (
                note_continuation
                or identify_trait(pending["label"]) is None
                or identify_trait(pending["label"] + " " + text) is not None
                or any(
                    label.startswith(normalize(pending["label"] + " " + text))
                    for label in TRAITS
                )
            ):
                # A valid short name can also begin a longer wrapped name:
                # "Damage" + "to Orokin" must not silently become plain Damage.
                pending["label"] += " " + text
                pending["raw"] += " " + text
                stat_rows.append(line)
            elif pending is not None and re.search(r"[a-zA-Z]{3}", text):
                invalid = True
                unreadable.append(text)
            elif identify_trait(text) or (
                re.search(r"\d", text) and re.search(r"[a-zA-Z]{3}", text)
            ):
                # Never silently drop an unparsed stat and infer a smaller format.
                invalid = True
                unreadable.append(text)
        finish()
        bottom_anchor = footer_bounds or (
            counter_candidates[0] if len(counter_candidates) == 1 else None
        )
        if bottom_anchor and stat_rows and len(stats) < 4:
            last = max(stat_rows, key=lambda row: row["y"] + row["h"])
            line_height = last.get("glyphHeight", last.get("fontHeight", last["h"]))
            if bottom_anchor["y"] - last["y"] - last["h"] > line_height * 1.7:
                # OCR can omit entire rows under a pointer or icon. A footer
                # does not make a short read complete when text space is lost.
                invalid = True
                unreadable.append("Unreadable text above the footer.")
        positives = sum(stat["polarity"] == "positive" for stat in stats)
        negatives = len(stats) - positives
        # Four recognized traits are unambiguous. Shorter cards must include MR footer,
        # otherwise a missing negative could silently produce the wrong grade multiplier.
        complete = (
            not invalid
            and positives in (2, 3)
            and negatives in (0, 1)
            and len({s["id"] for s in stats}) == len(stats)
            and (len(stats) == 4 or footer)
        )
        text_left = min(row["x"] for row in text_rows)
        text_right = max(row["x"] + row["w"] for row in text_rows)
        cards.append(
            {
                "weapon": name,
                "title": header.get("displayText", header["text"]),
                "stats": stats,
                "format": f"{positives}p{negatives}n",
                "complete": complete,
                "normalizedPercent": normalized_percent,
                "normalizedSpacing": normalized_spacing,
                "unreadable": unreadable,
                "footerBounds": footer_bounds,
                "counterBounds": counter_candidates[0]
                if len(counter_candidates) == 1
                else None,
                "titleBounds": {key: header[key] for key in ("x", "y", "w", "h")},
                "textBounds": {
                    "x": text_left,
                    "y": header["y"],
                    "w": text_right - text_left,
                    "h": lower_limit - header["y"],
                },
                "bounds": {
                    "x": max(0, int(center - half_width)),
                    "y": int(header["y"]),
                    "w": int(half_width * 2),
                    "h": int(lower_limit - header["y"]),
                },
            }
        )
    complete = all(c["complete"] for c in cards)
    problem = next((c for c in cards if not c["complete"]), None)
    detail = (
        f" Cannot read: {problem['unreadable'][0]}"
        if problem and problem["unreadable"]
        else ""
    )
    return {
        "cards": cards,
        "complete": complete,
        "reason": "" if complete else "Card text is incomplete." + detail,
    }


def fingerprint(result):
    return json.dumps(
        [
            (
                c["weapon"],
                [
                    (s["id"], s["value"], s.get("unit", "%"), s["polarity"])
                    for s in c["stats"]
                ],
            )
            for c in result["cards"]
        ],
        separators=(",", ":"),
    )
