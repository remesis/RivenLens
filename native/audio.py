# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Local synthesized alerts and bounded, user-selected audio files."""

import io
import wave
from pathlib import Path

import numpy as np
from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QObject,
    QSaveFile,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat, QAudioSink, QMediaDevices

from catalog import REFERENCE

SAMPLE_RATE = 48000
MAX_BYTES = 10 * 1024 * 1024
MAX_SECONDS = 30
GOOD_SOUNDS = REFERENCE["goodSounds"]
WARNING_SOUNDS = REFERENCE["warningSounds"]


def synthesize(preset):
    starts = [i * preset["gap"] for i in range(len(preset["notes"]))]
    duration = max(starts) + preset["decay"] + 0.02
    times = np.arange(int(duration * SAMPLE_RATE)) / SAMPLE_RATE
    output = np.zeros_like(times)
    envelope_sum = np.zeros_like(times)
    for start, frequency in zip(starts, preset["notes"]):
        age = times - start
        envelope = np.zeros_like(times)
        attack = (age >= 0) & (age < 0.008)
        hold = (age >= 0.008) & (age <= 0.048)
        release = (age > 0.048) & (age <= preset["decay"])
        fade = (age > preset["decay"]) & (age < preset["decay"] + 0.01)
        envelope[attack] = age[attack] / 0.008
        envelope[hold] = 1
        envelope[release] = 0.001 ** (
            (age[release] - 0.048) / (preset["decay"] - 0.048)
        )
        envelope[fade] = 0.001 * (1 - (age[fade] - preset["decay"]) / 0.01)
        wave_value = np.sin(2 * np.pi * frequency * age)
        if preset.get("wave") == "triangle":
            wave_value = 2 / np.pi * np.arcsin(wave_value)
        output += envelope * wave_value
        envelope_sum += envelope
    output *= 0.85 / max(1e-9, envelope_sum.max())
    return np.repeat(output[:, None], 2, axis=1).astype(np.float32)


def normalized(samples):
    if not np.isfinite(samples).all():
        raise ValueError("Audio contains invalid samples")
    peak = float(np.max(np.abs(samples), initial=0))
    return samples * min(4, 0.85 / peak) if peak > 0 else samples


def wave_bytes(samples):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


class SoundChannel(QObject):
    message = Signal(str)
    custom_loaded = Signal(str)

    def __init__(self, preferences, directory, warning=False, parent=None):
        super().__init__(parent)
        self.preferences = preferences
        self.directory = directory
        self.presets = WARNING_SOUNDS if warning else GOOD_SOUNDS
        self.custom_path = directory / ("warning.wav" if warning else "good.wav")
        self.custom = None
        self._cache = {}
        self._sink = self._buffer = self._decoder = None
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(
            lambda: self._decode_failed("Audio decoding timed out")
        )
        if self.custom_path.is_file():
            try:
                with wave.open(str(self.custom_path), "rb") as audio:
                    if (
                        audio.getnchannels() != 2
                        or audio.getsampwidth() != 2
                        or audio.getframerate() != SAMPLE_RATE
                        or audio.getnframes() > SAMPLE_RATE * MAX_SECONDS
                    ):
                        raise ValueError("Invalid saved audio")
                    self.custom = (
                        np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
                        .reshape(-1, 2)
                        .astype(np.float32)
                        / 32767
                    )
            except (OSError, ValueError, wave.Error):
                self.preferences["soundId"] = self.presets[0]["id"]
        elif preferences.get("soundId") == "custom":
            self.preferences["soundId"] = self.presets[0]["id"]

    def samples(self):
        identity = self.preferences.get("soundId")
        if identity == "custom":
            return self.custom
        preset = next((p for p in self.presets if p["id"] == identity), self.presets[0])
        if preset["id"] not in self._cache:
            self._cache[preset["id"]] = synthesize(preset)
        return self._cache[preset["id"]]

    def stop(self):
        if self._sink:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
        if self._buffer:
            self._buffer.close()
            self._buffer.deleteLater()
            self._buffer = None

    def play(self):
        self.stop()
        volume = max(0, min(100, self.preferences.get("soundVolume", 50))) / 100
        if not volume:
            return True
        samples = self.samples()
        if samples is None:
            self.message.emit("Choose a custom audio file first.")
            return False
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            self.message.emit("No audio output device is available.")
            return False
        fmt = device.preferredFormat()
        rate, channels = fmt.sampleRate(), fmt.channelCount()
        if rate <= 0 or channels <= 0:
            self.message.emit("The audio output format is unavailable.")
            return False
        if rate != SAMPLE_RATE:
            times = (
                np.arange(round(len(samples) * rate / SAMPLE_RATE)) * SAMPLE_RATE / rate
            )
            samples = np.column_stack(
                [
                    np.interp(times, np.arange(len(samples)), samples[:, c])
                    for c in range(2)
                ]
            )
        if channels == 1:
            samples = samples.mean(axis=1, keepdims=True)
        elif channels > 2:
            samples = np.pad(samples, ((0, 0), (0, channels - 2)))
        samples = np.clip(samples * volume, -1, 1)
        kind = fmt.sampleFormat()
        if kind == QAudioFormat.SampleFormat.Float:
            content = samples.astype("<f4").tobytes()
        elif kind == QAudioFormat.SampleFormat.Int16:
            content = (samples * 32767).astype("<i2").tobytes()
        elif kind == QAudioFormat.SampleFormat.Int32:
            content = (samples * 2147483647).astype("<i4").tobytes()
        elif kind == QAudioFormat.SampleFormat.UInt8:
            content = ((samples + 1) * 127.5).astype("u1").tobytes()
        else:
            self.message.emit("This audio output format is not supported.")
            return False
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(content))
        self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._sink = QAudioSink(device, fmt, self)
        self._sink.start(self._buffer)
        return True

    def load_custom(self, path):
        path = Path(path)
        try:
            valid_file = path.is_file() and 0 < path.stat().st_size <= MAX_BYTES
        except OSError:
            valid_file = False
        if not valid_file:
            self.message.emit("Choose an audio file up to 10 MB.")
            return
        self.cancel_decode()
        self._chunks = []
        self._frames = 0
        self._name = path.name
        decoder = self._decoder = QAudioDecoder(self)
        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        decoder.setAudioFormat(fmt)
        decoder.bufferReady.connect(self._read_buffer)
        decoder.finished.connect(self._decode_finished)
        decoder.error.connect(
            lambda *_: self._decode_failed(
                "Could not decode this file. Try WAV, MP3 or OGG."
            )
        )
        decoder.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self._timeout.start(15000)
        self.message.emit("Reading local sound…")
        decoder.start()

    def cancel_decode(self):
        self._timeout.stop()
        if self._decoder:
            decoder, self._decoder = self._decoder, None
            decoder.stop()
            decoder.deleteLater()

    def _decode_failed(self, text):
        self.cancel_decode()
        self.message.emit(text)

    def _read_buffer(self):
        if not self._decoder:
            return
        buffer = self._decoder.read()
        fmt = buffer.format()
        if (
            fmt.sampleRate() != SAMPLE_RATE
            or fmt.channelCount() != 2
            or fmt.sampleFormat() != QAudioFormat.SampleFormat.Float
        ):
            self._decode_failed(
                "The file could not be converted to a supported audio format."
            )
            return
        self._frames += buffer.frameCount()
        if self._frames > SAMPLE_RATE * MAX_SECONDS:
            self._decode_failed("Choose a sound no longer than 30 seconds.")
            return
        self._chunks.append(bytes(buffer.data()))

    def _decode_finished(self):
        if not self._decoder:
            return
        self.cancel_decode()
        try:
            samples = (
                np.frombuffer(b"".join(self._chunks), dtype="<f4").reshape(-1, 2).copy()
            )
            if not len(samples):
                raise ValueError("The audio file is empty.")
            samples = normalized(samples)
            self.directory.mkdir(parents=True, exist_ok=True)
            content = wave_bytes(samples)
            file = QSaveFile(str(self.custom_path))
            if (
                not file.open(QIODevice.OpenModeFlag.WriteOnly)
                or file.write(content) != len(content)
                or not file.commit()
            ):
                raise OSError("Could not save the local sound copy.")
            self.custom = samples
            self.preferences["soundId"] = "custom"
            self.custom_loaded.emit(self._name)
            self.message.emit("Custom sound saved locally.")
            self.play()
        except (ValueError, OSError) as exc:
            self.message.emit(str(exc))

    def close(self):
        self.stop()
        self.cancel_decode()
