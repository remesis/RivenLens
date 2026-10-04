# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Track kept/proposed rolls from visible card and action-label observations."""

from parser import fingerprint
from stat_warning import NewRollStatWarning, missing_known_stats


def key(card):
    return fingerprint({"cards": [card]}) if card else None


class RollTracker:
    STALE_AFTER = 1.0

    def __init__(self, stat_warning=None):
        self.current = None
        self.new = None
        self.previous = {"current": None, "new": None}
        self.hits = {"current": 0, "new": 0}
        self.metadata_hits = {"current": {}, "new": {}}
        self.seen = {"current": 0, "new": 0}
        self.cycle_key = None
        self.cycle_hits = 0
        self.stat_warning = (
            stat_warning if stat_warning is not None else NewRollStatWarning()
        )
        self.stat_warning.reset()

    def needs_current(self, now):
        """Recheck the dim left card when its last verified reading is old."""
        return self.current is None or now - self.seen["current"] > 0.75

    def pending_new_key(self):
        """None means a current card still has to be established before comparison."""
        return (key(self.new) or "") if self.current else None

    def interrupt(self):
        """Keep verified cards, but discard freshness and partial warning evidence."""
        self.previous = {"current": None, "new": None}
        self.hits = {"current": 0, "new": 0}
        self.metadata_hits = {"current": {}, "new": {}}
        self.seen = {"current": 0, "new": 0}
        self.cycle_key, self.cycle_hits = None, 0
        self.stat_warning.reset()

    def _clear_new(self):
        self.new = None
        self.previous["new"], self.hits["new"] = None, 0
        self.metadata_hits["new"] = {}

    def _observe(self, slot, card, now, allow_commit=True):
        if not card or not card.get("complete"):
            self.previous[slot], self.hits[slot] = None, 0
            self.metadata_hits[slot] = {}
            return False
        identity = key(card)
        held = getattr(self, slot)
        if missing_known_stats(card, (self.current, self.new)):
            self.previous[slot], self.hits[slot] = None, 0
            self.metadata_hits[slot] = {}
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
        previous = self.previous[slot]
        same_stats = previous is not None and previous[0] == identity
        self.hits[slot] = self.hits[slot] + 1 if same_stats else 1
        self.previous[slot] = observation
        # The stat identity settles independently of optional pip/caption OCR.
        # Metadata changes still need two matching observations of their own.
        for index, field in enumerate(("rank", "variantHint"), 1):
            count = self.metadata_hits[slot].get(field, 0)
            count = (
                count + 1 if same_stats and observation[index] == previous[index] else 1
            )
            self.metadata_hits[slot][field] = count
            if count < 2 and candidate.get(field) != (known or {}).get(field):
                candidate[field] = (known or {}).get(field)
                missing_metadata = True
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
        if len(raw) == 2:
            # Selection changes size/position, never left-to-right role order.
            raw = sorted(raw, key=lambda card: card.get("screenX", 0.5))
        mode = result.get("mode", "unknown")
        strict_roles = result.get("strictRoles", False)
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
        elif (
            len(raw) == 1
            and raw[0].get("complete")
            and (not strict_roles or mode == "comparison")
        ):
            card = raw[0]
            position = card.get("screenX", -1)
            if mode == "comparison" and 0 <= position < 0.43:
                observations["current"] = card
            elif mode == "comparison" and position > 0.57:
                observations["new"] = card
            elif self.current and key(card) == key(self.current):
                observations["current"] = card
            elif not strict_roles and self.new and key(card) == key(self.new):
                observations["new"] = card
            elif (
                not strict_roles
                and self.current
                and card["weapon"] == self.current["weapon"]
            ):
                # A new card is revealed in the center before the comparison
                # settles. Promote it only on CYCLE FOR or as a verified left card.
                observations["new"] = card
        previous_current = key(self.current)
        previous_new = key(self.new)
        fresh = set()
        for slot, card in observations.items():
            can_commit = mode != "current" or cycle_resolved
            if self._observe(slot, card, now, can_commit):
                fresh.add(slot)
        if (
            previous_current != key(self.current)
            and key(self.current) == previous_new
            and key(self.new) == previous_new
            and not (
                observations["new"]
                and observations["new"].get("complete")
                and key(observations["new"]) == previous_new
            )
        ):
            # A verified left card can reveal an acceptance missed between scans.
            # The former proposal is now kept, not a snapshot of the next roll.
            # Preserve any first fresh observation of that next roll.
            self.new = None
            fresh.discard("new")
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
            if held:
                cards.append(
                    {
                        **held,
                        "slot": slot,
                        "snapshot": slot not in fresh,
                        "displayStale": slot not in fresh
                        and now - self.seen[slot] >= self.STALE_AFTER,
                    }
                )
        live = bool(fresh)
        pending = any(self.hits.values())
        return {
            "cards": cards,
            "located": bool(raw),
            "newRollWarning": warning,
            "status": "reading" if live else "settling" if pending else "waiting",
            "message": "Live grades."
            if live
            else "Waiting for a clear reading; previous grades remain visible."
            if cards
            else result.get("reason") or "Checking the next frame...",
        }
