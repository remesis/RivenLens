# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Exact positives-first probabilities, including splice setup routes."""

import math
from functools import lru_cache
from itertools import combinations, permutations, product

from catalog import SPLICE_IDS


def choose(n, k):
    return math.comb(n, k) if 0 <= k <= n else 0


def bounds(values):
    values = [v for v in values if v is not None and not math.isnan(v)]
    return {"min": min(values), "max": max(values)} if values else None


def enumerate_pools(family):
    base = {"positive": set(), "negative": set()}
    uncertain = []
    for identity, entry in family["traits"].items():
        for polarity in base:
            if entry[polarity] == "allowed":
                base[polarity].add(identity)
            elif entry[polarity] == "unresolved":
                uncertain.append((identity, polarity))
    if len(uncertain) > 12:
        raise ValueError("Too many unresolved pool entries")
    result = []
    for mask in range(2 ** len(uncertain)):
        pool = {key: set(value) for key, value in base.items()}
        for bit, (identity, polarity) in enumerate(uncertain):
            if mask & (1 << bit):
                pool[polarity].add(identity)
        result.append(pool)
    return result


def setup_outcomes(pool, k, has_negative, lock):
    positive = lock["polarity"] == "positive"
    identity = lock["id"]
    if identity not in pool[lock["polarity"]] or (not positive and not has_negative):
        return
    candidates = sorted(pool["positive"] - {identity})
    draws = k - int(positive)
    denominator = choose(len(candidates), draws)
    if not denominator:
        return
    for rolled in combinations(candidates, draws):
        positives = (identity, *rolled) if positive else rolled
        negatives = (
            [None]
            if not has_negative
            else [identity]
            if not positive
            else sorted(pool["negative"] - set(positives))
        )
        for negative in negatives:
            yield rolled, positives, negative, 1 / denominator / len(negatives)


def partner_chance(pool, recipes, k, has_negative, held):
    partners = {b if a == held else a for a, b in recipes if held in (a, b)}
    total = sum(
        weight
        for rolled, _, negative, weight in setup_outcomes(
            pool, k, has_negative, {"id": held, "polarity": "positive"}
        )
        if partners.intersection(rolled) or negative in partners
    )
    return min(1, total)


def splice_route(
    pool, recipes, k, has_negative, lock, grade_chance=0.025, partner_chances=None
):
    if k not in (2, 3) or not 0 < grade_chance <= 1:
        return None
    ingredients = sorted({item for pair in recipes for item in pair} & pool["positive"])
    if (
        grade_chance == 1
        and lock["polarity"] == "positive"
        and lock["id"] in ingredients
    ):
        return None
    partner_chances = partner_chances or {
        identity: partner_chance(pool, recipes, k, has_negative, identity)
        for identity in ingredients
    }
    partners = {
        identity: {b if a == identity else a for a, b in recipes if identity in (a, b)}
        for identity in ingredients
    }
    chance = weighted_extra = needs_partner = 0
    for rolled, positives, negative, weight in setup_outcomes(
        pool, k, has_negative, lock
    ):
        eligible = [i for i in rolled if partner_chances.get(i, 0) > 0]
        for mask in range(1, 2 ** len(eligible)):
            grade_weight, extra = 1, math.inf
            for bit, identity in enumerate(eligible):
                qualifies = bool(mask & (1 << bit))
                grade_weight *= grade_chance if qualifies else 1 - grade_chance
                if qualifies:
                    ready = (
                        partners[identity].intersection(positives)
                        or negative in partners[identity]
                    )
                    extra = min(extra, 0 if ready else 1 / partner_chances[identity])
            contribution = weight * grade_weight
            chance += contribution
            weighted_extra += contribution * extra
            if extra > 0:
                needs_partner += contribution
    if not chance:
        return None
    first, additional = 1 / chance, weighted_extra / chance
    return {
        "lock": lock,
        "chance": chance,
        "first": first,
        "additional": additional,
        "total": first + additional,
        "ready": 1 - needs_partner / chance,
        "ifMissing": weighted_extra / needs_partner if needs_partner else 0,
    }


def optimal_splice_setup(pools, recipes, k, has_negative, grade_chance=0.025):
    recipe_ids = sorted({i for pair in recipes for i in pair})
    route_cache, partner_cache, scenarios = {}, {}, []
    for pool in pools:

        def membership(identity):
            return int(identity in pool["positive"]) + 2 * int(
                identity in pool["negative"]
            )

        signature = (
            len(pool["positive"]),
            len(pool["negative"]),
            len(pool["positive"] & pool["negative"]),
            *(membership(i) for i in recipe_ids),
        )
        if signature not in partner_cache:
            partner_cache[signature] = {
                i: partner_chance(pool, recipes, k, has_negative, i)
                for i in recipe_ids
                if i in pool["positive"]
            }
        routes = []
        for polarity in ["negative", "positive"] if has_negative else ["positive"]:
            for identity in sorted(pool[polarity]):
                lock = {"id": identity, "polarity": polarity}
                key = (
                    signature,
                    polarity,
                    identity if identity in recipe_ids else membership(identity),
                )
                if key not in route_cache:
                    route_cache[key] = splice_route(
                        pool,
                        recipes,
                        k,
                        has_negative,
                        lock,
                        grade_chance,
                        partner_cache[signature],
                    )
                if route_cache[key]:
                    routes.append({**route_cache[key], "lock": lock})
        minimum = min((r["total"] for r in routes), default=math.inf)
        scenarios.append([r for r in routes if abs(r["total"] - minimum) < 1e-8])
    if not scenarios or any(not rows for rows in scenarios):
        return {"available": False, "uncertain": len(pools) > 1}
    common = [
        r
        for r in scenarios[0]
        if all(any(other["lock"] == r["lock"] for other in rows) for rows in scenarios)
    ]
    if not common:
        return {"available": False, "uncertain": True}
    representative = common[0]
    selected = [
        next(r for r in rows if r["lock"] == representative["lock"])
        for rows in scenarios
    ]
    metrics = ("chance", "first", "additional", "total", "ready", "ifMissing")
    equivalent = []
    for candidate in common:
        if all(
            all(
                abs(
                    next(r for r in rows if r["lock"] == candidate["lock"])[key]
                    - selected[index][key]
                )
                < 1e-8
                for key in metrics
            )
            for index, rows in enumerate(scenarios)
        ):
            equivalent.append(candidate["lock"])
    return {
        "available": True,
        "uncertain": len(pools) > 1,
        "lock": representative["lock"],
        "equivalentLocks": equivalent,
        **{key: bounds(r[key] for r in selected) for key in metrics},
    }


def selected_lock_chance(pool, target, assumptions):
    identity, polarity, k = target["id"], target["polarity"], target["positives"]
    negative = target["hasNegative"]
    if (
        k not in (2, 3)
        or polarity not in pool
        or identity in SPLICE_IDS
        or identity not in pool[polarity]
        or (polarity == "negative" and not negative)
    ):
        return 0
    p, n = len(pool["positive"]), len(pool["negative"])
    draws = k - int(target.get("hasSplice", False))
    denominator = choose(p, draws)
    if not denominator:
        return 0
    shared = len(pool["positive"] & pool["negative"])
    probability = 0
    if polarity == "positive":
        if not negative:
            probability = choose(p - 1, draws - 1) / denominator
        else:
            delta = int(identity in pool["negative"])
            remaining = shared - delta
            probability = sum(
                choose(remaining, j)
                * choose(p - 1 - remaining, draws - 1 - j)
                / denominator
                for j in range(draws)
                if n - delta - j > 0
            )
    else:
        delta = int(identity in pool["positive"])
        remaining = shared - delta
        probability = sum(
            choose(remaining, j)
            * choose(p - delta - remaining, draws - j)
            / denominator
            / (n - j)
            for j in range(draws + 1)
            if n > j
        )
    return min(
        1,
        probability
        * (1 if target.get("hasSplice") else assumptions["unlockedLayoutWeight"]),
    )


@lru_cache(maxsize=64)
def target_combinations(options):
    """Unordered, distinct-stat outcomes satisfying every slot exactly once."""
    return tuple(
        sorted(
            {
                tuple(sorted(row))
                for row in product(*options)
                if len(set(row)) == len(row)
            }
        )
    )


def matches_targets(stats, options):
    """Require a distinct rolled stat for each target slot, regardless of order."""
    return (
        len(stats) == len(options)
        and len(set(stats)) == len(stats)
        and any(
            all(stat in choices for stat, choices in zip(assignment, options))
            for assignment in permutations(stats)
        )
    )


def evaluate(pool, target, assumptions):
    options = target.get("positiveOptions")
    if not options:
        return _evaluate_single(pool, target, assumptions)
    if all(len(row) == 1 for row in options):
        return _evaluate_single(
            pool, {**target, "positives": [row[0] for row in options]}, assumptions
        )
    # Use the same held stat for every outcome, including hypothetical strategies.
    fixed = dict(target)
    fixed["heldPositive"] = target.get("heldPositive") or next(
        iter(
            target.get("retainedPositives")
            or [i for row in options for i in row if i not in SPLICE_IDS]
        ),
        None,
    )
    totals = {key: [] for key in ("q0", "qPositive", "qNegative")}
    for positives in target_combinations(tuple(tuple(row) for row in options)):
        row = _evaluate_single(pool, {**fixed, "positives": positives}, assumptions)
        for key, value in row.items():
            totals[key].append(value)
    return {key: math.fsum(values) for key, values in totals.items()}


def _evaluate_single(pool, target, assumptions):
    positives, negatives = target["positives"], target["negatives"]
    combined = [i for i in positives if i in SPLICE_IDS]
    held_negative = target.get("heldNegative")
    retained_positive = set(target.get("retainedPositives", [])) - SPLICE_IDS
    retained_negative = set(target.get("retainedNegatives", []))
    retained = [i for i in positives if i in retained_positive]
    candidates = [i for i in positives if i not in SPLICE_IDS]
    ordinary = [i for i in candidates if i not in retained_positive]
    held_positive = target.get("heldPositive") or next(
        iter(retained or candidates), None
    )
    k = len(positives)
    m, p, n = k - len(combined), len(pool["positive"]), len(pool["negative"])
    r = len(set(positives) & pool["negative"])
    d = n - r
    compatible = pool["negative"] - set(positives)
    a = len(set(negatives) & compatible)
    delta = int(held_negative in pool["positive"])
    valid = (
        k in (2, 3)
        and len(combined) <= 1
        and len(set(positives)) == k
        and all(i in pool["positive"] for i in ordinary)
    )
    unlocked = valid and not retained
    positive_lock = (
        valid
        and held_positive in candidates
        and (
            held_positive == retained[0]
            if len(retained) == 1
            else not retained and held_positive in pool["positive"]
        )
    )
    positive_candidates = p - int(
        held_positive not in retained_positive and held_positive in pool["positive"]
    )
    negative_lock = (
        target["hasNegative"]
        and held_negative in negatives
        and held_negative not in positives
        and (held_negative in compatible or held_negative in retained_negative)
    )
    factor = (a / d if d > 0 else 0) if target["hasNegative"] else 1

    def inverse(count, draws):
        ways = choose(count, draws)
        return 1 / ways if ways else 0

    q0 = (
        (1 if combined else assumptions["unlockedLayoutWeight"])
        * inverse(p, m)
        * factor
        if unlocked
        else 0
    )
    qp = (
        assumptions["positiveLockLayoutWeight"]
        * inverse(positive_candidates, m - 1)
        * factor
        if positive_lock
        else 0
    )
    qn = (
        assumptions["negativeLockLayoutWeight"] * inverse(p - delta, m)
        if unlocked and negative_lock
        else 0
    )
    return {"q0": q0, "qPositive": qp, "qNegative": qn}


def attempts_for(probability, confidence):
    if not 0 < confidence < 1:
        raise ValueError("Confidence must be between zero and one")
    if probability <= 0:
        return math.inf
    return (
        1
        if probability >= 1
        else math.ceil(math.log1p(-confidence) / math.log1p(-probability))
    )
