# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""On-demand public Market reads with a shared request budget and bounded cache."""

import json
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from market import API_ROOT, SeedMatcher, attribute_map, weapon_slug
from releases import ReleaseConfig

REQUEST_INTERVAL = 6.1
RESPONSE_LIMIT = 8 * 1024 * 1024
CACHE_LIMIT = 32


def retry_delay(value, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            delay = (parsedate_to_datetime(value) - now).total_seconds()
        except (TypeError, ValueError, OverflowError):
            delay = 60
    return max(60, delay) if math.isfinite(delay) else 60


def request_error(status, error):
    if type(status) is int and 500 <= status <= 599:
        return f"Market server error (HTTP {status}). Try again later."
    if type(status) is int and 300 <= status <= 499:
        return f"Market request failed (HTTP {status})."
    if error == QNetworkReply.NetworkError.TimeoutError:
        return "Market request timed out."
    if error != QNetworkReply.NetworkError.NoError:
        return f"Could not reach Warframe Market. ({error.name})"
    return "Could not reach Warframe Market."


class MarketClient(QObject):
    completed = Signal(object, object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.reply = None
        self.next_request = 0
        self.cache = {}
        self.active = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.request)
        self.countdown = QTimer(self)
        self.countdown.setInterval(1000)
        self.countdown.timeout.connect(self.waiting)
        self.step = "Searching Warframe Market…"
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(lambda: self.fail("Market request timed out."))
        version = ReleaseConfig.load().version
        self.user_agent = (
            f"RivenLens/{version} (+https://github.com/remesis/RivenLens)".encode()
        )

    def start(self, planner, refresh=False):
        self.cancel()
        self.active = True
        self.planner = planner
        self.rows = {}
        self.matcher = None
        self.refresh_listings = refresh
        self.search_count = 0
        self.step = "Reading Market stat definitions…"
        self.fetch("v2/riven/attributes", self.attributes_ready, 86400)

    def fetch(self, path, callback, lifetime):
        self.path, self.callback, self.lifetime = path, callback, lifetime
        cached = self.cache.get(path)
        force = getattr(self, "refresh_listings", False) and path.startswith(
            "v1/auctions/"
        )
        if cached and not force and time.monotonic() - cached[0] < lifetime:
            self.progress.emit("Using cached results · " + self.step)
            self.deliver(path, callback, cached[1])
            return
        delay = max(0, self.next_request - time.monotonic())
        if delay > 120:
            self.fail("Market rate limit reached. Try again later.")
            return
        self.timer.start(math.ceil(delay * 1000))
        if delay:
            self.waiting()
            self.countdown.start()
        else:
            self.progress.emit(self.step)

    def waiting(self):
        if self.reply is not None:
            seconds = int(time.monotonic() - self.request_started)
            self.progress.emit(self.step + f" · {seconds}s elapsed")
            return
        seconds = math.ceil(max(0, self.next_request - time.monotonic()))
        self.progress.emit(f"Waiting {seconds}s for the next request · " + self.step)

    def request(self):
        if not self.active or self.reply is not None:
            return
        self.timer.stop()
        now = time.monotonic()
        delay = self.next_request - now
        if delay > 0:
            self.timer.start(math.ceil(delay * 1000))
            return
        self.progress.emit(self.step)
        self.request_started = now
        self.next_request = self.request_started + REQUEST_INTERVAL
        request = QNetworkRequest(QUrl(API_ROOT + self.path))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        request.setRawHeader(b"User-Agent", self.user_agent)
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"Platform", b"pc")
        request.setRawHeader(b"Crossplay", b"true")
        request.setRawHeader(b"Language", b"en")
        request.setTransferTimeout(20_000)
        self.data = bytearray()
        self.reply = self.manager.get(request)
        self.reply.setReadBufferSize(64 * 1024)
        self.reply.readyRead.connect(self.read)
        self.reply.finished.connect(self.finished)
        self.countdown.start()
        self.deadline.start(30_000)

    def read(self):
        if not self.active or self.reply is None:
            return
        source = self.sender()
        if source is not None and source is not self.reply:
            return
        chunk = bytes(self.reply.readAll())
        if len(self.data) + len(chunk) > RESPONSE_LIMIT:
            self.fail("Market response is too large.")
            return
        self.data.extend(chunk)

    def finished(self):
        if not self.active or self.reply is None:
            return
        source = self.sender()
        if source is not None and source is not self.reply:
            return
        self.read()
        if not self.active:
            return
        self.deadline.stop()
        self.countdown.stop()
        reply = self.reply
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status in (429, 509):
            self.next_request = time.monotonic() + retry_delay(
                bytes(reply.rawHeader(b"Retry-After")).decode("ascii", "replace")
            )
            self.fail("Market rate limit reached. Try again later.")
            return
        if status in (401, 403):
            self.next_request = time.monotonic() + 60
            self.fail("Market access is unavailable. Try again later.")
            return
        if status != 200 or reply.error() != QNetworkReply.NetworkError.NoError:
            self.fail(request_error(status, reply.error()))
            return
        try:
            data = json.loads(self.data)
            if not isinstance(data, dict) or data.get("error"):
                raise ValueError("Invalid envelope")
        except (ValueError, UnicodeError):
            self.fail("Market returned an unsupported response.")
            return
        self.release_reply()
        if len(self.cache) >= CACHE_LIMIT:
            self.cache.pop(next(iter(self.cache)))
        self.cache[self.path] = time.monotonic(), data
        self.deliver(self.path, self.callback, data)

    def deliver(self, path, callback, data):
        try:
            callback(data)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            self.cache.pop(path, None)
            self.fail("Market returned an unsupported response.")

    def attributes_ready(self, data):
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) > 256:
            raise ValueError("Invalid attribute list")
        self.mapping = attribute_map(rows, self.planner.definitions)
        self.step = "Reading Market weapon details…"
        self.fetch(
            "v2/riven/weapon/" + weapon_slug(self.planner.weapon["name"]),
            self.weapon_ready,
            86400,
        )

    def weapon_ready(self, data):
        weapon = data["data"]
        disposition = weapon["disposition"]
        if (
            weapon["slug"] != weapon_slug(self.planner.weapon["name"])
            or type(disposition) not in (int, float)
            or not math.isfinite(disposition)
            or not 0 < disposition <= 2
        ):
            raise ValueError("Invalid weapon disposition")
        self.matcher = SeedMatcher(self.planner, self.mapping, disposition)
        self.pending = self.matcher.searches()
        if not self.pending or len(self.pending) > 32:
            self.fail("Market does not support the selected stats yet.")
            return
        self.search_count = len(self.pending)
        self.search_next()

    def search_next(self):
        if self.pending:
            path = self.pending.pop(0)
            params = parse_qs(urlsplit(path).query)
            slug = (params.get("positive_stats") or params.get("negative_stats"))[0]
            stat = self.planner.name(self.mapping[slug])
            index = self.search_count - len(self.pending)
            self.step = f"Searching listings {index}/{self.search_count}: {stat}…"
            if self.rows:
                self.step += f"\nReceived {len(self.rows)} unique listings."
            self.fetch(path, self.listings_ready, 300)
        else:
            self.active = False
            self.progress.emit("Grading and matching listings…")
            self.completed.emit(self.matcher, list(self.rows.values()))

    def listings_ready(self, data):
        rows = data["payload"]["auctions"]
        if not isinstance(rows, list) or len(rows) > 5000:
            raise ValueError("Invalid listing list")
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                self.rows[row["id"]] = row
        self.progress.emit(f"Received {len(self.rows)} unique listings.")
        self.search_next()

    def release_reply(self):
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.readyRead.disconnect(self.read)
            reply.finished.disconnect(self.finished)
            if reply.isRunning():
                reply.abort()
            reply.deleteLater()

    def cancel(self):
        self.active = False
        self.timer.stop()
        self.countdown.stop()
        self.deadline.stop()
        self.release_reply()

    def fail(self, message):
        self.cancel()
        self.failed.emit(message)
