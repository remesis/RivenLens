# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See docs/LICENSE.txt for the license and warranty disclaimer.

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
                    "—": "-",
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
            if re.fullmatch(r"[A-Za-z][A-Za-z\-\s\u2010-\u2014]{2,}", suffix):
                return canonical
    return None


@lru_cache(maxsize=2048)
def identify_trait(text):
    text = clean_text(text)
    text = re.sub(r"\(\s*(?:[x×]2|2[x×])\s+for[^)]*\)", "", text, flags=re.I)
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


def card_headers(lines):
    headers = []
    for line in lines:
        name = weapon_title(line["text"])
        if name:
            headers.append((line, name))
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
            right = max(joined["x"] + joined["w"], following["x"] + following["w"])
            joined = {
                **joined,
                "text": joined["text"] + " " + following["text"],
                "x": min(joined["x"], following["x"]),
                "h": following["y"] + following["h"] - joined["y"],
            }
            joined["w"] = right - joined["x"]
            name = weapon_title(joined["text"])
            if name:
                headers.append((joined, name))
                break
            last = following
    return headers


def parse_cards(lines, width, height):
    headers = card_headers(lines)
    # If the companion was left on the captured monitor, do not re-read its
    # own displayed card titles as additional game cards (pixels only).
    companion_labels = [
        line
        for line in lines
        if re.search(r"\b(?:CURRENT|NEW)\s+ROLL.*\b[23]p[01]n\b", line["text"], re.I)
    ]
    headers = [
        (header, name)
        for header, name in headers
        if not any(
            0 < header["y"] - label["y"] < max(100, header["h"] * 5)
            and abs((header["x"] + header["w"] / 2) - (label["x"] + label["w"] / 2))
            < max(header["w"], label["w"])
            for label in companion_labels
        )
    ]
    headers.sort(key=lambda entry: entry[0]["x"])
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
            ],
            key=lambda line: line["y"],
        )
        stats, pending, footer, invalid = [], None, False, False
        footer_bounds = None
        unreadable = []

        def finish():
            nonlocal pending, invalid
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
                if not valid_unit:
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
            if re.match(r"^(?:CONFIRM|CYCLE\s+FOR|Remaining Kuva)", text, re.I):
                finish()
                break
            if FOOTER.search(text):
                footer = True
                footer_bounds = line.get("footerBounds") or {
                    key: line[key] for key in ("x", "y", "w", "h")
                }
                lower_limit = min(lower_limit, line["y"] + line["h"] + header["h"])
                finish()
                break
            # Percent glyphs are sometimes decoded as 0/0. Do not alter digits
            # or guess a missing decimal; just restore this recognizable symbol.
            text = re.sub(r"[0Oo]\s*/\s*[0Oo]", "%", text)
            text = re.sub(r"([x×])\s*[lI|]\s*(?=[.,])", r"\g<1>1", text)
            # A bow/heavy-attack multiplier note is not a separate trait value.
            if re.match(r"^\(?\s*(?:[x×]\s*2|2\s*[x×])\s+for\b", text, re.I):
                continue
            text = re.sub(
                r"\(\s*(?:[x×]2|2[x×])\s+for[^)]*\)", "", text, flags=re.I
            ).strip()
            match = VALUE.search(text)
            multiplier = None if match else MULTIPLIER.search(text)
            if multiplier:
                finish()
                value_text = multiplier.group("prefix") or multiplier.group("suffix")
                pending = {
                    "value": float(re.sub(r"\s", "", value_text).replace(",", ".")),
                    "unit": "x",
                    "label": text[multiplier.end() :],
                    "raw": line["text"],
                }
                continue
            if match:
                finish()
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
                    "negative": groups["sign"] == "-",
                    "unit": unit,
                    "label": text[match.end() :],
                    "raw": text,
                }
            elif pending is not None and (
                identify_trait(pending["label"]) is None
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
            elif pending is not None and re.search(r"[a-zA-Z]{3}", text):
                invalid = True
                unreadable.append(text)
            elif re.search(r"\d", text) and re.search(r"[a-zA-Z]{3}", text):
                # Never silently drop an unparsed stat and infer a smaller format.
                invalid = True
                unreadable.append(text)
        finish()
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
        cards.append(
            {
                "weapon": name,
                "title": header["text"],
                "stats": stats,
                "format": f"{positives}p{negatives}n",
                "complete": complete,
                "unreadable": unreadable,
                "footerBounds": footer_bounds,
                "titleBounds": {key: header[key] for key in ("x", "y", "w", "h")},
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
