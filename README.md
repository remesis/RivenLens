# RivenLens

A small desktop companion for grading Rivens as you roll.

## Why use RivenLens instead of AlecaFrame or WFHelper?

If you just want Riven grades without giving a tool access to the game or your
account, that's what RivenLens is built for.

[AlecaFrame](https://docs.alecaframe.com/get-started/connecting) gets inventory
data through Overwolf. [WFHelper](https://github.com/WFHelper/WFHelper#inventory-data)
offers several inventory sources; its recommended
[helper](https://github.com/Sainan/warframe-api-helper/blob/senpai/main.cpp)
reads session credentials from game memory to request inventory from Warframe's
servers.

RivenLens uses **local Optical Character Recognition (OCR)** to read the text
already on your screen. It does not read game memory, obtain account tokens,
interact with the game or contact Warframe's servers. You make every in-game
choice. There is no browser interface or local web server.

## Getting started

You'll need **Windows, Python 3.13 and Windows' English OCR language feature**.
Download the [latest release](https://github.com/remesis/RivenLens/releases/latest)
and extract the ZIP into a folder you can keep.
Double-click **Start Riven Lens.cmd**. The first launch installs the required
Python packages into its own environment.

Choose your monitor and press **Start OCR**. Put RivenLens beside the cards or
on a second monitor. Capture always starts paused. **Pause OCR** stops scanning;
closing the window or choosing **Quit** exits the app.

## What's included

- Current and new rolls side by side, with automatic rank and variant detection.
- Collapsible splice, manual-lock and final-odds stages, shown when relevant.
- Separate good and warning dings, volume controls, ten presets each and custom audio.
- Remembered targets, sounds, collapsed sections and window placement.
- **Always on top** in Settings for an overlay-style window. Use windowed or
  borderless play; exclusive fullscreen may cover it.

## A few things to know

Keep the stat text, rank pips and bottom-right **Fits In** label visible. Small
text, animations and covered cards can confuse OCR. **LAST READ** means retained
grades, not a fresh reading. If capture is blank, try another capture method.

**Double-check important rolls before discarding them.** The warning covers
incomplete new-roll stat lines, not unreadable rank pips or variant labels.
Silence does not guarantee a correct read. Grades use bundled reference values;
planner odds are estimates, not guarantees.

Images and readings stay in memory and are not uploaded or automatically saved.
Only settings and chosen sound copies are saved locally. Optional startup update
checks contact GitHub, never Warframe. Choose **Yes, update** to download, install
and reopen RivenLens automatically. Settings and custom sounds are preserved,
and the previous version is backed up. [Privacy details](docs/PRIVACY.md).

Updates come from [remesis/RivenLens](https://github.com/remesis/RivenLens/releases).
Only newer published releases prompt for an update. [Release configuration](docs/UPDATES.md).

## License

Copyright (C) 2026 remesis and RivenLens contributors.

RivenLens is free software under the **GNU General Public License version 3
only** ([GPL-3.0-only](docs/LICENSE.txt)). You may use, modify and redistribute
it under those terms. It comes **without warranty**. Distributed modified
versions must remain GPL-licensed and provide corresponding source code.
[Third-party notices and credits](docs/THIRD_PARTY_NOTICES.md).

[Discord](https://discord.gg/Arbitrations) · [arbi.guide](https://arbi.guide/)

Not affiliated with or endorsed by Digital Extremes.
