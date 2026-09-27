# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""User-approved Windows OCR feature setup; no downloaded scripts or game access."""

import base64
import ctypes
import subprocess
import sys
from pathlib import Path

# Only these fixed capability names may cross the administrator boundary.
# Do not derive an elevated command from preferences or translation data.
CAPABILITY_TAGS = {
    "en": "en-US",
    "de": "de-DE",
    "fr": "fr-FR",
    "it": "it-IT",
    "ko": "ko-KR",
    "ru": "ru-RU",
    "ja": "ja-JP",
    "pl": "pl-PL",
    "es": "es-ES",
    "pt": "pt-BR",
    "zh": "zh-CN",
    "tc": "zh-TW",
    "tr": "tr-TR",
}
RESTART_REQUIRED = 3010
CONSENT_CANCELLED = 1223


def installation_script(language):
    tag = CAPABILITY_TAGS[language]
    capabilities = ", ".join(
        f"'Language.{feature}~~~{tag}~0.0.1.0'" for feature in ("Basic", "OCR")
    )
    return r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    Import-Module (Join-Path $PSHOME 'Modules\Dism\Dism.psd1') -ErrorAction Stop
    $restartNeeded = $false
    foreach ($capabilityName in @(__CAPABILITIES__)) {
        $capability = Dism\Get-WindowsCapability -Online -Name $capabilityName
        if ($capability.Name -ne $capabilityName) { exit 1168 }
        if ($capability.State -eq 'Installed') { continue }
        if ($capability.State -eq 'InstallPending') { exit 3010 }
        $result = Dism\Add-WindowsCapability -Online -Name $capabilityName
        $restartNeeded = $restartNeeded -or $result.RestartNeeded
        $verified = Dism\Get-WindowsCapability -Online -Name $capabilityName
        if ($verified.State -ne 'Installed') {
            if ($restartNeeded -or $verified.State -eq 'InstallPending') { exit 3010 }
            exit 31
        }
    }
    if ($restartNeeded) { exit 3010 }
    exit 0
} catch {
    $failureCode = $_.Exception.HResult
    if (-not $failureCode) { $failureCode = 31 }
    exit $failureCode
}
""".replace("__CAPABILITIES__", capabilities)


def encoded(script):
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def launch_script(language):
    # The unelevated wrapper owns no files. It reports the administrator child's
    # exit code; closing RivenLens does not kill Windows servicing mid-operation.
    payload = encoded(installation_script(language))
    return r"""
$ErrorActionPreference = 'Stop'
try {
    $process = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') `
        -ArgumentList @('-NoProfile', '-NonInteractive', '-EncodedCommand', '__PAYLOAD__') `
        -WorkingDirectory $PSHOME -Verb RunAs -WindowStyle Hidden -PassThru
    $null = $process.Handle
    $process.WaitForExit()
    if ($null -eq $process.ExitCode) { exit 31 }
    exit $process.ExitCode
} catch {
    $failure = $_.Exception
    while ($failure) {
        if ($failure -is [System.ComponentModel.Win32Exception]) {
            exit $failure.NativeErrorCode
        }
        $failure = $failure.InnerException
    }
    exit 31
}
""".replace("__PAYLOAD__", payload)


def system_powershell():
    if sys.platform != "win32":
        raise OSError("Windows OCR feature installation requires Windows.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    directory = kernel.GetSystemDirectoryW
    directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = directory(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value) / "WindowsPowerShell/v1.0/powershell.exe"


class OCRInstallSession:
    def __init__(self, language):
        # Validate before creating a process, not in the elevated helper.
        self.script = launch_script(language)
        self.process = None

    def start(self):
        if self.process is not None:
            raise RuntimeError("OCR language installation has already started.")
        executable = system_powershell()
        self.process = subprocess.Popen(
            [
                str(executable),
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded(self.script),
            ],
            cwd=executable.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def poll(self):
        return self.process.poll() if self.process is not None else None
