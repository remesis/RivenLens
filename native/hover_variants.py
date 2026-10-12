# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Session-only display fallback from a confidently graded, compatible hover."""

RECENT_HOVER_NOTE = (
    "Variant unsure: grading as {variant} using a recent matching hover."
)


class HoverVariantMemory:
    def __init__(self, catalog):
        self.catalog = catalog
        self.reset()

    def reset(self):
        self.context = None
        self.variant_id = None

    def apply(self, cards, mode):
        # Never trust fallback metadata from an earlier display update as new
        # evidence. Only fresh, uniquely compatible cards can seed this memory.
        output = [
            {k: v for k, v in card.items() if k != "hoverVariantFallback"}
            for card in cards
        ]
        if mode != "hover":
            self.reset()
            return output
        if len(output) != 1:
            return output
        card = output[0]
        group = self.catalog.group(card)
        menu = card.get("hoverMenu")
        if (
            card.get("viewMode") != "hover"
            or not group
            or menu not in ("inventory", "arsenal")
        ):
            self.reset()
            return output
        context = (group["id"], menu)
        if context != self.context:
            self.reset()
            self.context = context
        compatible = self.catalog.hover_variants(card)
        if len(compatible) == 1:
            if not card.get("snapshot") and not card.get("displayStale"):
                self.variant_id = compatible[0]["id"]
        elif len(compatible) > 1 and any(
            v["id"] == self.variant_id for v in compatible
        ):
            card["hoverVariantFallback"] = self.variant_id
        return output
