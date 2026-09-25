# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Versioned, atomic local preferences. OCR readings are never persisted."""

import copy
import json

from PySide6.QtCore import QIODevice, QSaveFile

from catalog import SPLICE_IDS
from grading import FORMATS, GRADE_NAMES

DEFAULTS = {
    "weapon": "sobek-shotgun-primary",
    "variant": "sobek",
    "format": "3p1n",
    "positives": ["damage", "multishot", "fire-rate-attack-speed"],
    "negative": "impact",
    "lock": "none",
    "rank": 8,
    "rankMode": "auto",
    "gradeVariants": {},
    "spliceGrade": "S",
    "lockGrade": "F",
    "spliceSound": False,
    "lockSound": False,
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
        "monitor": 1,
        "interval": 0.1,
        "backend": "auto",
        "contrast": False,
        "region": [0, 0, 100, 100],
    },
    "geometry": "",
    "windowLayoutVersion": 0,
}


def capture_settings(value):
    result = {**DEFAULTS["capture"], **(value if isinstance(value, dict) else {})}
    if type(result["monitor"]) is not int or result["monitor"] < 1:
        result["monitor"] = 1
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
    if state["format"] not in FORMATS:
        state["format"] = "3p1n"
    for key in ("spliceGrade", "lockGrade"):
        if state[key] not in GRADE_NAMES:
            state[key] = DEFAULTS[key]
    if state["rankMode"] != "manual":
        state["rankMode"] = "auto"
    if not 0 <= state["rank"] <= 8:
        state["rank"] = 8
    state["positives"] = [s for s in state["positives"] if isinstance(s, str)][:3]
    state["spliceWatch"] = [
        s for s in state["spliceWatch"] if isinstance(s, str) and s in SPLICE_IDS
    ]
    state["capture"] = capture_settings(state["capture"])
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

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        data = {key: self.state[key] for key in DEFAULTS}
        file = QSaveFile(str(self.path))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError("Could not open local settings")
        content = json.dumps(
            {"schemaVersion": 1, **data}, ensure_ascii=False, allow_nan=False, indent=2
        ).encode("utf-8")
        if file.write(content) != len(content) or not file.commit():
            raise OSError("Could not save local settings")
