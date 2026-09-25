# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See docs/LICENSE.txt for the license and warranty disclaimer.

"""Pure grade matching and bounded, in-memory sound deduplication."""

import json
from collections import OrderedDict

from catalog import recipes
from grading import GRADE_NAMES, grade_stat, grading_rank


def meets_grade(result, minimum):
    grade = result.get("grade")
    return (
        grade in GRADE_NAMES
        and minimum in GRADE_NAMES
        and GRADE_NAMES.index(grade) <= GRADE_NAMES.index(minimum)
    )


def find_matches(cards, catalog, preferences):
    weapon = catalog.by_id.get(preferences["weapon"])
    if not weapon:
        return []
    watched = {
        i
        for identity in preferences["spliceWatch"]
        for pair in recipes(identity, weapon["kind"])
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
    matches = []
    for card in cards:
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
        for stat in card["stats"]:
            grade = grade_stat(
                stat,
                catalog.trait(variant, stat["id"]),
                variant["disposition"],
                card["format"],
                catalog.range_model,
                rank,
            )
            if (
                preferences["spliceSound"]
                and stat["polarity"] == "positive"
                and stat["id"] in watched
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
