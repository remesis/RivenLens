# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Bounded asynchronous HTTPS transfers, with atomic ZIP downloads."""

import hashlib

from PySide6.QtCore import QIODevice, QObject, QSaveFile, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from releases import github_url

METADATA_LIMIT = 1024 * 1024
DOWNLOAD_LIMIT = 256 * 1024 * 1024


class GithubTransfer(QObject):
    completed = Signal(bytes)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.reply = None
        self.file = None
        self.active = False
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(
            lambda: self.fail("The connection timed out. Please try again.")
        )

    def start(self, url, *, destination=None, sha256="", size=0):
        self.cancel()
        self.active = True
        self.download = destination is not None
        self.limit = DOWNLOAD_LIMIT if self.download else METADATA_LIMIT
        self.expected_digest, self.expected_size = sha256, size
        self.digest = hashlib.sha256()
        self.received = 0
        self.data = bytearray()
        self.prefix = bytearray()
        self.redirects = 0
        if self.expected_size > self.limit:
            self.fail("This update is too large to download here.")
            return
        if self.download:
            self.file = QSaveFile(str(destination))
            self.file.setDirectWriteFallback(False)
            if not self.file.open(QIODevice.OpenModeFlag.WriteOnly):
                self.fail(
                    "Could not save the update. Check free space and folder permissions."
                )
                return
        self.deadline.start(300_000 if self.download else 12_000)
        self.request(url)

    def request(self, url):
        if not github_url(url):
            self.fail("The update link is not a supported GitHub HTTPS address.")
            return
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        request.setRawHeader(b"User-Agent", b"RivenLens-Updater")
        request.setRawHeader(
            b"Accept",
            b"application/octet-stream"
            if self.download and QUrl(url).host() != "api.github.com"
            else b"application/vnd.github+json",
        )
        request.setTransferTimeout(15_000 if self.download else 10_000)
        self.reply = self.manager.get(request)
        self.reply.setReadBufferSize(64 * 1024)
        self.reply.readyRead.connect(self.read)
        self.reply.finished.connect(self.finished)
        self.reply.downloadProgress.connect(self.report_progress)

    def report_progress(self, received, total):
        if self.active:
            self.progress.emit(min(received, self.limit), min(total, self.limit))

    def read(self):
        if not self.active or self.reply is None:
            return
        status = self.reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        chunk = bytes(self.reply.readAll())
        if status != 200:
            return
        self.received += len(chunk)
        if self.received > self.limit:
            self.fail("The response exceeded the allowed download size.")
            return
        if self.download:
            self.prefix.extend(chunk[: max(0, 4 - len(self.prefix))])
            self.digest.update(chunk)
            if self.file.write(chunk) != len(chunk):
                self.fail(
                    "Could not finish saving the update. Check free space and permissions."
                )
        else:
            self.data.extend(chunk)

    def finished(self):
        if not self.active or self.reply is None:
            return
        self.read()
        if not self.active:
            return
        reply = self.reply
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        target = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
        if status in (301, 302, 303, 307, 308) and target:
            url = reply.url().resolved(target).toString(
                QUrl.ComponentFormattingOption.FullyEncoded
            )
            self.release_reply()
            self.redirects += 1
            if self.redirects > 5:
                self.fail("The update link redirected too many times.")
            else:
                self.request(url)
            return
        if reply.error() != QNetworkReply.NetworkError.NoError or status != 200:
            self.fail("Could not reach the release on GitHub. Please try again later.")
            return
        self.release_reply()
        if self.download:
            if (
                self.prefix != b"PK\x03\x04"
                or (self.expected_size and self.received != self.expected_size)
                or (
                    self.expected_digest
                    and self.digest.hexdigest() != self.expected_digest
                )
            ):
                self.fail(
                    "The downloaded file could not be verified. It has not been saved."
                )
                return
            if not self.file.commit():
                self.fail(
                    "Could not finish saving the update. Check free space and folder permissions."
                )
                return
            self.file = None
        self.active = False
        self.deadline.stop()
        self.completed.emit(bytes(self.data))

    def release_reply(self):
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.readyRead.disconnect(self.read)
            reply.finished.disconnect(self.finished)
            reply.downloadProgress.disconnect(self.report_progress)
            if reply.isRunning():
                reply.abort()
            reply.deleteLater()

    def cancel(self):
        self.active = False
        self.deadline.stop()
        self.release_reply()
        if self.file is not None:
            self.file.cancelWriting()
            self.file = None

    def fail(self, message):
        self.cancel()
        self.failed.emit(message)
