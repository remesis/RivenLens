# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

$ErrorActionPreference = 'Stop'
$pending = Join-Path $PSScriptRoot 'update-pending.json'
if (Test-Path -LiteralPath $pending) {
    try {
        $updateJob = (Get-Content -LiteralPath $pending -Raw | ConvertFrom-Json).job
        if ([string]::IsNullOrWhiteSpace($updateJob)) { throw 'Missing recovery folder.' }
    } catch {
        throw "The pending update marker is damaged. No files were changed. Keep the recovery folders in: $env:LOCALAPPDATA\Arbitrations\RivenLens Native\updates"
    }
    $updatesDirectory = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Arbitrations\RivenLens Native\updates'))
    $resolvedJob = [IO.Path]::GetFullPath($updateJob)
    if ((Split-Path -Parent $resolvedJob) -ne $updatesDirectory -or (Split-Path -Leaf $resolvedJob) -notlike 'update-*') {
        throw 'The update recovery path is invalid. No files were changed.'
    }
    $recoveryHelper = Join-Path $resolvedJob 'update_installer.py'
    if (-not (Test-Path -LiteralPath $recoveryHelper)) { throw "Recovery helper missing. Keep the backup in: $resolvedJob" }
    py -3.13 -B $recoveryHelper --inspect $resolvedJob (Split-Path -Parent $PSScriptRoot)
    if ($LASTEXITCODE -eq 2) {
        Add-Type -AssemblyName System.Windows.Forms
        $choice = [System.Windows.Forms.MessageBox]::Show('An update needs attention. Choose Yes to recover the previous version. Your settings and sounds will stay in place.', 'RivenLens recovery', 'YesNo', 'Warning', 'Button2')
        if ($choice -ne 'Yes') { exit 0 }
        py -3.13 -B $recoveryHelper --recover $resolvedJob (Split-Path -Parent $PSScriptRoot)
        if ($LASTEXITCODE -ne 0) { throw "Recovery could not finish. Keep the backup and log in: $resolvedJob" }
    } elseif ($LASTEXITCODE -ne 0) {
        throw "The update is still active or needs attention. Close RivenLens and retry the launcher. Keep the backup and log in: $resolvedJob"
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
& $nativePython -B (Join-Path $PSScriptRoot 'dependencies.py') $requirements
if ($LASTEXITCODE -ne 0) {
    & $nativePython -m pip install --disable-pip-version-check --no-input --only-binary=:all: --require-hashes -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed. Check your connection and try again.' }
}
Start-Process -FilePath $nativeWindowPython -ArgumentList @('-B', ('"' + (Join-Path $PSScriptRoot 'bootstrap.py') + '"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
