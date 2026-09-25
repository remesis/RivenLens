# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See docs/LICENSE.txt for the license and warranty disclaimer.

"""Local catalog expansion and conservative grading-variant resolution."""

import json
import re
import unicodedata
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
REFERENCE = json.loads((DATA_DIR / "reference.json").read_text(encoding="utf-8"))
SPLICES = REFERENCE["splices"]
SPLICE_IDS = frozenset(row["id"] for row in SPLICES)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def name_key(text):
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[\u2010\u2011\u2013\u2014]", "-", text)
    return " ".join(text.split()).lower()


def variant_label(variant):
    """Keep full weapon identities for grading and concise base labels for menus."""
    return "Base" if variant["label"] == "Base" else variant["name"]


def recipes(identity, kind):
    return REFERENCE["recipes"].get(kind, {}).get(identity, [])


def spliced_trait(identity, kind):
    return REFERENCE["baselines"].get(kind, {}).get(identity)


def splices_for(kind):
    return [row for row in SPLICES if spliced_trait(row["id"], kind)]


class Catalog:
    def __init__(self):
        self.data = json.loads((DATA_DIR / "catalog.json").read_text(encoding="utf-8"))
        if self.data["schemaVersion"] != 2:
            raise ValueError("Unsupported Riven catalog")
        self.definitions = self.data["definitions"]
        self.range_model = self.data["rangeModel"]
        self.assumptions = self.data["assumptions"]
        self.families = []
        states = ("excluded", "allowed", "unresolved")
        for name, kind, profile in self.data["families"]:
            codes = self.data["pools"][profile]
            if len(codes) != len(self.definitions[kind]):
                raise ValueError("Invalid trait pool")
            traits = {}
            for trait, code in zip(self.definitions[kind], codes):
                number = int(code, 18)
                traits[trait["id"]] = {
                    "positive": states[number % 3],
                    "negative": states[number // 3 % 3],
                    "vintage": number >= 9,
                }
            self.families.append(
                {
                    "id": f"{slug(name)}-{kind.lower()}",
                    "name": name,
                    "kind": kind,
                    "traits": traits,
                }
            )
        self.weapons = []
        for index, category, variants in self.data["weapons"]:
            family = self.families[index]
            self.weapons.append(
                {
                    "id": f"{family['id']}-{category.lower()}",
                    "name": family["name"],
                    "category": category,
                    "family": family,
                    "kind": family["kind"],
                    "variants": [
                        {
                            "id": slug(n),
                            "name": n,
                            "label": label,
                            "disposition": dispo,
                            "kind": family["kind"],
                        }
                        for n, label, dispo in variants
                    ],
                }
            )
        self.by_id = {w["id"]: w for w in self.weapons}
        self.groups = {}
        self.by_name = {}
        for weapon in self.weapons:
            plain = re.sub(
                r"\s*\((?:Primary|Secondary|Rifle|Melee)\)$",
                "",
                weapon["name"],
                flags=re.I,
            )
            group = self.groups.setdefault(
                plain,
                {
                    "id": weapon["id"],
                    "name": plain,
                    "memberIds": [],
                    "variants": [],
                },
            )
            group["memberIds"].append(weapon["id"])
            self.by_name[name_key(plain)] = group
            self.by_name[name_key(weapon["name"])] = group
            for variant in weapon["variants"]:
                self.by_name[name_key(variant["name"])] = group
                if not any(v["id"] == variant["id"] for v in group["variants"]):
                    group["variants"].append(variant)

    def group(self, card):
        return self.by_name.get(name_key(card.get("weapon")))

    def variant(self, card, saved=None):
        group = self.group(card)
        if not group:
            return None
        variants = group["variants"]
        hint = next(
            (
                v
                for v in variants
                if name_key(v["name"]) == name_key(card.get("variantHint"))
            ),
            None,
        )
        if hint:
            return {**hint, "source": "fits-in"}
        saved = saved or {}
        for identity in [group["id"], *group["memberIds"]]:
            selected = next(
                (v for v in variants if v["id"] == saved.get(identity)), None
            )
            if selected:
                return {**selected, "source": "manual"}
        if len(variants) == 1:
            return {**variants[0], "source": "only"}
        exact = next(
            (
                v
                for v in variants
                if name_key(v["name"]) == name_key(card.get("weapon"))
            ),
            None,
        )
        if exact and name_key(card.get("weapon")) != name_key(group["name"]):
            return {**exact, "source": "title"}
        return None

    def trait(self, variant, identity):
        if not variant:
            return None
        kind = variant["kind"]
        if identity in SPLICE_IDS:
            return spliced_trait(identity, kind)
        if identity == "damage" and kind in ("Melee", "Zaw"):
            identity = "melee-damage"
        return next((t for t in self.definitions[kind] if t["id"] == identity), None)
