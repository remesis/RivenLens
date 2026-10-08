# RivenLens

A small desktop companion for grading Rivens as you roll or view chat links.

![RivenLens example](https://raw.githubusercontent.com/remesis/RivenLens/main/assets/screenshots/example-image.png)

## Why use RivenLens instead of AlecaFrame or WFHelper?

If you just want Riven grades without giving a tool access to the game or your
account, that's what RivenLens is built for.

[AlecaFrame](https://docs.alecaframe.com/get-started/connecting) gets inventory
data through Overwolf. [WFHelper](https://github.com/WFHelper/WFHelper#inventory-data)
offers several inventory sources; its recommended
[helper](https://github.com/Sainan/warframe-api-helper/blob/senpai/main.cpp)
reads session credentials from game memory to request inventory from Warframe's
servers. Although, WFHelper's Riven scanner does work without these integrations
or a connected account.

RivenLens uses **local Optical Character Recognition (OCR)** to read the text
already on your screen. It does not read game memory, obtain account tokens,
interact with the game or contact Warframe's servers. You make every in-game
choice. However, it's important to use your own judgement and use it at your own risk.

According to section 2.f of the Warframe EULA, you agree that you will not under any
circumstance use unauthorized third‑party tools designed to modify the game experience.
You should read the EULA and the code yourself and decide whether you want to use this tool.

Digital Extremes PSA about third‑party software:
https://forums.warframe.com/topic/1320042-third-party-software-and-you/

## Getting started

Download the [latest release](https://github.com/remesis/RivenLens/releases/latest)
and extract **RivenLens.zip** into a folder you can keep.

**Windows:** double-click **Launch RivenLens - Windows.cmd**. If a standard 64-bit Python 3.13 or 3.14
install is missing, the launcher offers to download and install Python 3.13 for
you. Existing compatible installs are left alone.

**Linux:** on an x86-64 X11 desktop, install Python 3.12–3.14 with its `venv`
package, Qt's X11 libraries (`libxcb-cursor0` on Debian/Ubuntu) and fonts for
your chosen language, then run
`sh "Launch RivenLens - Linux.sh"` from the extracted folder. The launcher
asks before downloading dependencies into your user folder. Wayland capture is
not supported yet; live Warframe capture on Linux still needs user testing.

Choose your monitor and press **Start OCR**. Put RivenLens beside the cards or
on a second monitor. Capture always starts paused. **Pause OCR** stops scanning;
closing the window or choosing **Quit** exits the app.

## What's included

- Current and new grades side by side, automatic variant detection and rank 8
  by default. Chat-linked previews are graded in the current-card panel.
  Automatic rank detection is available in Settings.
- Collapsible splice, manual-lock and final-odds stages with roll and Kuva estimates.
- A platinum-icon seed finder for matching Warframe Market listings, sorted by
  the lowest listed price, with a remembered listing-age filter.
- Multiple acceptable targets per unlocked stat slot, with odds for distinct
  combinations. Choose **Any** at the bottom of a stat list to leave that slot open.
- Separate good and warning dings, volume controls, ten presets each and custom audio.
- Remembered targets, sounds, collapsed sections and window placement.
- **Always on top** in Settings for an overlay-style window. Use windowed or
  borderless play; exclusive fullscreen may cover it.

Check all acceptable targets in an unlocked stat list; press Enter or click
outside to close it. A `*` marks a target used in another slot. Each rolled stat
must be distinct, and overlapping combinations count only once. Locking a slot
keeps its first selection and removes that stat from other slots. Splices and
vintage stats stay single-select.

Each stage has its own **Ding** toggle. The final stage alerts only for a fully
read new roll matching the whole target, including the selected lock and splice
grades. Hover over the roll and Kuva figures for their assumptions.

## Finding a starting seed

Select a splice or manual lock, then click the **platinum button** beside the
eligibility legend to open **Purchase Ideal Starting Seed**. Without either
selection, the button does nothing. Searches run from your machine only when
you open this window or press **Refresh**. Planner changes never send requests;
age and grade changes reuse downloaded listings. Results are cached for five
minutes. Progress is shown while searching, with at least 6.1 seconds between
requests and no automatic retries. The window remembers its placement, and each
listing appears only in its highest matching stage. RivenLens never buys, bids
or messages sellers.

For a splice, Stage 1 finds either ingredient at the selected splice grade or
better. With a manual lock too, Stage 2 requires that lock at its selected grade
and both ingredients, with at least one meeting the splice grade (the partner
can be any grade), or the qualifying splice itself. Both ingredient grades are
shown when present: S + F still produces an S splice. Without a
splice, Stage 1 finds the selected lock. All sections require the selected format.

The default age filter is **Less than 30 days**, measured from original creation,
not the seller's latest refresh. You can save a different age filter. Results
include PC and crossplay-enabled Xbox, PlayStation and mobile listings, but not
Switch. Market may limit results and does not yet expose splice stats; ingredient
listings remain searchable. Seller-entered values are graded at the listed
rank using Market's base-weapon disposition; verify them with the seller.
Auction prices show the lower listed endpoint, not a guaranteed purchase price.

## Language support

Select your game's language in the **bottom-center language picker**.
The first launch follows your system language when supported; your saved choice
takes priority afterward. Windows uses its installed OCR engine for the original
13 languages. Linux uses **RapidOCR on four CPU threads**, as do Ukrainian and
Thai on Windows. No particular GPU is required.

| Language | Warframe support | RivenLens support |
| --- | :---: | --- |
| English | ✅ | ✅ |
| French | ✅ | ✅ |
| Italian | ✅ | ✅ |
| German | ✅ | ✅ |
| Spanish - Spain | ✅ | ✅ |
| Portuguese - Brazil | ✅ | ✅ |
| Russian | ✅ | ✅ |
| Polish | ✅ | ✅ |
| Ukrainian | ✅ | ✅ RapidOCR |
| Turkish | ✅ | ✅ |
| Japanese | ✅ | ✅ |
| Simplified Chinese | ✅ | ✅ |
| Traditional Chinese | ✅ | ✅ |
| Korean | ✅ | ✅ |
| Thai | ✅ | ✅ RapidOCR |

If the matching Windows OCR feature is missing, RivenLens offers to download and
install it. This needs your approval, Windows administrator permission and an
internet connection; your display language and keyboard stay unchanged.

RapidOCR asks before downloading its pinned CPU runtime and selected language's
models from PyPI and ModelScope. These files are cached **outside the application**
and reused by updates; they are not bundled into each release. Recognition stays
local. Support does not guarantee every roll will be read correctly.

## A few things to know

Grades measure rolled strength. A higher negative grade means a stronger penalty,
not a more desirable negative; the same scale applies to splice ingredients.

Grading defaults to **Manual rank 8/8**. Keep **Show Ranked** enabled in Warframe
when using this setting. If viewing lower-rank values, choose **Auto-detect rank
pips** under **Settings → Grading → Riven rank**. Your saved choice is remembered.

Keep the stat text, card footer and bottom-right **Fits In** label visible; rank
pips must also be visible when using automatic rank detection. For chat-linked
previews, keep **Item Details** or **Tradeable** visible too. Small
text, animations and covered cards can confuse OCR. Previous grades stay visible
during brief interruptions. On Windows, try another capture method if capture is
blank.

**Double-check important rolls before discarding them.** The warning covers
incomplete new-roll stat lines, not unreadable rank pips or variant labels.
Silence does not guarantee a correct read. Grades use bundled reference values;
planner odds are estimates, not guarantees.

Images and readings stay in memory and are not uploaded or automatically saved.
Settings, chosen sound copies and diagnostic logs are saved locally. Startup update
checks contact GitHub, never Warframe. Windows can download, install and reopen
an update with permission, preserving settings and backing up the previous
version. Linux updates are manual for now. [Privacy details](docs/PRIVACY.md).

Updates come from [remesis/RivenLens](https://github.com/remesis/RivenLens/releases).
Only newer published releases prompt for an update. [Release configuration](docs/UPDATES.md).
Git checkouts are updated through Git instead of the automatic installer.
On Windows, copies from 0.2.13 or earlier need the
[0.2.15 compatibility release](https://github.com/remesis/RivenLens/releases/tag/v0.2.15)
first, or a fresh extraction of the latest ZIP, because the launchers were renamed.
Recreate old launcher shortcuts to point to **Launch RivenLens - Windows.cmd**.
For copies from 0.1.0 or 0.1.1, download **RivenLens.zip** once and extract it into
a new folder to enable the updated installer. Your saved settings stay available.

## License

Copyright (C) 2026 remesis and RivenLens contributors.

RivenLens is free software under the **GNU General Public License version 3
only** ([GPL-3.0-only](LICENSE)). You may use, modify and redistribute
it under those terms. It comes **without warranty**. Distributed modified
versions must remain GPL-licensed and provide corresponding source code.
[Third-party notices and credits](docs/THIRD_PARTY_NOTICES.md).

[Discord](https://discord.gg/Arbitrations) · [arbi.guide](https://arbi.guide/)

Not affiliated with or endorsed by Digital Extremes.
