# RivenLens

A small desktop companion for grading Rivens as you roll.

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

You'll need **64-bit Windows and Windows' OCR feature for your game's language**.
See [Language support](#language-support) below for supported languages and setup.

Download the [latest release](https://github.com/remesis/RivenLens/releases/latest)
and extract **RivenLens.zip** into a folder you can keep.
Double-click **Start Riven Lens.cmd**. If a standard 64-bit Python 3.13 or 3.14
install is missing, the launcher offers to download and install Python 3.13 for
you. Existing compatible installs are left alone. The first launch then installs
the required packages into RivenLens' own environment.

Choose your monitor and press **Start OCR**. Put RivenLens beside the cards or
on a second monitor. Capture always starts paused. **Pause OCR** stops scanning;
closing the window or choosing **Quit** exits the app.

## What's included

- Current and new rolls side by side, with rank-8 grading by default and automatic
  variant detection. Automatic rank detection is available in Settings.
- Collapsible splice, manual-lock and final-odds stages, shown when relevant.
- A platinum-icon seed finder for matching Warframe Market listings, sorted by
  the lowest listed price, with a remembered listing-age filter.
- Multiple acceptable targets per unlocked stat slot, with odds for distinct
  combinations. Choose **Any** at the bottom of a stat list to leave that slot open.
- Separate good and warning dings, volume controls, ten presets each and custom audio.
- Remembered targets, sounds, collapsed sections and window placement.
- **Always on top** in Settings for an overlay-style window. Use windowed or
  borderless play; exclusive fullscreen may cover it.

In stat lists, check all acceptable targets; press Enter or click outside to close.
The field shows the first selection and how many more are selected. A `*` marks
a stat selected in another unlocked slot. Overlapping targets are allowed, but
each actual rolled stat must be different, and each matching combination counts
only once.
Locking a slot keeps only its first selection and makes that list single-select.
That stat becomes unavailable in every other slot, including the opposite sign.
Existing duplicates are removed; a slot with no remaining targets becomes **Any**.
Splices and vintage stats remain single selections.

Enable **Ding** in the final-odds stage to hear when a fully read new roll matches
the whole target. Locked and spliced stats must also meet their selected grades.
This toggle is independent of the splice-ingredient and lock-stat dings.
The final-odds stage also shows estimated combined rolls and Kuva for the
applicable setup stages and final target. Hover over the figures for assumptions;
Kuva estimates use the selected language's compact units and number formatting.
English uses whole thousands (`135k`) and two decimal places for millions
(`1.23m`); other languages use their familiar abbreviations or units, such as
`1,23 млн` in Russian and `123万` in Japanese.

Select a splice or manual lock, then click the **platinum button** beside the
eligibility legend to open **Purchase Ideal Starting Seed**. Without either
selection, the button does nothing. Searches run from your PC only when you open
the window or explicitly press **Refresh** in it. Changing planner stats,
grades, weapons or categories never sends Market requests; changing the age
filter uses downloaded results. Reopening within five minutes reuses cached
listings, including after grade changes; **Refresh** fetches listings again.
Search progress and request waits are shown in the window.
Requests are spaced at least 6.1 seconds apart. Failures stop the search rather
than automatically retrying, and rate-limit responses preserve their cooldown.
The window remembers its size and position. Each listing appears only in its
highest matching stage, rather than repeating across sections.
Searches never place bids, buy items or message
sellers.

For a splice, Stage 1 finds either ingredient at the selected splice grade or
better. With a manual lock too, Stage 2 requires that lock at its selected grade
and both ingredients, with at least one meeting the splice grade (the partner
can be any grade), or the qualifying splice itself. Both ingredient grades are
shown when present: S + F still produces an S splice. Without a
splice, Stage 1 finds the selected lock. All sections require the selected
format. Market currently does not advertise the new splice stats, so ingredient
listings are available but already-spliced listings cannot yet be verified.

The default age filter is **Less than 30 days**, measured from original creation,
not the seller's latest refresh. Options also include 15 days, 3 months, 6 months
and **Show All**; your preference is saved. Results include PC listings and
crossplay-enabled Xbox, PlayStation and mobile listings, including offline
sellers. Nintendo Switch listings are excluded. Searches use a five-minute
cache and may be limited by Market's search API. Seller-entered stats are
graded at the listed rank using Market's
base-weapon disposition. Inaccurate listings may be excluded; verify the Riven
with its seller. Auction prices show the lower listed endpoint, not a promise
that the item can be purchased for that amount.

## Language support

Select your game's language in the **bottom-center language picker**.
This changes OCR and the RivenLens interface together. The first
launch follows your Windows language when supported; your saved choice takes
priority afterward.

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
| Ukrainian | ✅ | ❌ No Windows OCR support at this time |
| Turkish | ✅ | ✅ |
| Japanese | ✅ | ✅ |
| Simplified Chinese | ✅ | ✅ |
| Traditional Chinese | ✅ | ✅ |
| Korean | ✅ | ✅ |
| Thai | ✅ | ❌ No Windows OCR support at this time |

If the matching Windows OCR feature is missing, RivenLens offers to download and
install it. This needs your approval, Windows administrator permission and an
internet connection; your display language and keyboard stay unchanged.
Support does not guarantee every roll will be read correctly.

## A few things to know

Grading defaults to **Manual rank 8/8**. Keep **Show Ranked** enabled in Warframe
when using this setting. If viewing lower-rank values, choose **Auto-detect rank
pips** under **Settings → Grading → Riven rank**. Your saved choice is remembered.
Existing installations switch to rank 8 once when upgrading; choices made
afterward remain saved.

Keep the stat text, card footer and bottom-right **Fits In** label visible; rank
pips must also be visible when using automatic rank detection. Small
text, animations and covered cards can confuse OCR. Previous grades stay visible
during brief interruptions. If capture is blank, try another capture method.

**Double-check important rolls before discarding them.** The warning covers
incomplete new-roll stat lines, not unreadable rank pips or variant labels.
Silence does not guarantee a correct read. Grades use bundled reference values;
planner odds are estimates, not guarantees.

Images and readings stay in memory and are not uploaded or automatically saved.
Settings, chosen sound copies and diagnostic logs are saved locally. Startup update
checks contact GitHub, never Warframe. Choose **Yes, update** to download, install
and reopen RivenLens automatically. Settings and custom sounds are preserved,
and the previous version is backed up. [Privacy details](docs/PRIVACY.md).

Updates come from [remesis/RivenLens](https://github.com/remesis/RivenLens/releases).
Only newer published releases prompt for an update. [Release configuration](docs/UPDATES.md).
Git checkouts are updated through Git instead of the automatic installer.
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
