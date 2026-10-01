# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Public listing adapters and stage-specific seed matching, independent of Qt."""

import calendar
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from catalog import SPLICES, recipes
from grading import FORMATS, GRADE_NAMES, grade_stat

AGE_CHOICES = (
    ("Less than 15 days", "15d"),
    ("Less than 30 days", "30d"),
    ("Less than 3 months", "3m"),
    ("Less than 6 months", "6m"),
    ("Show All", "all"),
)
ATTRIBUTE_IDS = {
    "base_damage_/_melee_damage": "damage",
    "puncture_damage": "puncture",
    "impact_damage": "impact",
    "slash_damage": "slash",
    "electric_damage": "electricity",
    "heat_damage": "heat",
    "cold_damage": "cold",
    "toxin_damage": "toxin",
    "recoil": "weapon-recoil",
    "fire_rate_/_attack_speed": "fire-rate-attack-speed",
    "damage_vs_corpus": "damage-to-corpus",
    "damage_vs_grineer": "damage-to-grineer",
    "damage_vs_infested": "damage-to-infested",
    "channeling_damage": "initial-combo",
    "channeling_efficiency": "heavy-attack-efficiency",
    "critical_chance_on_slide_attack": "critical-chance-for-slide-attack",
    "chance_to_gain_extra_combo_count": "additional-combo-count-chance",
}
API_ROOT = "https://api.warframe.market/"
CROSSPLAY_PLATFORMS = ("ps4", "xbox", "mobile")


def valid_slug(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[a-z0-9_/-]{1,100}", value))


def weapon_slug(name):
    name = re.sub(r"\s*\((?:Primary|Secondary|Rifle|Melee)\)$", "", name)
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def attribute_map(rows, definitions):
    """Only accept advertised Market slugs; new splice names can be recognized."""
    names = {row["name"].casefold(): row["id"] for row in [*definitions, *SPLICES]}
    known = {row["id"] for row in definitions}
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not valid_slug(row.get("slug")):
            continue
        slug = row["slug"]
        identity = ATTRIBUTE_IDS.get(slug, slug.replace("_", "-"))
        if identity == "damage" and "melee-damage" in known:
            identity = "melee-damage"
        if identity not in known:
            localized = (row.get("i18n") or {}).get("en") or {}
            identity = names.get(str(localized.get("name", "")).casefold())
        if identity:
            result[slug] = identity
    return result


def search_path(weapon, attribute, polarity):
    if not valid_slug(weapon) or not valid_slug(attribute):
        raise ValueError("Invalid Market search identity")
    if polarity not in ("positive", "negative"):
        raise ValueError("Invalid Market search polarity")
    return "v1/auctions/search?" + urlencode(
        {
            "type": "riven",
            "weapon_url_name": weapon,
            polarity + "_stats": attribute,
            "sort_by": "price_asc",
        }
    )


def age_cutoff(choice, now):
    if choice == "all":
        return None
    if choice in ("15d", "30d"):
        return now - timedelta(days=int(choice[:-1]))
    if choice not in ("3m", "6m"):
        raise ValueError("Invalid listing age")
    month = now.year * 12 + now.month - 1 - int(choice[:-1])
    year, month = divmod(month, 12)
    return now.replace(
        year=year,
        month=month + 1,
        day=min(now.day, calendar.monthrange(year, month + 1)[1]),
    )


def listing_price(row):
    prices = [row.get("starting_price"), row.get("buyout_price")]
    prices = [p for p in prices if type(p) is int and 0 <= p <= 2**31 - 1]
    return min(prices) if prices else None


def active_listing(row, weapon, cutoff, now):
    if not isinstance(row, dict):
        return False
    identity = row.get("id")
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{24}", identity):
        return False
    item = row.get("item")
    if not isinstance(item, dict) or item.get("type") != "riven":
        return False
    if row.get("platform") != "pc":
        owner = row.get("owner")
        if (
            row.get("platform") not in CROSSPLAY_PLATFORMS
            or row.get("crossplay") is not True
            or not isinstance(owner, dict)
            or owner.get("crossplay") is not True
        ):
            return False
    if (
        item.get("weapon_url_name") != weapon
        or row.get("closed") is not False
        or row.get("visible") is not True
        or row.get("private") is not False
        or listing_price(row) is None
    ):
        return False
    try:
        created = datetime.fromisoformat(row["created"].replace("Z", "+00:00"))
        return (
            created.tzinfo is not None
            and created <= now
            and (cutoff is None or created > cutoff)
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


class SeedMatcher:
    """Freeze the planner at opening; do not infer unadvertised stats or ranks."""

    def __init__(self, planner, mapping, disposition):
        self.model = planner
        self.mapping = mapping
        self.disposition = disposition
        self.weapon = weapon_slug(planner.weapon["name"])
        self.pairs = (
            recipes(planner.splice, planner.weapon["kind"]) if planner.splice else []
        )
        self.ingredients = {identity for pair in self.pairs for identity in pair}

    def searches(self):
        desired = {(i, "positive") for i in self.ingredients}
        if self.model.splice:
            desired.add((self.model.splice, "positive"))
        if self.model.lock_target:
            target = self.model.lock_target
            desired.add((target["id"], target["polarity"]))
        return sorted(
            {
                search_path(self.weapon, slug, polarity)
                for slug, identity in self.mapping.items()
                for identity_wanted, polarity in desired
                if identity == identity_wanted
            }
        )

    def grades(self, item):
        attributes = item.get("attributes")
        rank = item.get("mod_rank")
        if (
            not isinstance(attributes, list)
            or not 2 <= len(attributes) <= 4
            or type(rank) is not int
            or not 0 <= rank <= 8
            or any(
                not isinstance(a, dict) or type(a.get("positive")) is not bool
                for a in attributes
            )
        ):
            return None
        positive = sum(a["positive"] for a in attributes)
        negative = len(attributes) - positive
        fmt = f"{positive}p{negative}n"
        if fmt not in FORMATS or fmt != self.model.state["format"]:
            return None
        values = {}
        seen = set()
        for attr in attributes:
            slug = attr.get("url_name")
            if not isinstance(slug, str) or slug in seen:
                return None
            seen.add(slug)
            identity = self.mapping.get(slug)
            if identity is None:
                continue
            trait = self.model.catalog.trait(self.model.variant, identity)
            if not trait:
                return None
            value = attr.get("value")
            if type(value) not in (int, float) or not math.isfinite(value):
                return None
            if identity in values:
                return None
            polarity = "positive" if attr["positive"] else "negative"
            stat = {
                "id": identity,
                "value": value,
                "unit": trait["unit"],
                "polarity": polarity,
            }
            result = grade_stat(
                stat,
                trait,
                self.disposition,
                fmt,
                self.model.catalog.range_model,
                rank,
            )
            if result.get("invalid") or result.get("unknown"):
                return None
            values[identity] = {**result, "polarity": polarity}
        return values

    @staticmethod
    def qualifies(values, identity, polarity, minimum):
        stat = values.get(identity)
        return bool(
            stat
            and stat["polarity"] == polarity
            and GRADE_NAMES.index(stat["grade"]) <= GRADE_NAMES.index(minimum)
        )

    def summary_stat(self, values, identity):
        return {
            "name": self.model.name(identity),
            "grade": values[identity]["grade"],
            "polarity": values[identity]["polarity"],
        }

    def sections(self, listings, age="30d", now=None):
        now = now or datetime.now(timezone.utc)
        cutoff = age_cutoff(age, now)
        splice, lock = self.model.splice, self.model.lock_target
        output = [[] for _ in range(2 if splice and lock else 1)]
        seen = set()
        for listing in listings:
            if (
                not active_listing(listing, self.weapon, cutoff, now)
                or listing["id"] in seen
            ):
                continue
            seen.add(listing["id"])
            values = self.grades(listing["item"])
            if values is None:
                continue
            splice_grade, lock_grade = (
                self.model.state["spliceGrade"],
                self.model.state["lockGrade"],
            )
            qualifying = (
                [
                    i
                    for i in sorted(self.ingredients | {splice})
                    if i and self.qualifies(values, i, "positive", splice_grade)
                ]
                if splice
                else []
            )
            ready = bool(
                splice
                and (
                    self.qualifies(values, splice, "positive", splice_grade)
                    or any(
                        all(i in values for i in pair)
                        and any(
                            self.qualifies(values, i, "positive", splice_grade)
                            for i in pair
                        )
                        for pair in self.pairs
                    )
                )
            )
            locked = bool(
                lock
                and self.qualifies(values, lock["id"], lock["polarity"], lock_grade)
            )

            summary = (
                [
                    self.summary_stat(values, i)
                    for i in sorted(
                        (i for i in self.ingredients | {splice} if i in values),
                        key=lambda i: (GRADE_NAMES.index(values[i]["grade"]), i),
                    )
                ]
                if splice
                else []
            )
            entry = {
                "id": listing["id"],
                "price": listing_price(listing),
                "format": self.model.state["format"],
                "stats": summary,
            }
            if locked and (not splice or ready):
                output[-1].append(
                    {
                        **entry,
                        "stats": [
                            self.summary_stat(values, lock["id"]),
                            *(
                                s
                                for s in summary
                                if s["name"] != self.model.name(lock["id"])
                            ),
                        ],
                    }
                )
            elif splice and qualifying:
                output[0].append(entry)
        for section in output:
            section.sort(key=lambda row: (row["price"], row["id"]))
        return output
