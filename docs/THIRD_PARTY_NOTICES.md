# Third-party notices and credits

The GPL-3.0-only grant in [LICENSE](../LICENSE) covers RivenLens' original
application code and documentation. Dependencies remain under their own licenses.
It does not grant rights to third-party trademarks or content.

## Python dependencies

The source release does not bundle Python, Qt libraries, third-party wheels or
a virtual environment. The launcher installs dependencies separately. Their
copyright and license notices remain with their packages and upstream sources.
If standard 64-bit Python 3.13 or 3.14 is missing, the launcher can download the
official Python 3.13 installer with permission. Python remains covered by the
[PSF license](https://docs.python.org/3/license.html).

| Component | License | Upstream source |
| --- | --- | --- |
| PySide6, Shiboken6 and Qt | LGPLv3/GPL options; some components have additional terms | [Qt for Python](https://code.qt.io/cgit/pyside/pyside-setup.git/) and [Qt licensing](https://doc.qt.io/qt-6/licensing.html) |
| MSS | MIT | [python-mss](https://github.com/BoboTiG/python-mss) |
| DXcam | MIT | [DXcam](https://github.com/ra1nty/DXcam) |
| Pillow | MIT-CMU, with third-party component notices | [Pillow](https://github.com/python-pillow/Pillow) |
| PyWinRT runtime and Windows projections | MIT | [PyWinRT](https://github.com/pywinrt/pywinrt) |
| NumPy, installed through dependencies | BSD-3-Clause, with additional bundled-component licenses | [NumPy](https://github.com/numpy/numpy) |
| comtypes, installed through dependencies | MIT | [comtypes](https://github.com/enthought/comtypes) |
| typing_extensions, installed through dependencies | PSF-2.0 | [typing_extensions](https://github.com/python/typing_extensions) |

Python and Windows' installed OCR feature are prerequisites, not part of this
source distribution. If you package an executable or redistribute dependencies,
include the exact packages' licenses, copyright notices and required source or
source-access information. This table does not replace those obligations.

## Reference values and branding

Thanks to the [Warframe Wiki contributors](https://wiki.warframe.com/w/Riven_Mods)
for the public Riven reference tables, including spliced-stat baselines. The app
stores numerical values and names in its own catalog format. Wiki baseline links
are retained in `native/data/reference.json`; unknown values and assumptions are
identified separately. These are reference data, not a guarantee of game behavior.

The catalog includes the weapons from [Update 44: Iceblade of Narin](https://www.warframe.com/en/patch-notes/pc/44-0-0)
and DE's [September 2026 disposition changes](https://forums.warframe.com/topic/1523355-september-2026-riven-dispositions/).
New weapons start at 0.50. Nunchasa uses Rifle ranges; Aksondol uses Pistol ranges.
Their Puncture/Cold damage profiles are cross-checked against
[Nunchasa](https://overframe.gg/items/arsenal/8051/nunchasa/) and
[Aksondol](https://overframe.gg/items/arsenal/8050/aksondol/) reference entries.
Existing weapon pools, vintage flags and stat baselines are otherwise unchanged.

The Arbitrations logo is supplied by the Arbitrations project. Warframe and its
associated names and trademarks belong to Digital Extremes. No trademark rights,
Digital Extremes endorsement or ownership of third-party content are implied by
RivenLens' code license.
