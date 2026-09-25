# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See docs/LICENSE.txt for the license and warranty disclaimer.

"""Track kept/proposed rolls from visible card and action-label observations."""

from parser import fingerprint
from stat_warning import NewRollStatWarning, missing_known_stats


def key(card):
    return fingerprint({"cards": [card]}) if card else None


class RollTracker:
    STALE_AFTER = 1.0

    def __init__(self):
        self.current = None
        self.new = None
        self.previous = {"current": None, "new": None}
        self.hits = {"current": 0, "new": 0}
        self.seen = {"current": 0, "new": 0}
        self.cycle_key = None
        self.cycle_hits = 0
        self.stat_warning = NewRollStatWarning()

    def needs_current(self, now):
        """Recheck the dim left card when its last verified reading is old."""
        return self.current is None or now - self.seen["current"] > 0.75

    def _clear_new(self):
        self.new = None
        self.previous["new"], self.hits["new"] = None, 0

    def _observe(self, slot, card, now, allow_commit=True):
        if not card or not card.get("complete"):
            self.previous[slot], self.hits[slot] = None, 0
            return False
        identity = key(card)
        held = getattr(self, slot)
        if missing_known_stats(card, (self.current, self.new)):
            self.previous[slot], self.hits[slot] = None, 0
            return False
        known = next(
            (old for old in (held, self.current, self.new) if key(old) == identity),
            None,
        )
        candidate = dict(card)
        missing_metadata = False
        if known:
            # An obscured pip/caption is not a new rank/variant. Retain the last
            # verified value for display, but never alert on this inferred scan.
            for field in ("rank", "variantHint"):
                if candidate.get(field) is None and known.get(field) is not None:
                    candidate[field] = known[field]
                    missing_metadata = True
            candidate["title"] = known["title"]
        observation = (identity, candidate.get("rank"), candidate.get("variantHint"))
        self.hits[slot] = (
            self.hits[slot] + 1 if observation == self.previous[slot] else 1
        )
        self.previous[slot] = observation
        matches_held = (
            identity == key(held)
            and candidate.get("rank") == held.get("rank")
            and candidate.get("variantHint") == held.get("variantHint")
        )
        verified = (
            known is not None
            and candidate.get("rank") == known.get("rank")
            and candidate.get("variantHint") == known.get("variantHint")
        )
        if allow_commit and (self.hits[slot] >= 2 or verified):
            setattr(self, slot, candidate)
            matches_held = True
        fresh = matches_held and not missing_metadata
        if fresh:
            self.seen[slot] = now
        return fresh

    @staticmethod
    def _centered_card(raw):
        if len(raw) == 1:
            card = raw[0]
            return (
                card
                if "screenX" not in card or 0.43 <= card["screenX"] <= 0.57
                else None
            )
        # After confirmation the chosen card centers while the rejected one
        # may still be sliding away. Left/right order alone is not enough here.
        centered = [card for card in raw if 0.43 <= card.get("screenX", -1) <= 0.57]
        return centered[0] if len(centered) == 1 else None

    def update(self, result, now):
        raw = result.get("cards", [])
        mode = result.get("mode", "unknown")
        warning = self.stat_warning.update(raw, mode, now, (self.current, self.new))
        observations = {"current": None, "new": None}
        cycle = self._centered_card(raw) if mode == "current" else None
        cycle_identity = key(cycle) if cycle and cycle.get("complete") else None
        self.cycle_hits = (
            self.cycle_hits + 1
            if cycle_identity and cycle_identity == self.cycle_key
            else (1 if cycle_identity else 0)
        )
        self.cycle_key = cycle_identity
        cycle_resolved = cycle_identity and (
            self.cycle_hits >= 2 or cycle_identity in (key(self.current), key(self.new))
        )
        if mode == "transition":
            pass  # Dialogs/animations do not tell us which card the user kept.
        elif mode == "current":
            observations["current"] = cycle
        elif len(raw) == 2:
            observations = {"current": raw[0], "new": raw[1]}
        elif len(raw) == 1 and raw[0].get("complete"):
            card = raw[0]
            if self.current and key(card) == key(self.current):
                observations["current"] = card
            elif self.current and card["weapon"] == self.current["weapon"]:
                # A new card is revealed in the center before the comparison
                # settles. Promote it only on CYCLE FOR or as a verified left card.
                observations["new"] = card
        fresh = set()
        for slot, card in observations.items():
            can_commit = mode != "current" or cycle_resolved
            if self._observe(slot, card, now, can_commit):
                fresh.add(slot)
        if cycle_identity and cycle_resolved and key(self.current) == cycle_identity:
            # This works for keeping either side, including accepting the new roll.
            self._clear_new()
            fresh.discard("new")
        if self.current and self.new and self.current["weapon"] != self.new["weapon"]:
            self._clear_new()
            fresh.discard("new")
        cards = []
        for slot in ("current", "new"):
            held = getattr(self, slot)
            if held and self.current:
                cards.append(
                    {
                        **held,
                        "snapshot": slot not in fresh,
                        "displayStale": slot not in fresh
                        and now - self.seen[slot] >= self.STALE_AFTER,
                    }
                )
        live = bool(fresh)
        pending = any(self.hits.values())
        return {
            "cards": cards,
            "newRollWarning": warning,
            "status": "reading" if live else "settling" if pending else "waiting",
            "message": "Live grades."
            if live
            else "Waiting for a clear reading; previous grades remain visible."
            if cards
            else result.get("reason") or "Checking the next frame...",
        }
