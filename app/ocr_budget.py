# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Bound optional retries without cancelling an in-flight Windows OCR call."""

import time


class FrameBudget:
    """Cap retry calls, reserving progress for at most two located cards.

    The time limit covers optional work, not the required initial read. One
    reserved verification per card may run after it expires, within the same
    total pass cap. A slow new-card read cannot starve the current card either.
    """

    def __init__(self, seconds=0.20, passes=4):
        self.seconds = seconds
        self.deadline = None
        self.remaining = passes
        self.verification_slots = 0
        self.attempted_locations = set()

    def reserve_verifications(self, count):
        self.verification_slots = min(2, max(0, count))

    def take(self, verification=None):
        now = time.monotonic()
        if self.deadline is None:
            self.deadline = now + self.seconds
        reserved = max(0, self.verification_slots - len(self.attempted_locations))
        first_verification = (
            reserved > 0
            and verification is not None
            and verification not in self.attempted_locations
        )
        if self.remaining <= 0 or (
            not first_verification
            and (now >= self.deadline or self.remaining <= reserved)
        ):
            return False
        self.remaining -= 1
        if first_verification:
            self.attempted_locations.add(verification)
        return True


def allow_retry(engine, *, verification=None):
    budget = getattr(engine, "_frame_budget", None)
    return budget is None or budget.take(verification)


def reserve_verifications(engine, count):
    budget = getattr(engine, "_frame_budget", None)
    if budget is not None:
        budget.reserve_verifications(count)


async def primary_read(engine, image):
    """Required discovery reads do not spend the optional retry time allowance."""
    budget = getattr(engine, "_frame_budget", None)
    started = time.monotonic()
    try:
        return await engine.read(image)
    finally:
        if budget is not None and budget.deadline is not None:
            budget.deadline += time.monotonic() - started
