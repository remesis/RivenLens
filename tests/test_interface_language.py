# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Offscreen interface checks; no user settings or live capture are accessed."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "native"), str(ROOT / "app")]


@unittest.skipUnless(
    importlib.util.find_spec("PySide6"), "Install app dependencies for UI tests"
)
class InterfaceLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import ui_text

        cls.app = QApplication.instance() or QApplication([])
        cls.ui = ui_text

    def tearDown(self):
        self.ui.language.set("en")

    def test_initial_locale_distinguishes_chinese_scripts(self):
        for tag, expected in (
            ("zh-Hant", "tc"),
            ("zh-TW", "tc"),
            ("zh-Hans-CN", "zh"),
            ("pt-BR", "pt"),
            ("fr-FR", "fr"),
            ("th-TH", "en"),
        ):
            self.assertEqual(self.ui.default_language([tag]), expected)

    def test_repeated_translation_and_clear_are_lossless(self):
        label = self.ui.QLabel("Waiting for a Riven")
        for code in self.ui.LANGUAGES:
            self.ui.language.set(code)
            self.assertEqual(label.text(), self.ui.translate("Waiting for a Riven"))
        label.clear()
        self.ui.language.set("fr")
        self.assertEqual(label.text(), "")
        self.ui.language.set("en")
        self.assertEqual(label.text(), "")
        label.deleteLater()

    def test_language_picker_fits_widest_entry_at_each_scale(self):
        from PySide6.QtWidgets import QStyle, QStyleOptionComboBox
        from appearance import theme_for
        from language_picker import LanguagePicker, entry_width

        picker = LanguagePicker("en")
        self.addCleanup(picker.deleteLater)
        self.assertEqual(picker.layout().count(), 1)
        combo = picker.combo
        widths = []
        for scale in (0.85, 1, 1.65):
            picker.setStyleSheet(theme_for(scale))
            picker.set_ui_scale(scale)
            picker.show()
            self.app.processEvents()
            option = QStyleOptionComboBox()
            combo.initStyleOption(option)
            edit = (
                combo.style()
                .subControlRect(
                    QStyle.ComplexControl.CC_ComboBox,
                    option,
                    QStyle.SubControl.SC_ComboBoxEditField,
                    combo,
                )
                .adjusted(2, 0, -1, 0)
            )
            widest = max(
                entry_width(combo.itemText(i), option.fontMetrics, compact=True)
                for i in range(combo.count())
            )
            self.assertEqual(edit.width(), widest)
            width = combo.width()
            widths.append(width)
            for i in range(combo.count()):
                combo.setCurrentIndex(i)
                self.app.processEvents()
                self.assertEqual(combo.width(), width)
            picker.hide()
        self.assertLess(widths[0], widths[1])
        self.assertLess(widths[1], widths[2])

    def test_language_badge_is_drawn_before_text(self):
        from unittest.mock import Mock, patch
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QColor, QFontMetrics
        from language_picker import badge_size, draw_entry

        metrics = QFontMetrics(self.app.font())
        rect = QRect(7, 2, 200, 30)
        painter = Mock()
        with patch("language_picker.flag_pixmap", return_value=object()):
            draw_entry(painter, rect, "English", "en", metrics, QColor("white"))
        self.assertEqual(painter.drawPixmap.call_args.args[0], rect.left())
        self.assertEqual(
            painter.drawText.call_args.args[0].left(),
            rect.left() + badge_size(metrics) + 6,
        )

    def test_footer_picker_matches_link_height_without_clipping(self):
        from PySide6.QtWidgets import QHBoxLayout, QStyle, QStyleOptionComboBox, QWidget
        from appearance import theme_for
        from language_picker import LanguagePicker, badge_size
        from widgets import Hyperlink

        footer = QWidget()
        self.addCleanup(footer.deleteLater)
        layout = QHBoxLayout(footer)
        link = Hyperlink("discord.gg/Arbitrations", "https://discord.gg/Arbitrations")
        picker = LanguagePicker("pt")
        layout.addWidget(link)
        layout.addWidget(picker)
        for scale in (0.85, 1, 1.25, 1.65):
            footer.setStyleSheet(theme_for(scale))
            link.set_ui_scale(scale)
            height = link.sizeHint().height()
            picker.set_ui_scale(scale, height)
            footer.show()
            self.app.processEvents()
            self.assertEqual(picker.height(), height)
            self.assertEqual(picker.combo.height(), height)
            option = QStyleOptionComboBox()
            picker.combo.initStyleOption(option)
            edit = picker.combo.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option,
                QStyle.SubControl.SC_ComboBoxEditField,
                picker.combo,
            )
            self.assertGreaterEqual(edit.height(), option.fontMetrics.height())
            self.assertGreaterEqual(
                edit.height(), badge_size(option.fontMetrics, compact=True)
            )
            footer.hide()


if __name__ == "__main__":
    unittest.main()
