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

    def targets(self, index):
        """Ordered alternatives; the first choice remains the saved primary stat."""
        if index == 3:
            primary = self.state["negative"] if self.has_negative else None
        else:
            positives = self.state["positives"]
            primary = positives[index] if index < len(positives) else None
        return [primary, *self.state["statAlternatives"][index]]

    @property
    def positive_options(self):
        rollable = [
            t["id"]
            for t in self.positive_traits()
            if self.is_rollable(t["id"], "positive")
        ]
        return [
            [
                identity
                for identity in rollable
                if not self.conflicts_with_lock(i, identity)
            ]
            if self.targets(i)[0] == "any"
            else self.targets(i)
            for i in range(len(self.state["positives"]))
        ]

    def slot_locked(self, index):
        return (
            self.state["lock"] == "negative"
            if index == 3
            else self.state["lock"].startswith("positive:")
            and self.state["positiveLockSlot"] == index
        )

    def single_target(self, identity, polarity):
        return (
            identity is None
            or identity == "any"
            or identity in SPLICE_IDS
            or self.is_vintage(identity, polarity)
        )

    def conflicts_with_lock(self, index, identity):
        target = self.lock_target
        return bool(target and target["id"] == identity and not self.slot_locked(index))

    def set_targets(self, index, choices):
        """Apply a slot selection, including format changes and retained locks."""
        choices = list(dict.fromkeys(choices))
        if not choices:
            return
        polarity = "negative" if index == 3 else "positive"
        if any(
            self.conflicts_with_lock(index, i)
            or (
                not (i == "any" or i is None and index == 2)
                and not self.is_selectable(i, polarity)
            )
            for i in choices
        ):
            return
        identity = choices[0]
        locked = self.slot_locked(index)
        if locked or any(self.single_target(i, polarity) for i in choices):
            choices = choices[:1]
        if index == 3:
            self.state["negative"] = identity
        elif index == 2:
            self.state["positives"] = self.state["positives"][:2]
            if identity is not None:
                self.state["positives"].append(identity)
            self.state["format"] = (
                f"{len(self.state['positives'])}p{int(self.has_negative)}n"
            )
        else:
            self.state["positives"][index] = identity
        self.state["statAlternatives"][index] = choices[1:]
        if locked or self.is_vintage(identity, polarity):
            self.state["lock"] = (
                "negative" if index == 3 else "positive:" + str(identity)
            )
            self.state["positiveLockSlot"] = index if index < 3 else -1
        self.normalize(auto_lock=True)

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
        if identity == "any":
            return "Any"
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
        ]

    def normalize(self, auto_lock=False):
        s = self.state
        s["weapon"], s["variant"] = self.weapon["id"], self.variant["id"]
        if s.get("format") not in FORMATS:
            s["format"] = "3p1n"
        alternatives = s.get("statAlternatives", [])
        s["statAlternatives"] = [
            list(dict.fromkeys(row)) if isinstance(row, list) else []
            for row in (alternatives + [[], [], [], []])[:4]
        ]
        eligible = self.positive_traits()
        eligible_ids = {t["id"] for t in eligible}
        selected = []
        for index in range(FORMATS[s["format"]][0]):
            current = s.get("positives", [])
            identity = current[index] if index < len(current) else None
            if (identity != "any" and identity not in eligible_ids) or (
                identity in SPLICE_IDS and any(i in SPLICE_IDS for i in selected)
            ):
                identity = next(
                    (
                        i
                        for i in s["statAlternatives"][index]
                        if i in eligible_ids and not self.single_target(i, "positive")
                    ),
                    None,
                )
                if identity is None:
                    identity = next(
                        t["id"]
                        for t in eligible
                        if t["id"] not in selected
                        and t["id"] not in current
                        and self.is_rollable(t["id"], "positive")
                    )
            selected.append(identity)
        s["positives"] = selected
        negatives = self.negative_traits()
        if s.get("negative") != "any" and s.get("negative") not in [
            t["id"] for t in negatives
        ]:
            s["negative"] = next(
                (
                    i
                    for i in s["statAlternatives"][3]
                    if i in {t["id"] for t in negatives}
                    and not self.single_target(i, "negative")
                ),
                None,
            )
            if s["negative"] is None:
                s["negative"] = next(
                    (
                        t["id"]
                        for t in negatives
                        if self.is_rollable(t["id"], "negative")
                    ),
                    "any",
                )
        lock = s.get("lock", "none")
        if (
            lock == "negative" and (not self.has_negative or s["negative"] == "any")
        ) or (
            lock.startswith("positive:")
            and (
                lock[9:] not in selected or lock[9:] in SPLICE_IDS or lock[9:] == "any"
            )
        ):
            s["lock"] = "none"
        if auto_lock:
            required = ["positive:" + i for i in self.retained_positives]
            if self.retained_negatives:
                required.append("negative")
            if required and s["lock"] not in required:
                s["lock"] = required[0]
        slot = s.get("positiveLockSlot", -1)
        if s["lock"].startswith("positive:"):
            identity = s["lock"][9:]
            if slot not in range(len(selected)) or selected[slot] != identity:
                slot = selected.index(identity)
        else:
            slot = -1
        s["positiveLockSlot"] = slot
        for index in range(4):
            identity = self.targets(index)[0]
            polarity = "negative" if index == 3 else "positive"
            s["statAlternatives"][index] = [
                i
                for i in s["statAlternatives"][index]
                if not self.slot_locked(index)
                and not self.single_target(identity, polarity)
                and i != identity
                and isinstance(i, str)
                and not self.single_target(i, polarity)
                and self.is_selectable(i, polarity)
            ]
        for index in range(4):
            choices = [
                i for i in self.targets(index) if not self.conflicts_with_lock(index, i)
            ] or ["any"]
            if index == 3 and self.has_negative:
                s["negative"] = choices[0]
            elif index < len(s["positives"]):
                s["positives"][index] = choices[0]
            s["statAlternatives"][index] = choices[1:]

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
                and not self.conflicts_with_lock(3, t["id"])
            ]
            if s["negative"] == "any"
            else self.targets(3)
        )
        positive_options = self.positive_options
        target = {
            "positives": s["positives"],
            "positiveOptions": positive_options,
            "negatives": negatives,
            "hasNegative": self.has_negative,
            "retainedPositives": self.retained_positives,
            "retainedNegatives": self.retained_negatives,
            "heldNegative": next(iter(negatives), None),
            "heldPositive": s["lock"][9:]
            if s["lock"].startswith("positive:")
            else next(iter(self.retained_positives), None)
            or next(
                (i for i in s["positives"] if i not in SPLICE_IDS and i != "any"), None
            )
            or next(
                (i for row in positive_options for i in row if i not in SPLICE_IDS),
                None,
            ),
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
