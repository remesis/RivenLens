# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Planner selection rules and presentation-independent result snapshots."""

from functools import lru_cache

from catalog import SPLICE_IDS, SPLICES, recipes, splices_for
from grading import FORMATS, format_range, grade_chance, grade_range, trait_range
from odds import (
    bounds,
    enumerate_pools,
    evaluate,
    optimal_splice_setup,
    selected_lock_chance,
)


class Planner:
    def __init__(self, catalog, state):
        self.catalog, self.state = catalog, state
        self.normalize()

    @property
    def weapon(self):
        return self.catalog.by_id.get(self.state.get("weapon")) or next(
            w for w in self.catalog.weapons if w["name"] == "Sobek"
        )

    @property
    def variant(self):
        return next(
            (
                v
                for v in self.weapon["variants"]
                if v["id"] == self.state.get("variant")
            ),
            self.weapon["variants"][0],
        )

    @property
    def family(self):
        return self.weapon["family"]

    @property
    def definitions(self):
        return self.catalog.definitions[self.weapon["kind"]]

    @property
    def splice(self):
        return next((i for i in self.state["positives"] if i in SPLICE_IDS), None)

    @property
    def has_negative(self):
        return FORMATS[self.state["format"]][1]

    @property
    def lock_target(self):
        lock = self.state.get("lock", "none")
        if lock == "negative":
            return {"id": self.state["negative"], "polarity": "negative"}
        if lock.startswith("positive:"):
            return {"id": lock[9:], "polarity": "positive"}
        return None

    def is_vintage(self, identity, polarity):
        entry = self.family["traits"].get(identity, {})
        return bool(entry.get("vintage") and entry.get(polarity) == "excluded")

    def is_rollable(self, identity, polarity):
        return (
            self.family["traits"].get(identity, {}).get(polarity, "excluded")
            != "excluded"
        )

    def is_selectable(self, identity, polarity):
        if identity in SPLICE_IDS:
            return polarity == "positive" and any(
                t["id"] == identity for t in splices_for(self.weapon["kind"])
            )
        trait = self.catalog.trait(self.variant, identity)
        return bool(
            trait
            and trait[polarity]
            and (
                self.is_rollable(identity, polarity)
                or self.is_vintage(identity, polarity)
            )
        )

    @property
    def retained_positives(self):
        return [i for i in self.state["positives"] if self.is_vintage(i, "positive")]

    @property
    def retained_negatives(self):
        identity = self.state["negative"]
        return (
            [identity]
            if self.has_negative and self.is_vintage(identity, "negative")
            else []
        )

    def name(self, identity):
        return next(
            (r["name"] for r in [*self.definitions, *SPLICES] if r["id"] == identity),
            identity,
        )

    @property
    def rank(self):
        return self.state["rank"] if self.state["rankMode"] == "manual" else 8

    def stat_range(self, identity, polarity):
        return trait_range(
            self.catalog.trait(self.variant, identity),
            self.variant["disposition"],
            self.state["format"],
            polarity,
            self.catalog.range_model,
            self.rank,
        )

    def recipe_text(self, identity):
        pairs = recipes(identity, self.weapon["kind"])

        def eligible(identity):
            return any(
                self.family["traits"].get(identity, {}).get(p, "excluded") != "excluded"
                for p in ("positive", "negative")
            )

        return " or ".join(
            " + ".join(self.name(i) for i in pair)
            for pair in pairs
            if all(eligible(i) for i in pair)
        )

    def positive_traits(self, include_unrollable=False):
        return [
            t
            for t in self.definitions
            if t["positive"]
            and (include_unrollable or self.is_selectable(t["id"], "positive"))
        ] + splices_for(self.weapon["kind"])

    def negative_traits(self, include_unrollable=False):
        return [
            t
            for t in self.definitions
            if t["negative"]
            and (include_unrollable or self.is_selectable(t["id"], "negative"))
            and t["id"] not in self.state["positives"]
        ]

    def normalize(self, auto_lock=False):
        s = self.state
        s["weapon"], s["variant"] = self.weapon["id"], self.variant["id"]
        if s.get("format") not in FORMATS:
            s["format"] = "3p1n"
        eligible = self.positive_traits()
        selected = []
        for index in range(FORMATS[s["format"]][0]):
            current = s.get("positives", [])
            identity = current[index] if index < len(current) else None
            if (
                identity not in [t["id"] for t in eligible]
                or identity in selected
                or (identity in SPLICE_IDS and any(i in SPLICE_IDS for i in selected))
            ):
                identity = next(
                    t["id"]
                    for t in eligible
                    if t["id"] not in selected and self.is_rollable(t["id"], "positive")
                )
            selected.append(identity)
        s["positives"] = selected
        negatives = self.negative_traits()
        if s.get("negative") != "any" and s.get("negative") not in [
            t["id"] for t in negatives
        ]:
            s["negative"] = next(
                (t["id"] for t in negatives if self.is_rollable(t["id"], "negative")),
                "any",
            )
        lock = s.get("lock", "none")
        if (
            lock == "negative" and (not self.has_negative or s["negative"] == "any")
        ) or (
            lock.startswith("positive:")
            and (lock[9:] not in selected or lock[9:] in SPLICE_IDS)
        ):
            s["lock"] = "none"
        if auto_lock:
            required = ["positive:" + i for i in self.retained_positives]
            if self.retained_negatives:
                required.append("negative")
            if required and s["lock"] not in required:
                s["lock"] = required[0]

    @lru_cache(maxsize=64)
    def setup(self, family_id, kind, identity, fmt, grade):
        family = next(f for f in self.catalog.families if f["id"] == family_id)
        return optimal_splice_setup(
            enumerate_pools(family),
            recipes(identity, kind),
            *FORMATS[fmt],
            grade_chance(grade),
        )

    def calculate(self):
        s = self.state
        pools = enumerate_pools(self.family)
        result = {"splice": None, "lock": None, "uncertain": len(pools) > 1}
        if self.splice:
            result["splice"] = self.setup(
                self.family["id"],
                self.weapon["kind"],
                self.splice,
                s["format"],
                s["spliceGrade"],
            )
        if self.lock_target:
            target = {
                **self.lock_target,
                "positives": len(s["positives"]),
                "hasNegative": self.has_negative,
                "hasSplice": bool(self.splice),
            }
            probability = bounds(
                selected_lock_chance(pool, target, self.catalog.assumptions)
                * grade_chance(s["lockGrade"])
                for pool in pools
            )
            trait = next(t for t in self.definitions if t["id"] == target["id"])
            stat_range = grade_range(
                trait,
                self.variant["disposition"],
                s["format"],
                target["polarity"],
                self.catalog.range_model,
                s["lockGrade"],
                self.rank,
            )
            result["lock"] = {
                "probability": probability,
                "range": format_range(stat_range),
                "vintage": self.is_vintage(target["id"], target["polarity"]),
            }
        negatives = (
            [
                t["id"]
                for t in self.negative_traits()
                if self.is_rollable(t["id"], "negative")
            ]
            if s["negative"] == "any"
            else [s["negative"]]
        )
        target = {
            "positives": s["positives"],
            "negatives": negatives,
            "hasNegative": self.has_negative,
            "retainedPositives": self.retained_positives,
            "retainedNegatives": self.retained_negatives,
            "heldNegative": next(iter(negatives), None),
            "heldPositive": s["lock"][9:]
            if s["lock"].startswith("positive:")
            else next(iter(self.retained_positives), None)
            or next((i for i in s["positives"] if i not in SPLICE_IDS), None),
        }
        rows = [evaluate(pool, target, self.catalog.assumptions) for pool in pools]
        result["final"] = {
            strategy: bounds(row[field] for row in rows)
            for strategy, field in (
                ("none", "q0"),
                ("positive", "qPositive"),
                ("negative", "qNegative"),
            )
        }
        result["strategy"] = (
            "positive" if s["lock"].startswith("positive:") else s["lock"]
        )
        return result
