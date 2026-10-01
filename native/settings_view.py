# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Native settings, with the heading and Close button outside the scroll area."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from appearance import theme_for
from catalog import variant_label
from dialogs import ModalDialog
from widgets import Combo, box, label, options
from ui_text import QCheckBox, QFrame, QPushButton, translate


class SettingsCard(QFrame):
    """An always-visible group of settings with a noninteractive heading."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title = label(title, "settingsCardTitle")
        layout.addWidget(self.title)
        self.body, self.content = box()
        self.content.setContentsMargins(12, 6, 12, 14)
        self.content.setSpacing(12)
        layout.addWidget(self.body)


def field_row(caption, control, width=250):
    row, layout = box(False, spacing=12)
    title = label(caption, "muted", True)
    title.setFixedWidth(84)
    title.setBuddy(control)
    control.setMaximumWidth(width)
    control.setMinimumWidth(min(width, 180))
    layout.addWidget(title)
    layout.addWidget(control, 1)
    layout.addStretch()
    return row


class SettingsDialog(ModalDialog):
    def __init__(self, window):
        super().__init__(window)
        self.owner = window
        self.state = window.state
        self.sound_combos = {}
        self.setWindowTitle("RivenLens settings")
        # Settings stays comfortably readable when the companion is made smaller.
        self.setStyleSheet(
            theme_for(1.08)
            + "QLabel#settingsCardTitle { padding: 9px 12px; font-size: 14px; "
            "font-weight: 600; color: #87d4fa; }"
            + "QLabel#heading { font-size: 20px; }"
        )
        self.setMinimumSize(480, 400)
        area = window.screen().availableGeometry()
        self.resize(min(550, area.width()), min(690, area.height() - 40))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)
        header = QHBoxLayout()
        header.addWidget(label("Settings", "heading"), 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        header.addWidget(close)
        layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content, rows = box(spacing=16)
        rows.setContentsMargins(0, 0, 10, 0)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        pin_card = SettingsCard("Always on top")
        pin = QCheckBox("Enable overlay-style window")
        pin.setChecked(self.state["alwaysOnTop"])
        pin.toggled.connect(window.set_topmost)
        pin_card.content.addWidget(pin)
        pin_card.content.addWidget(
            label(
                "For single-monitor setups, or if you prefer everything on the same "
                "monitor. Keeps RivenLens visible over other windows like an overlay, "
                "without attaching to or interacting with Warframe. Grading still "
                "uses local screen OCR only.",
                "muted",
                True,
            )
        )
        pin_card.content.addWidget(
            label(
                "Keep the Riven cards uncovered. Use windowed or borderless mode; "
                "exclusive fullscreen may hide this window.",
                "muted",
                True,
            )
        )
        rows.addWidget(pin_card)
        for title, channel, warning in (
            ("Good Ding Sound", window.good_sound, False),
            ("Warning BAD Ding Sound", window.warning_sound, True),
        ):
            section = SettingsCard(title)
            rows.addWidget(section)
            if warning:
                enabled = QCheckBox("Warn when new-roll stat lines cannot be read")
                enabled.setChecked(self.state["ocrWarningEnabled"])
                enabled.toggled.connect(self.warning_changed)
                section.content.addWidget(enabled)
                section.content.addWidget(
                    label(
                        "Stat lines only. Missing rank pips or variant labels do not trigger this warning.",
                        "muted",
                        True,
                    )
                )
            preset = Combo()
            choices = [(p["name"], p["id"]) for p in channel.presets]
            if channel.custom is not None:
                choices.append(("Custom sound", "custom"))
            options(preset, choices, channel.preferences.get("soundId"))
            preset.activated.connect(
                lambda _, c=channel, combo=preset: self.select_sound(
                    c, combo.currentData()
                )
            )
            section.content.addWidget(field_row("Sound", preset))
            volume_row, volume_layout = box(False)
            volume = QSlider(Qt.Orientation.Horizontal)
            volume.setRange(0, 100)
            volume.setValue(int(channel.preferences.get("soundVolume", 50)))
            percent = label(f"{volume.value()}%", "muted")
            percent.setFixedWidth(32)
            volume.valueChanged.connect(
                lambda n, c=channel, text=percent: self.volume_changed(c, text, n)
            )
            volume.sliderReleased.connect(channel.play)
            volume_layout.addWidget(volume, 1)
            volume_layout.addWidget(percent)
            section.content.addWidget(field_row("Volume", volume_row))
            custom = QPushButton("Choose audio file…")
            custom.clicked.connect(lambda _, c=channel: self.choose_audio(c))
            section.content.addWidget(field_row("Custom sound", custom, 180))
            message = label("WAV, MP3 or OGG · up to 10 MB / 30 seconds", "muted", True)
            section.content.addWidget(message)
            channel.message.connect(message.setText)
            self.sound_combos[channel] = preset
            channel.custom_loaded.connect(self.custom_loaded)
        grading = SettingsCard("Grading")
        rows.addWidget(grading)
        rank = Combo()
        options(
            rank,
            [
                ("Manual rank 8/8", 8),
                ("Auto-detect rank pips", "auto"),
            ],
            self.state["rank"] if self.state["rankMode"] == "manual" else "auto",
        )
        rank.activated.connect(lambda: self.rank_changed(rank.currentData()))
        grading.content.addWidget(field_row("Riven rank", rank))
        self.fallback = Combo()
        self.fallback_group_id = ""
        window.planner.changed.connect(self.fill_fallback)
        self.fallback.activated.connect(self.fallback_changed)
        grading.content.addWidget(field_row("Variant fallback", self.fallback))
        grading.content.addWidget(
            label(
                "Uses the weapon selected in the main interface. The visible Fits In "
                "caption takes priority; this fallback is for unreadable or custom-named weapons.",
                "muted",
                True,
            )
        )
        self.fill_fallback()
        capture = SettingsCard("Capture")
        rows.addWidget(capture)
        config = self.state["capture"]
        self.backend = Combo()
        options(
            self.backend,
            [
                ("Automatic", "auto"),
                ("Desktop duplication", "dxgi"),
                ("Compatibility", "gdi"),
            ],
            config["backend"],
        )
        capture.content.addWidget(field_row("Method", self.backend))
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.1, 5)
        self.interval.setSingleStep(0.1)
        self.interval.setDecimals(2)
        self.interval.setSuffix(" sec")
        self.interval.setValue(config["interval"])
        capture.content.addWidget(field_row("Scan interval", self.interval, 110))
        self.contrast = QCheckBox("Boost card-text contrast")
        self.contrast.setChecked(config["contrast"])
        capture.content.addWidget(self.contrast)
        capture.content.addWidget(
            label(
                "Capture area, as a percentage of the selected monitor", "muted", True
            )
        )
        # Keep the four fields together so their shared percentage coordinates are clear.
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.region = []
        for i, caption in enumerate(("Left", "Top", "Width", "Height")):
            spin = QSpinBox()
            spin.setRange(0 if i < 2 else 10, 100)
            spin.setSuffix("%")
            spin.setValue(config["region"][i])
            spin.setMaximumWidth(84)
            grid.addWidget(label(caption, "muted"), 0, i)
            grid.addWidget(spin, 1, i)
            self.region.append(spin)
        capture.content.addLayout(grid)
        self.capture_error = label("", "muted", True)
        capture.content.addWidget(self.capture_error)
        apply = QPushButton("Apply capture settings")
        apply.clicked.connect(self.apply_capture)
        capture.content.addWidget(apply, 0, Qt.AlignmentFlag.AlignLeft)
        rows.addStretch()

    def warning_changed(self, enabled):
        self.state["ocrWarningEnabled"] = enabled
        self.owner.persist()
        self.owner.good_sound.stop()
        self.owner.warning_sound.play() if enabled else self.owner.warning_sound.stop()

    def select_sound(self, channel, identity):
        channel.preferences["soundId"] = identity
        self.owner.persist()
        self.owner.stop_sounds()
        channel.play()

    def volume_changed(self, channel, text, value):
        channel.preferences["soundVolume"] = value
        text.setText(f"{value}%")
        channel.stop()
        self.owner.persist()

    def choose_audio(self, channel):
        path, _ = QFileDialog.getOpenFileName(
            self,
            translate("Choose an alert sound"),
            "",
            "Audio (*.wav *.mp3 *.ogg *.flac *.m4a *.aac);;All files (*)",
        )
        if path:
            channel.load_custom(path)

    def custom_loaded(self, _):
        channel = self.sender()
        combo = self.sound_combos[channel]
        options(
            combo,
            [
                *((p["name"], p["id"]) for p in channel.presets),
                ("Custom sound", "custom"),
            ],
            "custom",
        )
        self.owner.persist()

    def rank_changed(self, value):
        self.state["rankMode"] = "auto" if value == "auto" else "manual"
        self.state["rank"] = 8
        self.owner.apply_capture()
        self.owner.planner.render()
        self.owner.persist()

    def fill_fallback(self):
        group = self.owner.catalog.group(
            {"weapon": self.owner.planner.model.weapon["name"]}
        )
        self.fallback_group_id = group["id"]
        self.fallback.setToolTip(f"Grading fallback for {group['name']}")
        options(
            self.fallback,
            [
                ("Auto-detect variant", ""),
                *((variant_label(v), v["id"]) for v in group["variants"]),
            ],
            self.state["gradeVariants"].get(group["id"], ""),
        )

    def fallback_changed(self):
        self.state["gradeVariants"][self.fallback_group_id] = (
            self.fallback.currentData()
        )
        self.owner.persist()
        self.owner.refresh_grades()

    def apply_capture(self):
        region = [spin.value() for spin in self.region]
        if region[0] + region[2] > 100 or region[1] + region[3] > 100:
            self.capture_error.setText("The capture area must stay inside the monitor.")
            return
        self.state["capture"].update(
            backend=self.backend.currentData(),
            interval=self.interval.value(),
            contrast=self.contrast.isChecked(),
            region=region,
        )
        self.owner.apply_capture()
        self.owner.persist()
        self.capture_error.setText("Capture settings applied.")
