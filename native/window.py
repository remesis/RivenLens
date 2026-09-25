# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See docs/LICENSE.txt for the license and warranty disclaimer.

"""RivenLens desktop window. No embedded browser, HTTP server or web socket."""

from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from alerts import History, find_matches
from appearance import theme_for, window_scale
from audio import SoundChannel
from catalog import Catalog
from planner_view import PlannerView
from settings_view import SettingsDialog
from widgets import (
    Combo,
    Hyperlink,
    RichLabel,
    RollCard,
    Section,
    box,
    label,
    options,
    rich,
)
from worker import CaptureWorker

ICON = Path(__file__).resolve().parent / "data" / "arbi-logo.png"
DEFAULT_WINDOW_SIZE = (550, 750)
WINDOW_LAYOUT_VERSION = 1


class MainWindow(QWidget):
    def __init__(self, preferences, worker_factory=CaptureWorker):
        super().__init__()
        self.setObjectName("windowSurface")
        self.setWindowTitle("RivenLens")
        self.setWindowIcon(QIcon(str(ICON)))
        self.preferences, self.state = preferences, preferences.state
        self.catalog = Catalog()
        self.cards = []
        self.running = False
        self.revision = 0
        self._closing = False
        self._settings = None
        self._ui_scale = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(35)
        self._resize_timer.timeout.connect(self.update_scale)
        self._balance_timer = QTimer(self)
        self._balance_timer.setSingleShot(True)
        self._balance_timer.setInterval(0)
        self._balance_timer.timeout.connect(self.balance_layout)
        self.match_history, self.warning_history = History(), History()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self.save_now)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(8)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)
        logo = label()
        self.logo = logo
        self.logo_image = QPixmap(str(ICON))
        logo.setPixmap(
            QPixmap(str(ICON)).scaled(
                25,
                25,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        toolbar.addWidget(logo)
        toolbar.addWidget(rich('Riven<span style="color:#bd9ae6">Lens</span>', "brand"))
        self.monitor = Combo()
        self.monitor.activated.connect(self.monitor_changed)
        toolbar.addWidget(self.monitor, 1)
        self.start = QPushButton("Start OCR")
        self.start.setObjectName("primary")
        self.start.setEnabled(False)
        self.start.clicked.connect(self.toggle_capture)
        toolbar.addWidget(self.start)
        settings = QPushButton("Settings")
        settings.clicked.connect(self.open_settings)
        toolbar.addWidget(settings)
        layout.addLayout(toolbar)
        heading = QHBoxLayout()
        heading.addWidget(label("Live grades", "heading"), 1)
        self.variant_label = label("", "variant")
        self.variant_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.addWidget(self.variant_label, 1)
        self.status = label("Capture paused", "status")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight)
        heading.addWidget(self.status, 1)
        layout.addLayout(heading)
        cards, card_layout = box(False, spacing=9)
        self.card_area = cards
        self.current_card, self.new_card = (
            RollCard("CURRENT ROLL"),
            RollCard("NEW ROLL", True),
        )
        card_layout.addWidget(self.current_card, 1)
        card_layout.addWidget(self.new_card, 1)
        cards.setFixedHeight(208)
        layout.addWidget(cards)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.planner = PlannerView(self.catalog, self.state)
        self.planner.changed.connect(self.persist)
        self.planner.sound_toggled.connect(self.sound_toggled)
        self.planner.calculator.finished.connect(self.try_close)
        self.planner.calculator.completed.connect(self.schedule_balance)
        self.planner.toggled.connect(self.schedule_balance)
        for section in self.planner.findChildren(Section):
            section.toggled.connect(self.schedule_balance)
        wrapper, wrapper_layout = box(spacing=0)
        wrapper_layout.addWidget(self.planner)
        wrapper_layout.addStretch()
        scroll.setWidget(wrapper)
        layout.addWidget(scroll, 2)
        self.planner_scroll = scroll
        footer = QHBoxLayout()
        for caption, url in (
            ("discord.gg/Arbitrations", "https://discord.gg/Arbitrations"),
            ("https://arbi.guide/", "https://arbi.guide/"),
        ):
            link = Hyperlink(caption, url)
            link.linkActivated.connect(
                lambda _, target=url: QDesktopServices.openUrl(QUrl(target))
            )
            footer.addWidget(link)
            if caption.startswith("discord"):
                footer.addStretch()
        layout.addLayout(footer)
        self.good_sound = SoundChannel(
            self.state, preferences.directory / "sounds", parent=self
        )
        self.warning_sound = SoundChannel(
            self.state["ocrWarningAudio"],
            preferences.directory / "sounds",
            warning=True,
            parent=self,
        )
        self.good_sound.message.connect(self.audio_message)
        self.warning_sound.message.connect(self.audio_message)
        self.good_sound.custom_loaded.connect(self.persist)
        self.warning_sound.custom_loaded.connect(self.persist)
        self.worker = worker_factory(self.state["capture"], self)
        self.worker.updated.connect(self.receive)
        self.worker.displays.connect(self.displays_changed)
        self.worker.finished.connect(self.try_close)
        self.worker.start()
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip("RivenLens")
            menu = QMenu(self)
            for title, callback in (
                ("Show RivenLens", self.showNormal),
                ("Start / pause OCR", self.toggle_capture),
                ("Quit", self.close),
            ):
                action = QAction(title, self)
                action.triggered.connect(callback)
                menu.addAction(action)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(
                lambda reason: (
                    self.showNormal()
                    if reason == QSystemTrayIcon.ActivationReason.DoubleClick
                    else None
                )
            )
            self.tray.show()
        self.resize(*DEFAULT_WINDOW_SIZE)
        self.setMinimumSize(500, 500)
        restored = False
        if self.state["geometry"]:
            restored = self.restoreGeometry(
                QByteArray.fromBase64(
                    self.state["geometry"].encode("ascii", errors="ignore")
                )
            )
        self._initial_size_pending = (
            not restored or self.state["windowLayoutVersion"] < WINDOW_LAYOUT_VERSION
        )
        if self._initial_size_pending:
            self.setWindowState(Qt.WindowState.WindowNoState)
            self.resize(*DEFAULT_WINDOW_SIZE)
        self.state["windowLayoutVersion"] = WINDOW_LAYOUT_VERSION
        self.keep_on_screen()
        self.setWindowFlag(
            Qt.WindowType.WindowStaysOnTopHint, self.state["alwaysOnTop"]
        )
        if preferences.error:
            self.status.setToolTip(preferences.error)
        self.update_scale()

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_size_pending:
            self._initial_size_pending = False
            QTimer.singleShot(0, self.set_initial_size)

    def set_initial_size(self):
        # Keep the initial outer size consistent, including the native title bar.
        frame = self.frameGeometry()
        border_width = max(0, frame.width() - self.width())
        border_height = max(0, frame.height() - self.height())
        self.resize(
            DEFAULT_WINDOW_SIZE[0] - border_width,
            DEFAULT_WINDOW_SIZE[1] - border_height,
        )
        self.keep_on_screen()
        self.update_scale()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Coalesce layout work during a drag without waiting for the drag to end.
        if not self._resize_timer.isActive():
            self._resize_timer.start()

    def update_scale(self):
        scale = window_scale(self.width(), self.height())
        if scale == self._ui_scale:
            self.schedule_balance()
            return
        self._ui_scale = scale
        self.setUpdatesEnabled(False)
        try:
            self.setStyleSheet(theme_for(scale))
            self.layout().setContentsMargins(
                round(10 * scale),
                round(10 * scale),
                round(10 * scale),
                round(8 * scale),
            )
            self.layout().setSpacing(round(8 * scale))
            self.logo.setPixmap(
                self.logo_image.scaled(
                    round(25 * scale),
                    round(25 * scale),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            for widget in self.findChildren(QWidget):
                if widget.window() is self and isinstance(
                    widget, (RichLabel, Section, RollCard)
                ):
                    widget.set_ui_scale(scale)
            self.card_area.setFixedHeight(round(208 * scale))
            self.card_area.layout().setSpacing(round(9 * scale))
        finally:
            self.setUpdatesEnabled(True)
        self.schedule_balance()

    def schedule_balance(self, *_):
        if not self._closing:
            self._balance_timer.start()

    def balance_layout(self):
        # Let compact cards give space back to the planner before adding a scrollbar.
        self.layout().activate()
        self.planner_scroll.widget().layout().activate()
        planner_height = self.planner.height()
        spare = self.planner_scroll.viewport().height() - planner_height
        preferred = round(208 * self._ui_scale)
        minimum = max(
            round(164 * self._ui_scale),
            self.current_card.minimumSizeHint().height(),
            self.new_card.minimumSizeHint().height(),
        )
        height = max(minimum, min(preferred, self.card_area.height() + spare))
        if height != self.card_area.height():
            self.card_area.setFixedHeight(height)

    def keep_on_screen(self):
        from PySide6.QtWidgets import QApplication

        screens = QApplication.screens()
        geometry = self.frameGeometry()
        screen = next(
            (s for s in screens if s.availableGeometry().intersects(geometry)),
            QApplication.primaryScreen(),
        )
        area = screen.availableGeometry()
        border_width = max(0, geometry.width() - self.width())
        border_height = max(0, geometry.height() - self.height())
        self.resize(
            min(self.width(), area.width() - border_width),
            min(self.height(), area.height() - border_height),
        )
        geometry = self.frameGeometry()
        self.move(
            max(area.left(), min(self.x(), area.right() + 1 - geometry.width())),
            max(area.top(), min(self.y(), area.bottom() + 1 - geometry.height())),
        )

    def persist(self, *_):
        self._save_timer.start()

    def save_now(self):
        try:
            self.preferences.save()
        except OSError as exc:
            self.status.setText("Settings not saved")
            self.status.setToolTip(str(exc))

    def displays_changed(self, monitors, config):
        if self._closing or config["revision"] < self.revision:
            return
        self.revision = config["revision"]
        self.running = config["running"]
        self.state["capture"]["monitor"] = config["monitor"]
        options(
            self.monitor,
            (
                (f"Monitor {i + 1} · {m['width']} × {m['height']}", i + 1)
                for i, m in enumerate(monitors)
            ),
            config["monitor"],
        )
        self.start.setEnabled(bool(monitors))
        self.update_start()

    def monitor_changed(self):
        self.state["capture"]["monitor"] = self.monitor.currentData()
        self.apply_capture()
        self.persist()

    def toggle_capture(self):
        if self._closing or not self.start.isEnabled():
            return
        self.running = not self.running
        self.apply_capture()

    def apply_capture(self):
        self.revision = self.worker.configure(self.state["capture"], self.running)
        self.stop_sounds()
        self.cards = []
        self.refresh_grades()
        self.new_card.set_warning(False)
        self.status.setText("Waiting for a Riven" if self.running else "Capture paused")
        self.update_start()

    def update_start(self):
        self.start.setText("Pause OCR" if self.running else "Start OCR")
        self.start.setProperty("running", self.running)
        self.start.style().unpolish(self.start)
        self.start.style().polish(self.start)

    def receive(self, state):
        if self._closing or state["revision"] != self.revision:
            return
        self.cards = state.get("cards", [])
        self.refresh_grades()
        warning = (
            state.get("newRollWarning")
            if self.running and not state.get("error")
            else None
        )
        self.new_card.set_warning(
            bool(warning),
            warning.get("message", "Check the new roll's stat lines")
            if warning
            else "",
        )
        if warning:
            if (
                self.warning_history.take([warning["id"]])
                and self.state["ocrWarningEnabled"]
            ):
                self.good_sound.stop()
                self.warning_sound.play()
        elif self.running and state.get("status") == "reading":
            matches = find_matches(self.cards, self.catalog, self.state)
            if self.match_history.take(matches):
                self.warning_sound.stop()
                self.good_sound.play()
        text = (
            "OCR unavailable"
            if state.get("error")
            else "Check new roll stats"
            if warning
            else "Capture paused"
            if not self.running
            else "Watching for rolls"
            if self.cards
            else "Waiting for a Riven"
        )
        self.status.setText(text)
        self.status.setProperty("warning", bool(warning or state.get("error")))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setToolTip(
            " · ".join(
                str(v)
                for v in [
                    state.get("error")
                    or (warning or {}).get("message")
                    or state.get("message"),
                    f"{state['scanMs']} ms / scan"
                    if state.get("scanMs") is not None
                    else None,
                    state.get("captureBackend"),
                ]
                if v
            )
        )

    def refresh_grades(self):
        current = self.cards[0] if self.cards else None
        new = self.cards[1] if len(self.cards) > 1 else None
        self.current_card.show_card(current, self.catalog, self.state)
        self.new_card.show_card(new, self.catalog, self.state)
        card = new or current
        variant = (
            self.catalog.variant(card, self.state["gradeVariants"]) if card else None
        )
        self.variant_label.setText(variant["name"] if variant else "")
        self.variant_label.setToolTip(
            f"{variant['source']} · disposition {variant['disposition']}"
            if variant
            else ""
        )

    def sound_toggled(self, enabled):
        self.stop_sounds()
        if enabled:
            self.good_sound.play()

    def stop_sounds(self):
        self.good_sound.stop()
        self.warning_sound.stop()

    def audio_message(self, message):
        self.status.setToolTip(message)

    def set_topmost(self, enabled):
        self.state["alwaysOnTop"] = enabled
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()
        self.persist()

    def open_settings(self):
        if self._settings:
            self._settings.show()
            return
        self._settings = SettingsDialog(self)
        self._settings.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._settings.finished.connect(lambda *_: setattr(self, "_settings", None))
        self._settings.show()

    def try_close(self):
        if (
            self._closing
            and not self.worker.isRunning()
            and not self.planner.calculator.isRunning()
        ):
            self.close()

    def closeEvent(self, event):
        if not self._closing:
            self._closing = True
            if hasattr(self, "updates"):
                self.updates.shutdown()
            self.running = False
            self.good_sound.close()
            self.warning_sound.close()
            self._save_timer.stop()
            self._resize_timer.stop()
            self._balance_timer.stop()
            self.state["geometry"] = bytes(self.saveGeometry().toBase64()).decode(
                "ascii"
            )
            self.save_now()
            self.worker.shutdown()
            self.planner.calculator.shutdown()
            self.start.setEnabled(False)
            self.status.setText("Closing…")
        if self.worker.isRunning() or self.planner.calculator.isRunning():
            event.ignore()
            return
        if self.tray:
            self.tray.hide()
        event.accept()
