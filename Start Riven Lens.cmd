@echo off
rem Copyright (C) 2026 remesis and RivenLens contributors.
rem SPDX-License-Identifier: GPL-3.0-only
rem See LICENSE in the project root for the license and warranty disclaimer.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0native\launch.ps1"
if errorlevel 1 pause
