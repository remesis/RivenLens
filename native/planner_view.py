# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Three-stage native planner with nonblocking exact calculations."""

import copy
import html
import math
import threading

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from catalog import SPLICES, SPLICE_IDS, splices_for, variant_label
from dialogs import ModalDialog
from grading import FORMATS, GRADE_NAMES, format_range
from odds import attempts_for
from planner import Planner
from search_combo import SearchCombo
from ui_text import QCheckBox, QFrame, QPushButton, language
from widgets import (
    Combo,
    LockButton,
    Section,
    StatCombo,
    box,
    interval,
    label,
    odds_text,
    options,
    rich,
)

STAGE_TITLES = {
    "splice": "Getting Optimal Splice",
    "lock": "Getting Selected Lock Stat",
    "final": "Final Target Roll Odds",
}


class Calculator(QThread):
    completed = Signal(int, object)

    def __init__(self, catalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self._pending = None
        self._guard = threading.Condition()
        self._stopping = False

    def submit(self, generation, state):
        with self._guard:
            self._pending = generation, copy.deepcopy(state)
            self._guard.notify()

    def shutdown(self):
        with self._guard:
            self._stopping = True
            self._guard.notify()

    def run(self):
        planner = None
        while True:
            with self._guard:
                self._guard.wait_for(
                    lambda: self._stopping or self._pending is not None
                )
                if self._stopping:
                    return
                generation, state = self._pending
                self._pending = None
            try:
                if planner is None:
                    planner = Planner(self.catalog, state)
                else:
                    planner.state = state
                    planner.normalize()
                result = planner.calculate()
            except Exception as exc:
                result = {"error": str(exc)}
            self.completed.emit(generation, result)


class PlannerView(Section):
    changed = Signal()
    sound_toggled = Signal(bool)

    def __init__(self, catalog, state, parent=None):
        super().__init__("Target roll planner", state["plannerOpen"], parent)
        self.catalog, self.state = catalog, state
        self.model = Planner(catalog, state)
        self.generation = 0
        self.calculator = Calculator(catalog, self)
        self.calculator.completed.connect(self.show_results)
        self.calculator.start()
        self.toggled.connect(lambda value: self.save_value("plannerOpen", value, False))
        columns, column_layout = box(False, spacing=10)
        self.content.addWidget(columns)
        left, left_layout = box(spacing=6)
        self.left_column = left
        left.setMinimumWidth(180)
        column_layout.addWidget(left, 4)
        right, right_layout = box(spacing=5)
        self.right_column = right
        right.setMinimumWidth(270)
        column_layout.addWidget(right, 6)
        fields = QWidget()
        grid = QGridLayout(fields)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(5)
        self.category, self.format, self.variant = (Combo() for _ in range(3))
        self.weapon = SearchCombo()
        for caption, field, row, col in (
            ("Category", self.category, 0, 0),
            ("Format", self.format, 0, 1),
            ("Weapon", self.weapon, 2, 0),
            ("Variant", self.variant, 2, 1),
        ):
            grid.addWidget(label(caption, "muted"), row, col)
            grid.addWidget(field, row + 1, col)
        self.weapon.activated.connect(self.choose_weapon)
        self.category.activated.connect(self.choose_category)
        self.variant.activated.connect(
            lambda: self.save_value("variant", self.variant.currentData())
        )
        self.format.activated.connect(
            lambda: self.save_value("format", self.format.currentData())
        )
        left_layout.addWidget(fields)
        divider = QFrame()
        divider.setObjectName("plannerDivider")
        divider.setFixedHeight(1)
        left_layout.addWidget(divider)
        self.positive_rows, self.positives = [], []
        self.stat_captions, self.stat_ranges = [], []
        self.stat_locks = []
        for index in range(3):
            container, container_layout = box(spacing=3)
            row, row_layout = box(False, spacing=5)
            caption = label("+", "statCaption")
            caption.setFixedWidth(12)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.stat_captions.append(caption)
            selector = StatCombo()
            selector.setAccessibleName(f"Positive {index + 1}")
            selector.activated.connect(lambda _, i=index: self.choose_positive(i))
            row_layout.addWidget(caption)
            row_layout.addWidget(selector, 1)
            row_layout.addWidget(self.make_stat_lock(index))
            container_layout.addWidget(row)
            container_layout.addWidget(self.make_stat_range())
            left_layout.addWidget(container)
            self.positive_rows.append(container)
            self.positives.append(selector)
        self.negative_row, negative_layout = box(spacing=3)
        neg_fields, neg_layout = box(False, spacing=5)
        negative_caption = label("−", "statCaption")
        negative_caption.setFixedWidth(12)
        negative_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stat_captions.append(negative_caption)
        self.negative = StatCombo()
        self.negative.setAccessibleName("Negative")
        self.negative.activated.connect(self.choose_negative)
        neg_layout.addWidget(negative_caption)
        neg_layout.addWidget(self.negative, 1)
        neg_layout.addWidget(self.make_stat_lock(3))
        negative_layout.addWidget(neg_fields)
        negative_layout.addWidget(self.make_stat_range())
        left_layout.addWidget(self.negative_row)
        self.eligibility_legend, legend_layout = box(spacing=3)
        self.vintage_legend = label("Vintage / not rollable", "muted")
        self.research_legend = label("Eligibility under research", "muted")
        for caption, color in (
            (self.vintage_legend, "#ff757b"),
            (self.research_legend, "#dfb96b"),
        ):
            row, row_layout = box(False, spacing=6)
            row_layout.addWidget(
                rich(f'<span style="color:{color};font-size:12px">●</span>')
            )
            row_layout.addWidget(caption)
            row_layout.addStretch()
            legend_layout.addWidget(row)
        left_layout.addWidget(self.eligibility_legend)
        left_layout.addStretch()
        self.stages = {}
        for key, title in STAGE_TITLES.items():
            stage = Section(title, state["stagesOpen"].get(key, True))
            stage.toggled.connect(lambda value, k=key: self.stage_toggled(k, value))
            right_layout.addWidget(stage)
            self.stages[key] = stage
        right_layout.addStretch()
        self.splice_grade, self.lock_grade = Combo(), Combo()
        self.splice_ding, self.lock_ding = QCheckBox("Ding"), QCheckBox("Ding")
        self.watch = QPushButton("Watch splices (0)")
        self.watch.clicked.connect(self.watch_dialog)
        for key, combo, ding in (
            ("splice", self.splice_grade, self.splice_ding),
            ("lock", self.lock_grade, self.lock_ding),
        ):
            combo.setFixedWidth(65)
            combo.activated.connect(
                lambda _, k=key, c=combo: self.save_value(k + "Grade", c.currentData())
            )
            ding.toggled.connect(lambda value, k=key: self.ding_toggled(k, value))
            toolbar, toolbar_layout = box(False, spacing=6)
            toolbar_layout.addWidget(combo)
            if key == "splice":
                toolbar_layout.addWidget(self.watch)
            toolbar_layout.addStretch()
            toolbar_layout.addWidget(ding)
            self.stages[key].content.addWidget(toolbar)
        self.splice_info = rich(wrap=True)
        self.splice_metrics = rich(wrap=True)
        self.splice_note = label("", "muted", True)
        for item in (self.splice_info, self.splice_metrics, self.splice_note):
            self.stages["splice"].content.addWidget(item)
        self.lock_choices = Section(
            "Optimal starting locks", state["startingLocksOpen"]
        )
        self.lock_choices.toggled.connect(
            lambda value: self.save_value("startingLocksOpen", value, False)
        )
        self.lock_names = rich(wrap=True)
        self.lock_choices.content.addWidget(self.lock_names)
        self.stages["splice"].content.addWidget(self.lock_choices)
        self.lock_info, self.lock_metrics = label("", "muted", True), rich(wrap=True)
        self.stages["lock"].content.addWidget(self.lock_info)
        self.stages["lock"].content.addWidget(self.lock_metrics)
        self.final_info = label("", "muted")
        self.final_metrics = rich(wrap=True)
        self.stages["final"].content.addWidget(self.final_info)
        self.stages["final"].content.addWidget(self.final_metrics)
        self.error = label("", "muted", True)
        self.content.addWidget(self.error)
        self.render(auto_lock=True)

    def set_ui_scale(self, scale):
        super().set_ui_scale(scale)
        self.left_column.setMinimumWidth(round(180 * scale))
        self.right_column.setMinimumWidth(round(270 * scale))
        self.left_column.layout().setSpacing(round(6 * scale))
        self.right_column.layout().setSpacing(round(5 * scale))
        for caption in self.stat_captions:
            caption.setFixedWidth(round(12 * scale))
        for value in self.stat_ranges:
            value.setContentsMargins(round(12 * scale) + 5, 0, round(24 * scale) + 5, 0)
        for button in self.stat_locks:
            button.set_ui_scale(scale)
        for combo in (self.splice_grade, self.lock_grade):
            combo.setFixedWidth(round(65 * scale))

    def make_stat_range(self):
        value = label("", "statRange", True)
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value.setContentsMargins(17, 0, 29, 0)
        self.stat_ranges.append(value)
        return value

    def make_stat_lock(self, index):
        button = LockButton()
        button.clicked.connect(lambda: self.choose_lock(index))
        self.stat_locks.append(button)
        return button

    def choose_lock(self, index):
        if index == 3:
            if not self.model.has_negative or self.state["negative"] == "any":
                return
            target = "negative"
        else:
            if index >= len(self.state["positives"]):
                return
            identity = self.state["positives"][index]
            if identity in SPLICE_IDS:
                return
            target = "positive:" + identity
        self.save_value("lock", "none" if self.state["lock"] == target else target)

    def show_stat_lock(self, index, identity, polarity):
        available = identity is not None and identity != "any"
        target = "negative" if polarity == "negative" else f"positive:{identity}"
        self.stat_locks[index].show_lock(
            f"{'+' if polarity == 'positive' else '-'} {self.model.name(identity)}"
            if available
            else "",
            locked=available and self.state["lock"] == target,
            retained=identity in SPLICE_IDS,
            available=available,
        )

    def refresh_stat_row(self, index, identity, polarity):
        self.show_stat_lock(index, identity, polarity)
        value = self.stat_ranges[index]
        selected = identity is not None and identity != "any"
        value.setText(
            format_range(self.model.stat_range(identity, polarity)) if selected else ""
        )
        value.setVisible(selected)
        value.setAccessibleName(
            f"{self.model.name(identity)} range for {self.model.variant['name']}, rank {self.model.rank}"
            if selected
            else ""
        )
        vintage = self.model.is_vintage(identity, polarity)
        uncertain = (
            self.model.family["traits"].get(identity, {}).get(polarity) == "unresolved"
        )
        selector = self.negative if index == 3 else self.positives[index]
        for widget in (selector, value):
            if (
                widget.property("unrollable") != vintage
                or widget.property("uncertain") != uncertain
            ):
                widget.setProperty("unrollable", vintage)
                widget.setProperty("uncertain", uncertain)
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def style_stat_choices(self, selector, polarity):
        for index in range(selector.count()):
            identity = selector.itemData(index)
            special = identity is None or identity == "any" or identity in SPLICE_IDS
            selector.set_eligibility(
                index,
                excluded=not special and not self.model.is_rollable(identity, polarity),
                selectable=special or self.model.is_selectable(identity, polarity),
                vintage=self.model.is_vintage(identity, polarity),
                uncertain=self.model.family["traits"].get(identity, {}).get(polarity)
                == "unresolved",
            )

    def save_value(self, key, value, calculate=True):
        self.state[key] = value
        if calculate:
            self.render(auto_lock=key in ("weapon", "format", "negative"))
        self.changed.emit()

    def stage_toggled(self, key, value):
        self.state["stagesOpen"][key] = value
        self.changed.emit()

    def ding_toggled(self, key, value):
        self.state[key + "Sound"] = value
        self.changed.emit()
        self.sound_toggled.emit(value)

    def choose_category(self):
        self.state["weapon"] = next(
            w["id"]
            for w in self.catalog.weapons
            if w["category"] == self.category.currentData()
        )
        self.render(auto_lock=True)
        self.changed.emit()

    def choose_weapon(self, *_):
        match = next(
            (
                w
                for w in self.catalog.weapons
                if w["category"] == self.category.currentData()
                and w["id"] == self.weapon.currentData()
                and self.weapon.currentText()
                == self.weapon.itemText(self.weapon.currentIndex())
            ),
            None,
        )
        if match:
            if self.state["weapon"] != match["id"]:
                self.save_value("weapon", match["id"])
        else:
            self.weapon.setEditText(self.weapon.itemText(self.weapon.currentIndex()))

    def choose_positive(self, index):
        identity = self.positives[index].currentData()
        if identity is not None and not self.model.is_selectable(identity, "positive"):
            self.render()
            return
        if index == 2:
            positives = self.state["positives"][:2]
            if identity is not None:
                positives.append(identity)
            self.state["format"] = f"{len(positives)}p{int(self.model.has_negative)}n"
            self.state["positives"] = positives
        else:
            self.state["positives"][index] = identity
        if self.model.is_vintage(identity, "positive"):
            self.state["lock"] = "positive:" + identity
        self.render(auto_lock=True)
        self.changed.emit()

    def choose_negative(self):
        identity = self.negative.currentData()
        if identity != "any" and not self.model.is_selectable(identity, "negative"):
            self.render()
            return
        if self.model.is_vintage(identity, "negative"):
            self.state["lock"] = "negative"
        self.save_value("negative", identity)

    def render(self, auto_lock=False):
        self.model.normalize(auto_lock=auto_lock)
        s, model = self.state, self.model
        self.refresh_stages()
        categories = list(dict.fromkeys(w["category"] for w in self.catalog.weapons))
        options(self.category, ((c, c) for c in categories), model.weapon["category"])
        options(
            self.weapon,
            (
                (w["name"], w["id"])
                for w in self.catalog.weapons
                if w["category"] == model.weapon["category"]
            ),
            s["weapon"],
        )
        options(
            self.variant,
            ((variant_label(v), v["id"]) for v in model.weapon["variants"]),
            s["variant"],
        )
        options(self.format, ((fmt, fmt) for fmt in FORMATS), s["format"])
        for index in range(3):
            identity = s["positives"][index] if index < len(s["positives"]) else None
            others = [i for j, i in enumerate(s["positives"]) if j != index]
            choices = [
                t
                for t in model.positive_traits(include_unrollable=True)
                if t["id"] not in others
                and not (t["id"] in SPLICE_IDS and any(i in SPLICE_IDS for i in others))
            ]
            options(
                self.positives[index],
                ([("No Third Positive", None)] if index == 2 else [])
                + [(t["name"], t["id"]) for t in choices],
                identity,
            )
            self.style_stat_choices(self.positives[index], "positive")
            self.refresh_stat_row(index, identity, "positive")
        self.negative_row.setVisible(model.has_negative)
        options(
            self.negative,
            [
                ("Any compatible negative", "any"),
                *(
                    (t["name"], t["id"])
                    for t in model.negative_traits(include_unrollable=True)
                ),
            ],
            s["negative"],
        )
        self.style_stat_choices(self.negative, "negative")
        self.refresh_stat_row(
            3, s["negative"] if model.has_negative else None, "negative"
        )
        for key, combo, ding in (
            ("splice", self.splice_grade, self.splice_ding),
            ("lock", self.lock_grade, self.lock_ding),
        ):
            options(
                combo,
                (("≥ " + grade, grade) for grade in GRADE_NAMES),
                s[key + "Grade"],
            )
            ding.blockSignals(True)
            ding.setChecked(s[key + "Sound"])
            ding.blockSignals(False)
        available = {t["id"] for t in splices_for(model.weapon["kind"])}
        self.watch.setText(f"Watch splices ({len(set(s['spliceWatch']) & available)})")
        self.generation += 1
        # Never leave old odds looking like results for a newly selected target.
        self.final_metrics.setText('<span style="color:#8dabc0">Calculating…</span>')
        self.splice_info.setText(self.splice_description())
        self.splice_metrics.clear()
        self.splice_note.clear()
        self.lock_choices.hide()
        self.lock_info.setText("Calculating…" if model.lock_target else "")
        self.lock_metrics.clear()
        for stage in self.stages.values():
            stage.set_summary("")
        self.calculator.submit(self.generation, s)

    def refresh_stages(self):
        available = {
            "splice": bool(self.model.splice),
            "lock": bool(self.model.lock_target),
            "final": True,
        }
        number = 1
        for key, title in STAGE_TITLES.items():
            stage = self.stages[key]
            stage.setVisible(available[key])
            stage.title.setText(f"{number}. {title}" if available[key] else title)
            if available[key]:
                number += 1

    def splice_description(self):
        identity = self.model.splice
        if not identity:
            return ""
        name = html.escape(self.model.name(identity))
        recipe = self.model.recipe_text(identity)
        return (
            f'{name} <b style="color:#c4a2ef">({html.escape(recipe)})</b>'
            if recipe
            else name
        )

    def show_results(self, generation, result):
        if generation != self.generation:
            return
        if "error" in result:
            self.error.setText("Estimate unavailable: " + result["error"])
            self.error.show()
            return
        s, model = self.state, self.model
        self.error.setText(
            "Unresolved pool entries: intervals show the possible odds."
            if result["uncertain"]
            else ""
        )
        self.error.setVisible(bool(self.error.text()))
        setup = result["splice"]
        if setup and setup["available"]:
            # Longer localized units need their own line at compact widths.
            unit_separator = " " if language.code == "en" else "<br>"
            self.splice_metrics.setText(
                f'<table width="100%"><tr><td>1a. Find ≥{s["spliceGrade"]} ingredient</td><td>1b. Find its partner</td></tr>'
                f'<tr><td><b style="font-size:22px;color:#80d4fc">{interval(setup["first"])}</b>{unit_separator}<span style="color:#8dabc0">avg rolls</span></td>'
                f'<td><b style="font-size:22px;color:#80d4fc">{interval(setup["additional"])}</b>{unit_separator}<span style="color:#8dabc0">extra</span></td></tr></table>'
            )
            self.splice_metrics.setToolTip(
                f"Already together on {interval({k: v * 100 for k, v in setup['ready'].items()}, 2)}% of qualifying finds. Otherwise lock the positive: {interval(setup['ifMissing'])} rolls on average to find a partner."
            )
            self.splice_note.setText(
                f"Start {s['format']} with a listed lock. Acquisition excluded."
            )
            self.splice_note.setToolTip(
                "Use the listed positive or negative polarity. A lock preserves the format; acquiring the starting lock is not included in these estimates."
            )
            alternatives = setup["equivalentLocks"]
            self.lock_choices.title.setText(
                f"{len(alternatives)} equally optimal starting locks"
                if len(alternatives) > 1
                else "1 optimal starting lock"
            )
            text = []
            for polarity in ("negative", "positive"):
                names = sorted(
                    model.name(row["id"])
                    for row in alternatives
                    if row["polarity"] == polarity
                )
                if names:
                    text.append(
                        f"<b>{polarity.title()}:</b> {html.escape(', '.join(names))}."
                    )
            self.lock_names.setText("<br>".join(text))
            self.lock_choices.show()
            self.stages["splice"].set_summary(
                f"≥{s['spliceGrade']} · {interval(setup['total'])} avg rolls"
            )
        elif setup:
            self.splice_note.setText(
                "No single optimal route is available for this pool and format."
            )
        lock = result["lock"]
        if lock:
            target = model.lock_target
            self.lock_info.setText(
                f"{model.name(target['id'])} · {target['polarity']} · {s['format']}"
            )
            self.lock_metrics.setText(
                f'<table width="100%"><tr><td style="color:#8dabc0">{s["lockGrade"]} stat range</td><td style="color:#8dabc0">Odds · grade or better</td></tr>'
                f'<tr><td>{html.escape(lock["range"])}</td><td><b style="color:#80d4fc">{"Not rollable" if lock["vintage"] else odds_text(lock["probability"])}</b></td></tr></table>'
                + (
                    '<span style="color:#ff757b">Existing line only; cannot roll anew.</span>'
                    if lock["vintage"]
                    else ""
                )
            )
            p = lock["probability"]
            if lock["vintage"]:
                self.stages["lock"].set_summary("Not rollable")
            elif p["max"] > 0:
                self.stages["lock"].set_summary(
                    f"≥{s['lockGrade']} · {interval({'min': 1 / p['max'], 'max': 1 / p['min'] if p['min'] else math.inf})} avg rolls"
                )
        strategy = result["strategy"]
        titles = {
            "none": "Splice only" if model.splice else "No lock",
            "positive": "Positive lock",
            "negative": "Negative lock",
        }
        if model.splice and strategy != "none":
            titles[strategy] = "Splice + " + titles[strategy].lower()
        self.final_info.setText(titles[strategy])
        probability = result["final"][strategy]
        retained = model.retained_positives + model.retained_negatives
        if retained and probability["max"] <= 0:
            note = (
                "Only one vintage line can be retained with the manual lock."
                if len(retained) > 1
                else "Lock the vintage line to retain it."
            )
            self.final_metrics.setText(
                f'<b style="color:#ff757b">Not possible by cycling</b><br>{note}'
            )
            self.final_metrics.setToolTip("")
            self.stages["final"].set_summary("Not possible by cycling")
            return
        chances = []
        for confidence in (0.5, 0.95):
            attempts = interval(
                {
                    "min": attempts_for(probability["max"], confidence),
                    "max": attempts_for(probability["min"], confidence),
                },
                0,
            )
            chances.append(
                f'<span style="color:#8dabc0">{confidence:.0%}</span> · <b>{attempts} rolls</b>'
            )
        self.final_metrics.setText(
            f'<table width="100%"><tr><td valign="middle"><span style="color:#80d4fc;font-size:26px">{odds_text(probability)}</span></td>'
            f'<td align="right">{chances[0]}<br>{chances[1]}</td></tr></table>'
        )
        self.final_metrics.setToolTip(
            "\n".join(f"{name}: {odds_text(p)}" for name, p in result["final"].items())
            + "\nAverage rolls, not a guarantee. Existing locks and splice assumed."
        )
        self.stages["final"].set_summary(odds_text(probability))

    def watch_dialog(self):
        dialog = ModalDialog(self)
        dialog.setWindowTitle("Watch splice ingredients")
        dialog.resize(440, 540)
        layout = QVBoxLayout(dialog)
        header = QHBoxLayout()
        header.addWidget(label("Watch splice ingredients", "heading"), 1)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        header.addWidget(close)
        layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content, rows = box(spacing=12)
        available = {t["id"] for t in splices_for(self.model.weapon["kind"])}
        for trait in SPLICES:
            checkbox = QCheckBox(trait["name"])
            checkbox.setEnabled(trait["id"] in available)
            checkbox.setChecked(
                trait["id"] in available and trait["id"] in self.state["spliceWatch"]
            )
            rows.addWidget(checkbox)
            recipe = (
                self.model.recipe_text(trait["id"])
                if trait["id"] in available
                else "Not applicable to this weapon type"
            )
            rows.addWidget(label(recipe, "recipe", True))
            checkbox.toggled.connect(
                lambda checked, identity=trait["id"]: self.watch_changed(
                    identity, checked
                )
            )
        rows.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        dialog.exec()

    def watch_changed(self, identity, checked):
        selected = set(self.state["spliceWatch"])
        selected.add(identity) if checked else selected.discard(identity)
        self.state["spliceWatch"] = sorted(selected)
        available = {t["id"] for t in splices_for(self.model.weapon["kind"])}
        self.watch.setText(f"Watch splices ({len(selected & available)})")
        self.changed.emit()
