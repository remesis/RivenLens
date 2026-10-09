# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Pure grade matching and bounded, in-memory sound deduplication."""

import json
from collections import OrderedDict

from catalog import recipes
from grading import GRADE_NAMES, grade_stat, grading_rank
from odds import matches_targets
from planner import Planner


def meets_grade(result, minimum):
    grade = result.get("grade")
    return (
        grade in GRADE_NAMES
        and minimum in GRADE_NAMES
        and GRADE_NAMES.index(grade) <= GRADE_NAMES.index(minimum)
    )


def final_target_matches(card, grades, planner):
    """A whole, graded roll must satisfy the target and retained grade thresholds."""
    state = planner.state
    stats = card["stats"]
    if (
        card.get("complete") is not True
        or card["format"] != state["format"]
        or len({s["id"] for s in stats}) != len(stats)
        or any(g.get("grade") not in GRADE_NAMES for g in grades)
        or any(s["polarity"] not in ("positive", "negative") for s in stats)
    ):
        return False
    positives = [s["id"] for s in stats if s["polarity"] == "positive"]
    negatives = [s["id"] for s in stats if s["polarity"] == "negative"]
    if not matches_targets(positives, planner.positive_options):
        return False
    if len(negatives) != int(planner.has_negative):
        return False
    if negatives and not (
        planner.is_rollable(negatives[0], "negative")
        if state["negative"] == "any"
        else negatives[0] in planner.targets(3)
    ):
        return False
    for stat, grade in zip(stats, grades):
        if planner.lock_target == {"id": stat["id"], "polarity": stat["polarity"]}:
            if not meets_grade(grade, state["lockGrade"]):
                return False
        if stat["id"] == planner.splice and not meets_grade(
            grade, state["spliceGrade"]
        ):
            return False
    return True


def find_matches(cards, catalog, preferences):
    weapon = catalog.by_id.get(preferences["weapon"])
    if not weapon:
        return []
    watched_pairs = [
        pair
        for identity in preferences["spliceWatch"]
        for pair in recipes(identity, weapon["kind"])
    ]
    watched = {
        i
        for pair in watched_pairs
        for i in pair
        if weapon["family"]["traits"].get(i, {}).get("positive") == "allowed"
    }
    lock = preferences["lock"]
    lock_identity, polarity = (
        (preferences["negative"], "negative")
        if lock == "negative"
        else (lock[9:], "positive")
        if lock.startswith("positive:")
        else (None, None)
    )
    lock_rollable = (
        weapon["family"]["traits"].get(lock_identity, {}).get(polarity, "excluded")
        != "excluded"
    )
    vintage_lock = not lock_rollable and weapon["family"]["traits"].get(
        lock_identity, {}
    ).get("vintage", False)
    matches = []
    planner = (
        Planner(catalog, dict(preferences)) if preferences.get("finalSound") else None
    )
    for index, card in enumerate(cards):
        if not card or card.get("snapshot") or card.get("complete") is False:
            continue
        rank = grading_rank(card, preferences)
        group = catalog.group(card)
        variant = catalog.variant(card, preferences["gradeVariants"])
        if (
            rank is None
            or not variant
            or variant["id"] != preferences["variant"]
            or weapon["id"] not in group["memberIds"]
        ):
            continue
        if variant["source"] == "title" and len(group["variants"]) > 1:
            continue
        identity = json.dumps(
            [
                weapon["id"],
                variant["id"],
                card["format"],
                rank,
                sorted(
                    (s["id"], s["value"], s["unit"], s["polarity"])
                    for s in card["stats"]
                ),
            ]
        )
        grades = []
        ready_ingredients = set()
        if (
            vintage_lock
            and card["format"] == preferences["format"]
            and any(
                s["id"] == lock_identity and s["polarity"] == polarity
                for s in card["stats"]
            )
        ):
            present = {s["id"] for s in card["stats"]}
            ready_ingredients = {
                i
                for pair in watched_pairs
                if lock_identity not in pair and set(pair).issubset(present)
                for i in pair
            }
        for stat in card["stats"]:
            grade = grade_stat(
                stat,
                catalog.trait(variant, stat["id"]),
                variant["disposition"],
                card["format"],
                catalog.range_model,
                rank,
            )
            grades.append(grade)
            if (
                preferences["spliceSound"]
                and stat["polarity"] == "positive"
                and stat["id"] in watched
                and (not vintage_lock or stat["id"] in ready_ingredients)
                and meets_grade(grade, preferences["spliceGrade"])
            ):
                matches.append(("splice", identity))
            if (
                preferences["lockSound"]
                and lock_rollable
                and card["format"] == preferences["format"]
                and stat["id"] == lock_identity
                and stat["polarity"] == polarity
                and meets_grade(grade, preferences["lockGrade"])
            ):
                matches.append(("lock", identity))
        if (
            planner
            and card.get("slot", "current" if index == 0 else "new") == "new"
            and final_target_matches(card, grades, planner)
        ):
            matches.append(("final", identity))
    return matches


class History:
    def __init__(self):
        self.seen = OrderedDict()

    def take(self, keys):
        fresh = []
        for key in keys:
            if key not in self.seen:
                self.seen[key] = None
                fresh.append(key)
        while len(self.seen) > 256:
            self.seen.popitem(last=False)
        return fresh
