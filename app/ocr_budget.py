# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Bound optional retries without cancelling an in-flight Windows OCR call."""

import time


class FrameBudget:
    def __init__(self, seconds=0.20, passes=4):
        self.deadline = time.monotonic() + seconds
        self.remaining = passes

    def take(self):
        if self.remaining <= 0 or time.monotonic() >= self.deadline:
            return False
        self.remaining -= 1
        return True


def allow_retry(engine):
    budget = getattr(engine, "_frame_budget", None)
    return budget is None or budget.take()
