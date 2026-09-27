# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Portable dictionary/parser checks; no capture, game files or OCR models.

Run from the repository root: python -m unittest discover -s tests -v
"""

import json
import re
import sys
import unittest
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from localization import LANGUAGES, adapt_lines, profile  # noqa: E402
from parser import parse_cards  # noqa: E402


def rendered(stat, value):
    return re.sub(
        r"\|(?:val|STAT1)\|",
        value,
        re.sub(r"<[^>]+>", "", stat["template"]),
        flags=re.I,
    )


def row(text, y=100, x=100, height=26):
    return {"text": text, "x": x, "y": y, "w": 440, "h": height}


class DictionaryTests(unittest.TestCase):
    def test_every_stat_template(self):
        for code in LANGUAGES:
            locale = profile(code)
            for stat in locale.data["stats"]:
                with self.subTest(code=code, stat=stat["id"], template=stat["template"]):
                    value = "x1,32" if stat["unit"] == "x" else "+12,3"
                    self.assertIsNotNone(locale.stat(rendered(stat, value)))

    def test_every_weapon_name_and_title(self):
        for code in LANGUAGES:
            locale = profile(code)
            for name, aliases in locale.data["weapons"].items():
                plain = re.sub(r" \((Primary|Secondary|Rifle|Melee)\)$", "", name)
                for alias in aliases:
                    with self.subTest(code=code, name=name, alias=alias):
                        self.assertEqual(locale.weapon(alias), plain)
                        self.assertEqual(
                            locale.title(alias + " Crita-visican"),
                            (plain, "Crita-visican"),
                        )

    def test_controls_and_footer(self):
        for code in LANGUAGES:
            locale = profile(code)
            for key, canonical in (
                ("fits", "FITS IN"),
                ("confirm", "CONFIRM"),
                ("yes", "YES"),
                ("no", "NO"),
            ):
                self.assertEqual(locale.control(locale.ui[key]), canonical, code)
            self.assertEqual(
                locale.control(locale.ui["footer"].replace("|LEVEL|", "12")),
                "MR 12",
                code,
            )

    def test_cards_keep_canonical_ids_and_visible_names(self):
        for code in LANGUAGES:
            if code == "en":
                continue
            locale = profile(code)
            definitions = {stat["id"]: stat for stat in locale.data["stats"]}
            title = locale.data["weapons"]["Sobek"][0] + " Crita-visican"
            lines = [row(title)]
            for n, (identity, value) in enumerate(
                (
                    ("multishot", "+159,3"),
                    ("critical-damage", "+87,8"),
                    ("impact", "-111,9"),
                )
            ):
                lines.append(row(rendered(definitions[identity], value), 140 + n * 40))
            lines.append(row(locale.ui["footer"].replace("|LEVEL|", "12"), 310))
            result = parse_cards(adapt_lines(lines, code), 1600, 900)
            with self.subTest(code=code):
                self.assertTrue(result["complete"], result)
                card = result["cards"][0]
                self.assertEqual(card["title"], title)
                self.assertEqual(card["weapon"], "Sobek")
                self.assertEqual(card["format"], "2p1n")
                self.assertEqual(
                    [stat["value"] for stat in card["stats"]], [159.3, 87.8, -111.9]
                )

    def test_ambiguous_numbers_and_unknown_labels_are_rejected(self):
        for code in LANGUAGES:
            locale = profile(code)
            stat = next(s for s in locale.data["stats"] if s["id"] == "multishot")
            for value in ("+12 3", "123", "+12/3", "+1 „23", "+12:3"):
                self.assertIsNone(locale.stat(rendered(stat, value)), (code, value))
            self.assertIsNone(
                locale.stat(rendered(stat, "+12,3").replace("%", "")), code
            )
            self.assertIsNone(locale.stat("+100% unknown 不明 неизвестно"), code)

    def test_icon_glyphs_do_not_relax_values_or_labels(self):
        locale = profile("ko")
        self.assertEqual(locale.stat("+111.3% •쬐독성"), "+111.3% Toxin")
        self.assertEqual(locale.stat("+150.4% 4 전기"), "+150.4% Electricity")
        for text in ("+111 3% •쬐독성", "+111.3% 임의문자독성", "+111.3% •쬐독녕"):
            self.assertIsNone(locale.stat(text))

    def test_english_adapter_preserves_input(self):
        lines = [row("Sobek Crita-visican"), row("+100% Multishot", 140)]
        self.assertIs(adapt_lines(lines, "en"), lines)

    def test_turkish_uppercase_controls_keep_footer_and_cycle(self):
        locale = profile("tr")
        self.assertEqual(locale.control("SEVİYE 9"), "MR 9")
        self.assertEqual(locale.control("DEVİR İŞLEMİ MALİYETİ: 3,500"), "CYCLE FOR")
        self.assertEqual(locale.control("DEVİR İŞLEMİ MALİYETİ:"), "CYCLE FOR")
        self.assertEqual(locale.footer_bounds([row("SEVİYE"), row("9")])["h"], 26)

    def test_chinese_decimal_and_latin_faction_glyphs(self):
        locale = profile("zh")
        self.assertEqual(locale.stat("+ 87 · 3 ％ 攻 击 速 度"), "+87.3% Fire Rate")
        self.assertEqual(
            locale.stat("× 0 . 75 对 lnfested 的 伤 害"), "0.75x Damage to Infested"
        )
        self.assertEqual(
            locale.stat("+ 113 , 9 % , 电 击 伤 害"), "+113,9% Electricity"
        )
        self.assertEqual(
            locale.stat("一 54 · 4 % 暴 击 伤 害"), "-54.4% Critical Damage"
        )
        for text in (
            "+ 87 3 ％ 攻 击 速 度",
            "× 0 · 7 · 5 对 lnfested 的 伤 害",
            "× O · 75 对 lnfested 的 伤 害",
            "× 0.75 对 lnfestea 的 伤 害",
        ):
            self.assertIsNone(locale.stat(text))

    def test_interface_translations_have_all_languages_and_placeholders(self):
        data = json.loads((ROOT / "native/data/ui_strings.json").read_text("utf-8"))
        self.assertEqual(set(data["languages"]), set(LANGUAGES) - {"en"})

        def fields(text):
            return {field for _, field, _, _ in Formatter().parse(text) if field}

        for source, translations in data["strings"].items():
            self.assertEqual(len(translations), len(data["languages"]), source)
            for translation in translations:
                self.assertTrue(translation.strip(), source)
                self.assertEqual(fields(source), fields(translation), source)


if __name__ == "__main__":
    unittest.main()
