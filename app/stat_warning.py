# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Debounced new-card text warnings, independent of rank and grading metadata."""


def missing_known_stats(card, known_cards):
    """A cursor-hidden trait must not turn a known card into a smaller format."""

    def readings(item):
        return {
            (s["id"], s["value"], s.get("unit", "%"), s["polarity"])
            for s in item["stats"]
        }

    return any(
        old
        and card["weapon"] == old["weapon"]
        and card["title"] == old["title"]
        and readings(card) < readings(old)
        for old in known_cards
    )


class NewRollStatWarning:
    """Show sustained stat failures, sounding once for each proposed roll."""

    SETTLE_SECONDS = 1.25
    MIN_BAD_READS = 4

    def __init__(self):
        self.serial = 0
        self.reset()

    def reset(self):
        self.since = None
        self.bad_reads = 0
        self.good_reads = 0
        self.last_stats = None
        self.warning = None
        self.roll_stats = None
        self.warning_id = None

    def update(self, raw, mode, now, known_cards=()):
        if mode in ("current", "inspection", "transition"):
            self.reset()
            return None
        if mode != "comparison" and len(raw) != 2:
            # A menu, animation or missing phase caption is not a stat failure.
            self.since = None
            self.bad_reads = self.good_reads = 0
            self.last_stats = None
            return None

        candidate = raw[-1] if len(raw) == 2 else None
        if len(raw) == 1 and raw[0].get("screenX", 0) >= 0.43:
            candidate = raw[0]
        readable = (
            candidate is not None
            and candidate.get("complete", False)
            and not missing_known_stats(candidate, known_cards)
        )
        if readable:
            identity = (
                candidate["weapon"],
                tuple(
                    (s["id"], s["value"], s.get("unit", "%"), s["polarity"])
                    for s in candidate["stats"]
                ),
            )
            recovering = self.last_stats is None
            self.good_reads = self.good_reads + 1 if identity == self.last_stats else 1
            self.last_stats = identity
            if self.good_reads >= 2:
                if self.roll_stats is not None and identity != self.roll_stats:
                    # A different verified roll also rearms when the intervening
                    # confirmation/animation was missed between captures.
                    self.warning_id = None
                self.roll_stats = identity
                self.since = None
                self.bad_reads = 0
                self.warning = None
                return None
            if recovering:
                # A clear read breaks a run of occlusions. Keep an existing
                # warning until confirmation, but do not combine separate mouse
                # passes into one sustained failure. Alternating numeric reads
                # still accumulate below until the values agree twice.
                self.since = None
                self.bad_reads = 0
                return self.warning
        else:
            self.good_reads = 0
            self.last_stats = None
        self.bad_reads += 1
        if self.since is None:
            self.since = now
        if (
            self.warning is None
            and self.bad_reads >= self.MIN_BAD_READS
            and now - self.since >= self.SETTLE_SECONDS
        ):
            if self.warning_id is None:
                self.serial += 1
                self.warning_id = self.serial
            self.warning = {
                "id": self.warning_id,
                "message": "New roll stat lines could not be reliably read. Check the card before rolling again.",
            }
        return self.warning
