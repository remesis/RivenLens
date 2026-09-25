# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See docs/LICENSE.txt for the license and warranty disclaimer.

$ErrorActionPreference = 'Stop'
$pending = Join-Path $PSScriptRoot 'update-pending.json'
if (Test-Path -LiteralPath $pending) {
    $updateJob = (Get-Content -LiteralPath $pending -Raw | ConvertFrom-Json).job
    $updateStatus = Join-Path $updateJob 'status.json'
    if ((Test-Path -LiteralPath $updateStatus) -and (Get-Content -LiteralPath $updateStatus -Raw | ConvertFrom-Json).state -eq 'complete') {
        Remove-Item -LiteralPath $pending
    } else {
        throw "An update is still in progress or needs recovery. Its backup and log are in: $updateJob"
    }
}
$runtime = '.venv'
$runtimeSettings = Join-Path $PSScriptRoot 'runtime.json'
if (Test-Path -LiteralPath $runtimeSettings) {
    $runtime = (Get-Content -LiteralPath $runtimeSettings -Raw | ConvertFrom-Json).directory
    if ($runtime -ne '.venv' -and $runtime -notmatch '^\.runtimes/update-[a-z0-9_]+$') { throw 'Invalid Python environment selection.' }
}
$runtimeDirectory = Join-Path $PSScriptRoot $runtime
$nativePython = Join-Path $runtimeDirectory 'Scripts\python.exe'
$nativeWindowPython = Join-Path $runtimeDirectory 'Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $nativePython)) {
    Write-Host 'Setting up RivenLens. This first launch downloads its Python dependencies.'
    py -3.13 -m venv $runtimeDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.13 for Windows, then try again.' }
}
$requirements = Join-Path (Split-Path -Parent $PSScriptRoot) 'requirements.txt'
$dependencyProbe = @'
import importlib.metadata as metadata
from pathlib import Path
import sys
try:
    rows = [line.strip().split('==') for line in Path(sys.argv[1]).read_text().splitlines() if line.strip() and not line.startswith('#')]
    ready = all(len(row) == 2 and metadata.version(row[0]) == row[1] for row in rows)
except (metadata.PackageNotFoundError, ValueError):
    ready = False
sys.exit(0 if ready else 1)
'@
& $nativePython -B -c $dependencyProbe $requirements
if ($LASTEXITCODE -ne 0) {
    & $nativePython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed. Check your connection and try again.' }
}
Start-Process -FilePath $nativeWindowPython -ArgumentList @('-B', ('"' + (Join-Path $PSScriptRoot 'main.py') + '"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
