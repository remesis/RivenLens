# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Versioned, atomic local preferences. OCR readings are never persisted."""

import copy
import json

from PySide6.QtCore import QIODevice, QLockFile, QSaveFile

from catalog import SPLICE_IDS
from grading import FORMATS, GRADE_NAMES
from localization import LANGUAGES
from ui_text import default_language

DEFAULTS = {
    "weapon": "sobek-shotgun-primary",
    "variant": "sobek",
    "format": "3p1n",
    "positives": ["damage", "multishot", "fire-rate-attack-speed"],
    "negative": "impact",
    "lock": "none",
    "positiveLockSlot": -1,
    "statAlternatives": [[], [], [], []],
    "categoryPlans": {},
    "rank": 8,
    "rankMode": "auto",
    "gradeVariants": {},
    "spliceGrade": "S",
    "lockGrade": "F",
    "spliceSound": False,
    "lockSound": False,
    "finalSound": False,
    "ocrWarningEnabled": True,
    "spliceWatch": [],
    "stagesOpen": {},
    "startingLocksOpen": False,
    "plannerOpen": True,
    "alwaysOnTop": False,
    "soundId": "chime",
    "soundVolume": 50,
    "ocrWarningAudio": {"soundId": "soft-fall", "soundVolume": 35},
    "capture": {
        "language": "en",
        "monitor": 1,
        "monitorId": "",
        "interval": 0.1,
        "backend": "auto",
        "contrast": False,
        "region": [0, 0, 100, 100],
    },
    "geometry": "",
    "windowPosition": [],
    "windowSize": [],
    "windowLayoutVersion": 0,
}

PLANNER_FIELDS = (
    "weapon",
    "variant",
    "format",
    "positives",
    "negative",
    "lock",
    "positiveLockSlot",
    "statAlternatives",
    "spliceGrade",
    "lockGrade",
    "spliceSound",
    "lockSound",
    "finalSound",
    "spliceWatch",
    "stagesOpen",
    "startingLocksOpen",
)


def planner_settings(saved):
    """Copy only category-specific selections, with safe defaults for older saves."""
    saved = saved if isinstance(saved, dict) else {}
    state = {
        key: copy.deepcopy(
            saved[key]
            if key in saved and type(saved[key]) is type(DEFAULTS[key])
            else DEFAULTS[key]
        )
        for key in PLANNER_FIELDS
    }
    if state["format"] not in FORMATS:
        state["format"] = DEFAULTS["format"]
    for key in ("spliceGrade", "lockGrade"):
        if state[key] not in GRADE_NAMES:
            state[key] = DEFAULTS[key]
    state["positives"] = [s for s in state["positives"] if isinstance(s, str)][:3]
    alternatives = state["statAlternatives"]
    state["statAlternatives"] = [
        list(dict.fromkeys(s for s in row if isinstance(s, str)))[:64]
        if isinstance(row, list)
        else []
        for row in (alternatives + [[], [], [], []])[:4]
    ]
    if state["positiveLockSlot"] not in (-1, 0, 1, 2):
        state["positiveLockSlot"] = -1
    state["spliceWatch"] = [
        s for s in state["spliceWatch"] if isinstance(s, str) and s in SPLICE_IDS
    ]
    state["stagesOpen"] = {
        key: value
        for key, value in state["stagesOpen"].items()
        if key in ("splice", "lock", "final") and type(value) is bool
    }
    return state


def capture_settings(value):
    result = {**DEFAULTS["capture"], **(value if isinstance(value, dict) else {})}
    if not isinstance(result["language"], str) or result["language"] not in LANGUAGES:
        result["language"] = "en"
    if type(result["monitor"]) is not int or result["monitor"] < 1:
        result["monitor"] = 1
    if not isinstance(result["monitorId"], str) or len(result["monitorId"]) > 1024:
        result["monitorId"] = ""
    if (
        type(result["interval"]) not in (float, int)
        or not 0.1 <= result["interval"] <= 5
    ):
        result["interval"] = 0.1
    if result["backend"] not in ("auto", "dxgi", "gdi"):
        result["backend"] = "auto"
    result["contrast"] = result["contrast"] is True
    region = result["region"]
    if (
        not isinstance(region, list)
        or len(region) != 4
        or any(type(n) not in (int, float) or not 0 <= n <= 100 for n in region)
        or region[2] < 10
        or region[3] < 10
        or region[0] + region[2] > 100
        or region[1] + region[3] > 100
    ):
        result["region"] = [0, 0, 100, 100]
    return {k: result[k] for k in DEFAULTS["capture"]}


def sanitize(saved):
    state = copy.deepcopy(DEFAULTS)
    if not isinstance(saved, dict):
        return state
    for key, default in DEFAULTS.items():
        if key in saved and type(saved[key]) is type(default):
            state[key] = copy.deepcopy(saved[key])
    state.update(planner_settings(state))
    state["categoryPlans"] = {
        category: planner_settings(plan)
        for category, plan in state["categoryPlans"].items()
        if isinstance(category, str) and len(category) <= 64 and isinstance(plan, dict)
    }
    if state["rankMode"] != "manual":
        state["rankMode"] = "auto"
    if not 0 <= state["rank"] <= 8:
        state["rank"] = 8
    state["capture"] = capture_settings(state["capture"])
    position = state["windowPosition"]
    if len(position) != 2 or any(
        type(value) is not int or not -(2**30) <= value < 2**30 for value in position
    ):
        state["windowPosition"] = []
    size = state["windowSize"]
    if len(size) != 2 or any(type(n) is not int or not 1 <= n <= 32768 for n in size):
        state["windowSize"] = []
    for audio in (state, state["ocrWarningAudio"]):
        volume = audio.get("soundVolume", 50)
        audio["soundVolume"] = (
            max(0, min(100, volume)) if type(volume) in (int, float) else 50
        )
    return state


class Preferences:
    def __init__(self, directory):
        self.directory = directory
        self.path = directory / "preferences.json"
        self.error = ""
        try:
            saved = (
                json.loads(self.path.read_text(encoding="utf-8"))
                if self.path.exists()
                else {}
            )
        except (OSError, ValueError):
            saved = {}
            self.error = "Saved settings could not be read. Defaults are in use."
        self.state = sanitize(saved)
        self._saved = copy.deepcopy(self.state)
        # A saved choice wins on every later launch/update. Default only when
        # this preference did not exist; never track live keyboard changes.
        capture = saved.get("capture", {}) if isinstance(saved, dict) else {}
        if not isinstance(capture, dict) or "language" not in capture:
            self.state["capture"]["language"] = default_language()

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        data = {key: self.state[key] for key in DEFAULTS}
        lock = QLockFile(str(self.directory / "preferences.lock"))
        if not lock.tryLock(1000):
            raise OSError("Settings are being saved by another copy. Please retry.")
        try:
            self._save_changed(data)
        finally:
            lock.unlock()

    def _save_changed(self, data):
        try:
            saved = (
                json.loads(self.path.read_text(encoding="utf-8"))
                if self.path.exists()
                else {}
            )
            if not isinstance(saved, dict):
                raise ValueError("Invalid settings")
        except (OSError, ValueError) as exc:
            raise OSError(
                "Saved settings could not be read. The existing file was left untouched."
            ) from exc
        merged = sanitize(saved)
        changed = {key for key in DEFAULTS if data[key] != self._saved[key]}
        # Keep a planner selection internally consistent across simultaneous copies.
        target = set(PLANNER_FIELDS)
        if changed & target:
            changed |= target
        for key in changed:
            if key == "categoryPlans":
                # Independent categories edited in another copy must survive.
                for category in data[key].keys() | self._saved[key].keys():
                    if data[key].get(category) != self._saved[key].get(category):
                        if category in data[key]:
                            merged[key][category] = copy.deepcopy(data[key][category])
                        else:
                            merged[key].pop(category, None)
            else:
                merged[key] = data[key]
        file = QSaveFile(str(self.path))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError("Could not open local settings")
        content = json.dumps(
            {"schemaVersion": 1, **merged},
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        ).encode("utf-8")
        if file.write(content) != len(content) or not file.commit():
            raise OSError("Could not save local settings")
        self._saved = copy.deepcopy(data)
