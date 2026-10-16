# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Observe this process's ScreenCast consent and explicitly close its session."""

import re

from PySide6.QtCore import QObject, Signal, Slot, SLOT
from PySide6.QtDBus import (
    QDBusConnection,
    QDBusMessage,
    QDBusObjectPath,
    QDBusPendingCallWatcher,
    QDBusServiceWatcher,
)

SERVICE = "org.freedesktop.portal.Desktop"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"
ROOT = "/org/freedesktop/portal/desktop/"
RESPONSE_SLOT = SLOT("_response(uint,QVariantMap,QDBusMessage)")
CLOSED_SLOT = SLOT("_ended(QVariantMap,QDBusMessage)")


class PortalWatch(QObject):
    """Observe consent, cancellation and closure alongside Qt's capture backend."""

    stopped = Signal(str)
    ready = Signal()
    authorized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bus = QDBusConnection.sessionBus()
        self.session = ""
        self._select_request = self._start_request = self._pending_request = ""
        self._authorized = False
        self._closed = False
        self._probe = None
        self._checked = False
        # Portal paths encode the requesting connection, not an arbitrary process.
        owner = self.bus.baseService().removeprefix(":").replace(".", "_")
        self._request_prefix = ROOT + "request/" + owner + "/"
        self._session_prefix = ROOT + "session/" + owner + "/"
        if not self.bus.isConnected() or not self.bus.connect(
            SERVICE, "", REQUEST, "Response", self, RESPONSE_SLOT
        ):
            raise RuntimeError("The desktop session bus is unavailable.")
        if not self.bus.connect(SERVICE, "", SESSION, "Closed", self, CLOSED_SLOT):
            self.bus.disconnect(SERVICE, "", REQUEST, "Response", self, RESPONSE_SLOT)
            raise RuntimeError("Screen-sharing session notifications are unavailable.")
        self._service = QDBusServiceWatcher(
            SERVICE,
            self.bus,
            QDBusServiceWatcher.WatchModeFlag.WatchForUnregistration,
            self,
        )
        self._service.serviceUnregistered.connect(self._service_ended)

    def prepare(self):
        """Check the required interface asynchronously, before Qt can fall back."""
        if self._closed or self._checked:
            return
        self._checked = True
        request = QDBusMessage.createMethodCall(
            SERVICE, ROOT.rstrip("/"), "org.freedesktop.DBus.Properties", "Get"
        )
        request.setArguments(["org.freedesktop.portal.ScreenCast", "version"])
        self._probe = QDBusPendingCallWatcher(self.bus.asyncCall(request, 3000), self)
        self._probe.finished.connect(self._prepared)

    @Slot(QDBusPendingCallWatcher)
    def _prepared(self, pending):
        if self._closed or pending != self._probe:
            return
        self._probe = None
        pending.deleteLater()
        if pending.isError() or not pending.reply().arguments():
            self.stopped.emit(
                "ScreenCast is unavailable. Install your desktop's screen-sharing portal backend, then restart RivenLens."
            )
        else:
            self.ready.emit()

    @Slot("uint", "QVariantMap", QDBusMessage)
    def _response(self, result, values, message):
        if self._closed or not message.path().startswith(self._request_prefix):
            return
        handle = values.get("session_handle")
        if isinstance(handle, QDBusObjectPath):
            handle = handle.path()
        if (
            not self.session
            and not result
            and isinstance(handle, str)
            and handle.startswith(self._session_prefix)
        ):
            token = handle.rsplit("/", 1)[1]
            modern = re.fullmatch(r"(qtmm_[0-9a-f]{32}_)([0-9]+)", token)
            if modern:
                # Qt 6.12 allocates distinct consecutive request/session tokens.
                prefix, number = modern[1], int(modern[2])
                if message.path() != self._request_prefix + prefix + str(number - 1):
                    return
                select, start = prefix + str(number + 1), prefix + str(number + 2)
            else:
                # Qt 6.11 reuses the session token for both requests.
                select = start = token
            self.session = handle
            self._select_request = self._pending_request = self._request_prefix + select
            self._start_request = self._request_prefix + start
        # Never authorize from another chooser's response or a different session.
        if not self._pending_request or message.path() != self._pending_request:
            return
        if result:
            self.stopped.emit(
                "Screen sharing was cancelled or declined. Press Start OCR to try again."
            )
        elif message.path() == self._start_request and values.get("streams"):
            self._pending_request = ""
            if not self._authorized:
                self._authorized = True
                self.authorized.emit()
        elif message.path() == self._select_request:
            self._pending_request = self._start_request
        else:
            self.stopped.emit(
                "The screen-sharing portal returned no streams. Press Start OCR to try again."
            )

    @Slot("QVariantMap", QDBusMessage)
    def _ended(self, values, message):
        if not self._closed and self.session and message.path() == self.session:
            self.session = ""  # Already closed by the desktop.
            self.stopped.emit(
                "Screen sharing ended. Press Start OCR to choose a screen again."
            )

    @Slot(str)
    def _service_ended(self, service):
        if not self._closed:
            self.stopped.emit(
                "The screen-sharing service stopped. Press Start OCR to try again."
            )

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._probe is not None:
            self._probe.deleteLater()
            self._probe = None
        self.bus.disconnect(SERVICE, "", REQUEST, "Response", self, RESPONSE_SLOT)
        self.bus.disconnect(SERVICE, "", SESSION, "Closed", self, CLOSED_SLOT)
        self._service.removeWatchedService(SERVICE)
        if self.session:
            # Close both a pending chooser request and the granted stream without
            # waiting on the UI thread or issuing a fresh permission request.
            if self._pending_request:
                self.bus.asyncCall(
                    QDBusMessage.createMethodCall(
                        SERVICE, self._pending_request, REQUEST, "Close"
                    )
                )
            self.bus.asyncCall(
                QDBusMessage.createMethodCall(SERVICE, self.session, SESSION, "Close")
            )
            self.session = ""
