# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Offline scheduling/verification checks; no Windows OCR or screen capture."""

import copy
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


@unittest.skipUnless(
    importlib.util.find_spec("PIL"), "Install app dependencies for image tests"
)
class LocalizedReaderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from PIL import Image
        import reader
        from ocr_budget import FrameBudget

        self.reader, self.Image, self.Budget = reader, Image, FrameBudget
        self.image = Image.new("RGB", (1600, 1000))
        self.parsed = reader.parse_cards(
            [
                dict(text=text, x=600, y=400 + i * 40, w=350, h=30)
                for i, text in enumerate(
                    (
                        "Lecta Cronitor",
                        "+2.9m Range",
                        "+87.3% Attack Speed",
                        "x0.75 Damage to Infested",
                        "MR 9",
                    )
                )
            ],
            1600,
            1000,
        )
        self.assertTrue(self.parsed["complete"])

    async def test_cjk_large_text_still_needs_confirming_read(self):
        for language in ("ja", "ko", "zh", "tc"):
            for percent_repaired in (False, True):
                with self.subTest(language=language, percent_repaired=percent_repaired):
                    card = copy.deepcopy(self.parsed["cards"][0])
                    card["normalizedPercent"] = percent_repaired
                    engine = SimpleNamespace(
                        language=language,
                        read=AsyncMock(return_value=[]),
                        _validate_stats=lambda _: True,
                        _frame_budget=self.Budget(seconds=20, passes=1),
                    )
                    result = await self.reader.refine_card(engine, self.image, card)
                    engine.read.assert_awaited_once()
                    self.assertFalse(result["complete"])

    async def test_cjk_confirmation_preserves_crop_padding_and_value(self):
        card = copy.deepcopy(self.parsed["cards"][0])
        engine = SimpleNamespace(
            language="ja",
            read=AsyncMock(return_value=[]),
            _frame_budget=self.Budget(seconds=20, passes=1),
        )
        with patch("reader.parse_cards", return_value=copy.deepcopy(self.parsed)):
            result = await self.reader.refine_card(engine, self.image, card)
        sample = engine.read.call_args.args[0]
        # First treatment is 2x. Both sides have >= 0.8 title-heights of room.
        self.assertGreaterEqual(sample.width / 2, card["textBounds"]["w"] + 48)
        self.assertTrue(result["complete"])
        self.assertEqual(result["stats"][-1]["value"], 0.75)

    async def test_variant_mask_gap_pixels_invalidate_cached_caption(self):
        engine = SimpleNamespace(language="ja", read=AsyncMock(return_value=[]))
        with patch("reader.caption_variant", return_value="Secura Lecta"):
            self.assertEqual(
                await self.reader.variant_hint(engine, self.image, discover=False),
                "Secura Lecta",
            )
            await self.reader.variant_hint(engine, self.image, discover=False)
            self.assertEqual(engine.read.await_count, 1)
            # This pixel lies between the quick strips, inside the extra mask.
            self.image.putpixel((1400, 765), (255, 180, 0))
            await self.reader.variant_hint(engine, self.image, discover=False)
            self.assertEqual(engine.read.await_count, 2)


if __name__ == "__main__":
    unittest.main()
