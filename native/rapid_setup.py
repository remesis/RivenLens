# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Consent-driven, bounded setup of optional local OCR packages and models."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from instance import InstallationLease
from ocr_install import CONSENT_CANCELLED
from ocr_models import (
    BUILD_LOCK,
    LOCK,
    MODEL_HOST,
    MODELS,
    model_directory,
    model_path,
    ready,
    required_models,
    runtime_directory,
    runtime_packages,
    runtime_ready,
    valid_model,
)

FILE_LIMIT = 40 * 1024 * 1024


def model_url(url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        return (
            parsed.scheme == "https"
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and (host == "modelscope.cn" or host.endswith(".modelscope.cn"))
        )
    except ValueError:
        return False


class ModelRedirects(urllib.request.HTTPRedirectHandler):
    max_repeats = 1
    max_redirections = 5

    def redirect_request(self, request, response, code, message, headers, url):
        if not model_url(url):
            raise RuntimeError(
                "The model download redirected to an unsupported address."
            )
        return super().redirect_request(request, response, code, message, headers, url)


class RapidInstallSession:
    """One user-approved attempt. Failures stop; retry requires another click."""

    def __init__(self, code):
        # Reject unsupported languages before starting a worker or any downloads.
        required_models(code)
        self.code = code
        self.message = "Preparing RapidOCR setup…"
        self.error = ""
        self.result = None
        self.cancelled = threading.Event()
        self.thread = None
        self.verified = False

    def start(self):
        if self.thread is not None:
            raise RuntimeError("OCR setup has already started.")
        # Let an in-flight, timeout-bounded request finish cancellation cleanup
        # during process exit instead of abandoning its temporary files.
        self.thread = threading.Thread(target=self._run, name="OCR setup")
        self.thread.start()

    def poll(self):
        return None if self.thread is None or self.thread.is_alive() else self.result

    def cancel(self):
        self.cancelled.set()

    def shutdown(self):
        self.cancel()
        if self.thread is not None:
            self.thread.join(timeout=1)

    def _cleanup_staging(self):
        # Called only while holding the shared OCR installation lease. Other
        # versions' complete runtimes remain available for rollback/running apps.
        for parent, prefix in (
            (runtime_directory().parent, ".ocr-"),
            (model_directory(), ".model-"),
        ):
            if not parent.is_dir():
                continue
            resolved = parent.resolve()
            for path in parent.glob(prefix + "*"):
                if (
                    path.is_symlink()
                    or path.is_junction()
                    or path.resolve().parent != resolved
                ):
                    continue
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()

    def _verify_engine(self):
        from ocr_engine import LocalOCR

        self.message = "Checking the local OCR engine…"
        self._check_cancelled()
        engine = LocalOCR(self.code)
        try:
            self._check_cancelled()
        finally:
            engine.close()
        self.verified = True

    def _check_cancelled(self):
        if self.cancelled.is_set():
            raise InterruptedError("OCR setup was cancelled. OCR remains paused.")

    def _run(self):
        lease = None
        try:
            directory = model_directory().parent
            directory.mkdir(parents=True, exist_ok=True)
            lease = InstallationLease(directory)
            if not lease.acquire():
                raise RuntimeError(
                    "Another RivenLens copy is setting up OCR. Wait for it to finish, then try again."
                )
            self._cleanup_staging()
            self._install_packages()
            model_directory().mkdir(parents=True, exist_ok=True)
            for key in required_models(self.code):
                self._check_cancelled()
                if not valid_model(key):
                    self._download_model(key)
            if not ready(self.code):
                raise RuntimeError("The installed OCR files could not be verified.")
            self._verify_engine()
            self.message, self.result = "RapidOCR is ready.", 0
        except InterruptedError as exc:
            self.error, self.result = str(exc), CONSENT_CANCELLED
        except Exception as exc:
            self.error, self.result = str(exc), 1
        finally:
            if lease is not None:
                lease.close()

    def _install_packages(self):
        self._check_cancelled()
        if runtime_ready():
            return
        destination = runtime_directory()
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A unique staging directory prevents interrupted installs from becoming
        # an active runtime. No package is installed into the application itself.
        with tempfile.TemporaryDirectory(
            prefix=".ocr-", dir=destination.parent
        ) as staging:
            self.message = "Downloading and installing the local OCR runtime…"
            command = [
                sys.executable,
                "-m",
                "pip",
                "--isolated",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--no-deps",
                "--only-binary=:all:",
                "--require-hashes",
                "--retries",
                "0",
                "--timeout",
                "30",
                "--index-url",
                "https://pypi.org/simple",
                "--target",
                staging,
            ]
            deadline = time.monotonic() + 1200
            self._run_pip([*command, "-r", str(BUILD_LOCK)], deadline)
            # ANTLR has no upstream wheel. Build only its pinned pure-Python
            # source with the verified tools above, without build-isolation
            # downloads or installing anything into the application's Python.
            environment = dict(os.environ, PYTHONPATH=staging)
            self._run_pip(
                [
                    *command,
                    "--no-binary=antlr4-python3-runtime",
                    "--no-build-isolation",
                    "--upgrade",
                    "-r",
                    str(LOCK),
                ],
                deadline,
                environment,
            )
            self._check_cancelled()
            marker = Path(staging) / "installed.json"
            marker.write_text(json.dumps(runtime_packages()), encoding="utf-8")
            if not runtime_ready(Path(staging)):
                raise RuntimeError(
                    "The downloaded OCR runtime failed verification. It was not activated."
                )
            backup = None
            if destination.is_symlink() or destination.is_junction():
                raise RuntimeError("OCR setup cannot replace a linked runtime.")
            if destination.exists():
                # Never merge new packages over a damaged installation. Keep
                # the old directory until its verified replacement is in place.
                backup = destination.with_name(
                    destination.name + "-incomplete-" + str(time.time_ns())
                )
                destination.rename(backup)
            os.replace(staging, destination)
            if backup is not None:
                # This copy failed verification and is no longer the active
                # destination. The new runtime is already verified and in place.
                shutil.rmtree(backup)
            # TemporaryDirectory must still own an empty directory on exit.
            Path(staging).mkdir()

    def _run_pip(self, command, deadline, environment=None):
        self._check_cancelled()
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            while process.poll() is None:
                self._check_cancelled()
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "OCR package setup timed out. Check your connection before retrying."
                    )
                self.cancelled.wait(0.2)
            self._check_cancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "OCR package setup timed out. Check your connection before retrying."
                )
            if process.returncode:
                raise RuntimeError(
                    "OCR packages could not be installed. Check your connection, free space and Python version, then retry."
                )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def _download_model(self, key):
        self._check_cancelled()
        relative, expected = MODELS[key]
        url = MODEL_HOST + relative
        if not model_url(url):
            raise ValueError("Unsupported model address.")
        path = model_path(key)
        self.message = f"Downloading {path.name}…"
        opener = urllib.request.build_opener(ModelRedirects())
        request = urllib.request.Request(
            url, headers={"User-Agent": "RivenLens-OCR-Setup"}
        )
        descriptor, filename = tempfile.mkstemp(prefix=".model-", dir=path.parent)
        temporary = Path(filename)
        deadline, received, digest = time.monotonic() + 300, 0, hashlib.sha256()
        try:
            with (
                os.fdopen(descriptor, "wb") as output,
                opener.open(request, timeout=30) as response,
            ):
                if response.status != 200:
                    raise RuntimeError("The model server did not return a model.")
                while chunk := response.read(65536):
                    self._check_cancelled()
                    received += len(chunk)
                    if received > FILE_LIMIT or time.monotonic() >= deadline:
                        raise RuntimeError(
                            "The model download exceeded its size or time limit."
                        )
                    output.write(chunk)
                    digest.update(chunk)
                    self.message = (
                        f"Downloading {path.name} · {received / 1048576:.1f} MB"
                    )
                output.flush()
                os.fsync(output.fileno())
            self._check_cancelled()
            if time.monotonic() >= deadline:
                raise RuntimeError("The model download exceeded its time limit.")
            if received == 0 or digest.hexdigest() != expected:
                raise RuntimeError(
                    "The model download failed its SHA-256 check. No model was activated."
                )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
