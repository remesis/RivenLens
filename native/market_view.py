# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Owned seed-listing dialog; all purchases remain on Warframe Market."""

import copy
import html
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtWidgets import QApplication, QHBoxLayout, QTextBrowser, QVBoxLayout

from dialogs import ModalDialog
from grading import GRADE_COLORS
from market import AGE_CHOICES
from planner import Planner
from ui_text import QPushButton, translate
from widgets import Combo, label, number, options


class SeedDialog(ModalDialog):
    def __init__(self, owner, client):
        super().__init__(owner)
        self.owner, self.client = owner, client
        self.model = Planner(owner.catalog, copy.deepcopy(owner.state))
        self.matcher, self.listings = None, []
        self._closed = False
        self._window_ready = False
        self._restore_pending = True
        self.setWindowTitle("Purchase Ideal Starting Seed")
        self.resize(640, 560)
        bounds = owner.state.get("seedWindowBounds", [])
        if bounds:
            self.resize(*bounds[2:])
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.age = Combo()
        options(self.age, AGE_CHOICES, owner.state.get("seedListingAge", "30d"))
        self.age.activated.connect(self.age_changed)
        header.addWidget(self.age)
        header.addStretch()
        self.refresh = QPushButton("Refresh")
        self.refresh.clicked.connect(lambda: self.load(refresh=True))
        header.addWidget(self.refresh)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        header.addWidget(close)
        layout.addLayout(header)
        caption = f"{self.model.variant['name']} • {self.model.state['format']}"
        if self.model.splice:
            caption += (
                f" • ≥{self.model.state['spliceGrade']} {self.model.name(self.model.splice)}"
                f" ({self.model.recipe_text(self.model.splice)})"
            )
        elif self.model.lock_target:
            target = self.model.lock_target
            sign = "− " if target["polarity"] == "negative" else ""
            caption += f" • ≥{self.model.state['lockGrade']} {sign}{self.model.name(target['id'])}"
        layout.addWidget(label(caption, "heading", True))
        self.status = label("", "muted", True)
        layout.addWidget(self.status)
        self.results = QTextBrowser()
        self.results.setOpenExternalLinks(True)
        self.results.setObjectName("seedListings")
        self.platinum = QImage(str(Path(__file__).parent / "data/platinum.png"))
        layout.addWidget(self.results, 1)
        layout.addWidget(
            label(
                "PC + cross-platform listings (no Switch) · Lowest listed price; auction bids may cost more.",
                "muted",
                True,
            )
        )
        layout.addWidget(
            label(
                "Market may limit results. Seller-entered values are graded at the listed rank using Market’s base-weapon disposition.",
                "muted",
                True,
            )
        )
        self.client.completed.connect(self.loaded)
        self.client.failed.connect(self.failed)
        self.client.progress.connect(self.status.setText)
        self.finished.connect(self.cleanup)
        QTimer.singleShot(0, self, self.load)

    def showEvent(self, event):
        super().showEvent(event)
        if self._restore_pending:
            self._restore_pending = False
            QTimer.singleShot(0, self, self.restore_window)

    def restore_window(self):
        if self._closed or not self.isVisible():
            return
        bounds = self.owner.state.get("seedWindowBounds", [])
        if bounds:
            # Apply frame coordinates after the native title bar has been created.
            self.move(*bounds[:2])
        frame = self.frameGeometry()
        screen = QApplication.screenAt(frame.center()) or self.parentWidget().screen()
        area = screen.availableGeometry()
        if not area.contains(frame):
            self.resize(
                min(self.width(), area.width() - (frame.width() - self.width())),
                min(self.height(), area.height() - (frame.height() - self.height())),
            )
            frame = self.frameGeometry()
            self.move(
                max(area.left(), min(self.x(), area.right() + 1 - frame.width())),
                max(area.top(), min(self.y(), area.bottom() + 1 - frame.height())),
            )
        self._window_ready = True
        self.remember_window()

    def remember_window(self):
        if self._window_ready and not (
            self.isMinimized() or self.isMaximized() or self.isFullScreen()
        ):
            bounds = [self.x(), self.y(), self.width(), self.height()]
            if bounds != self.owner.state.get("seedWindowBounds"):
                self.owner.state["seedWindowBounds"] = bounds
                self.owner.changed.emit()

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.isVisible():
            self.remember_window()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible():
            self.remember_window()

    def load(self, refresh=False):
        if self._closed:
            return
        self.refresh.setEnabled(False)
        self.matcher = None
        self.clear_rows()
        self.status.setText("Searching Warframe Market…")
        if refresh:
            self.client.start(self.model, refresh=True)
        else:
            self.client.start(self.model)

    def cleanup(self):
        self.remember_window()
        self._closed = True
        self.client.cancel()
        self.client.completed.disconnect(self.loaded)
        self.client.failed.disconnect(self.failed)
        self.client.progress.disconnect(self.status.setText)

    def loaded(self, matcher, listings):
        self.matcher, self.listings = matcher, listings
        self.refresh.setEnabled(True)
        self.render()

    def failed(self, message):
        self.refresh.setEnabled(True)
        self.status.setText(message)

    def age_changed(self):
        self.owner.state["seedListingAge"] = self.age.currentData()
        self.owner.changed.emit()
        self.render()

    def clear_rows(self):
        self.results.clear()

    def render(self):
        if self.matcher is None:
            return
        self.clear_rows()
        sections = self.matcher.sections(self.listings, self.age.currentData())
        self.status.setText(
            "Search complete · "
            + " · ".join(
                f"Stage {i + 1}: {len(rows)} listings"
                for i, rows in enumerate(sections)
            )
            + "\nListings cached for 5 minutes. Age uses the original creation date."
        )
        titles = (
            ["Getting Optimal Splice", "Getting Selected Lock Stat"]
            if self.model.splice
            else ["Getting Selected Lock Stat"]
        )
        content = []
        height = self.results.fontMetrics().height()
        width = round(height * self.platinum.width() / self.platinum.height())
        grade_size = self.results.fontInfo().pixelSize() * 1.2
        for index, entries in enumerate(sections):
            title = html.escape(translate(titles[index]))
            content.append(f'<h3 style="color:#80d4fc">{index + 1}. {title}</h3>')
            if not entries:
                content.append(
                    '<p style="color:#8dabc0">'
                    + html.escape(translate("No matching listings found."))
                    + "</p>"
                )
            for entry in entries:
                url = "https://warframe.market/auction/" + entry["id"]
                stats = []
                for stat in entry["stats"]:
                    color = GRADE_COLORS[stat["grade"]]
                    name = html.escape(translate(stat["name"]))
                    sign = "− " if stat["polarity"] == "negative" else ""
                    stats.append(
                        f'<span style="color:{color};font-size:{grade_size:g}px;font-weight:600">{stat["grade"]}</span> {sign}{name}'
                    )
                detail = (
                    f'{html.escape(number(entry["price"], 0))} <img src="rivenlens:platinum" width="{width}" height="{height}" style="vertical-align:middle"> - {html.escape(entry["format"])} - '
                    + ", ".join(stats)
                )
                content.append(
                    f'<p><a href="{url}" style="color:#80d4fc">{url}</a><br>{detail}</p>'
                )
        self.results.setHtml("".join(content))
        self.results.document().addResource(
            QTextDocument.ResourceType.ImageResource,
            QUrl("rivenlens:platinum"),
            self.platinum,
        )
