# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

# Bootstrap only when no compatible interpreter is available. Existing Python
# installations, PATH and file associations are left alone.

function Test-RivenPython {
    param([string]$Executable, [string]$Prefix = '')
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
    $probe = 'import sys, sysconfig, venv, ensurepip; ok = sys.version_info[:2] in ((3, 13), (3, 14)) and sysconfig.get_platform() == ''win-amd64'' and not sysconfig.get_config_var(''Py_GIL_DISABLED''); print(sys.executable) if ok else sys.exit(1)'
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = "$Prefix -I -B -X utf8 -c `"$probe`""
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    # Do not let Python's launcher install anything during discovery.
    $start.EnvironmentVariables.Remove('PYLAUNCHER_ALLOW_INSTALL')
    $start.EnvironmentVariables['PYTHON_MANAGER_AUTOMATIC_INSTALL'] = 'false'
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    try {
        [void]$process.Start()
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(5000)) {
            $process.Kill()
            return $null
        }
        $path = $output.GetAwaiter().GetResult().Trim()
        [void]$errors.GetAwaiter().GetResult()
        if ($process.ExitCode -eq 0 -and [IO.Path]::IsPathRooted($path) -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            return $path
        }
    } catch {
        # A missing or broken candidate must not prevent trying other installs.
    } finally {
        $process.Dispose()
    }
    return $null
}

function Find-RivenPython {
    # Registry discovery also works without py.exe or Python on PATH.
    foreach ($root in @('HKCU:', 'HKLM:')) {
        foreach ($tag in @('3.13', '3.13-64', '3.14', '3.14-64')) {
            $key = Get-Item -LiteralPath "$root\Software\Python\PythonCore\$tag\InstallPath" -ErrorAction SilentlyContinue
            if ($null -ne $key) {
                $candidate = $key.GetValue('ExecutablePath')
                if (-not $candidate -and $key.GetValue('')) { $candidate = Join-Path $key.GetValue('') 'python.exe' }
                if ($candidate) {
                    $found = Test-RivenPython $candidate
                    if ($found) { return $found }
                }
            }
        }
    }
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        foreach ($version in @('-3.13', '-3.14')) {
            $found = Test-RivenPython $launcher.Source $version
            if ($found) { return $found }
        }
    }
    foreach ($name in @('python3.13.exe', 'python3.14.exe', 'python.exe')) {
        foreach ($command in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            # Store placeholders can open a storefront instead of running Python.
            if ($command.Source -match '\\Microsoft\\WindowsApps\\[^\\]+$') { continue }
            $found = Test-RivenPython $command.Source
            if ($found) { return $found }
        }
    }
    return $null
}

function Confirm-RivenPythonInstall {
    Add-Type -AssemblyName System.Windows.Forms
    $message = 'RivenLens needs standard 64-bit Python 3.13 or 3.14, and a working copy was not found. Download the official Python 3.13 installer from python.org and install it for your Windows account? No administrator access or PATH changes are requested.'
    return [System.Windows.Forms.MessageBox]::Show($message, 'RivenLens setup', 'YesNo', 'Question', 'Button2') -eq 'Yes'
}

function Install-RivenPython {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'RivenLens needs 64-bit Windows.' }
    if (-not (Confirm-RivenPythonInstall)) { return $null }
    # Pinned official Windows x64 installer and published SHA-256:
    # https://www.python.org/downloads/release/python-31315/
    $url = 'https://www.python.org/ftp/python/3.13.15/python-3.13.15-amd64.exe'
    $digest = 'edec09c4853aeae9ac36efb8c9f95b6b8e2fee65eee56d9767a8b7c69c574403'
    $local = [Environment]::GetFolderPath('LocalApplicationData')
    if (-not [IO.Path]::IsPathRooted($local)) { throw 'Windows could not locate the local application data folder.' }
    $setup = Join-Path $local 'Arbitrations\RivenLens Native\setup'
    [void](New-Item -ItemType Directory -Path $setup -Force)
    $identity = [Guid]::NewGuid().ToString('N')
    $installer = Join-Path $setup "python-$identity.exe"
    $log = Join-Path $setup "python-$identity.log"
    $protocol = [Net.ServicePointManager]::SecurityProtocol
    try {
        Write-Host 'Downloading Python 3.13 from python.org...'
        [Net.ServicePointManager]::SecurityProtocol = $protocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installer -MaximumRedirection 0 -TimeoutSec 180
        if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne $digest) {
            throw 'Python download verification failed. The installer was not run. Please retry the launcher.'
        }
        Write-Host 'Installing Python for your Windows account. Please wait...'
        $arguments = @('/quiet', '/norestart', '/log', ('"' + $log + '"'),
            'InstallAllUsers=0', 'Include_launcher=0', 'InstallLauncherAllUsers=0',
            'PrependPath=0', 'AppendPath=0', 'AssociateFiles=0', 'Shortcuts=0',
            'Include_pip=1', 'Include_test=0', 'Include_doc=0', 'Include_tcltk=0',
            'Include_freethreaded=0')
        $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
        try { $code = $process.ExitCode } finally { $process.Dispose() }
        if ($code -notin @(0, 3010)) { throw "Python setup did not finish (code $code). Details: $log" }
        $found = Find-RivenPython
        if ($found) { return $found }
        if ($code -eq 3010) { throw 'Python setup needs a Windows restart. Restart your computer, then open RivenLens again.' }
        throw "Python setup finished, but a compatible interpreter could not be found. Details: $log"
    } finally {
        [Net.ServicePointManager]::SecurityProtocol = $protocol
        # Delete only this attempt's downloaded installer, retaining setup logs.
        if (Test-Path -LiteralPath $installer) { Remove-Item -LiteralPath $installer -ErrorAction SilentlyContinue }
    }
}

function Get-RivenSetupPython {
    $found = Find-RivenPython
    if ($found) { return $found }
    return Install-RivenPython
}
