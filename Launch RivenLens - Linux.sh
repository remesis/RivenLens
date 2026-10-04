#!/bin/sh
# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

set -eu
root_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for candidate in python3.14 python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; sys.exit(sys.version_info[:2] not in ((3, 12), (3, 13), (3, 14)))'; then
        exec "$candidate" -B "$root_dir/native/linux_setup.py"
    fi
done
printf '%s\n' 'RivenLens needs Python 3.12, 3.13 or 3.14 with its venv package.' >&2
exit 1
