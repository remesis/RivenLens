# Copyright (C) 2026 remesis and RivenLens contributors.
# SPDX-License-Identifier: GPL-3.0-only
# See LICENSE in the project root for the license and warranty disclaimer.

"""Exact weapon families shared by card and caption readers."""

import re

from parser import CATALOG

VARIANT_GROUPS = {}
for family_index, _, variants in CATALOG["weapons"]:
    family = CATALOG["families"][family_index][0]
    group_name = re.sub(
        r"\s*\((?:Primary|Secondary|Rifle|Melee)\)$", "", family, flags=re.I
    )
    VARIANT_GROUPS.setdefault(group_name, set()).update(
        [family, *[v[0] for v in variants]]
    )
VARIANT_CHOICES = {
    name: choices for choices in VARIANT_GROUPS.values() for name in choices
}
