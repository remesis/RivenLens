# Privacy and runtime boundaries

## Screen pixels only

RivenLens captures the selected monitor or region through Windows desktop
capture or Linux X11 capture. Windows' installed OCR engine reads supported
languages locally; RapidOCR handles Linux, and Ukrainian and Thai on Windows.
It does not read game memory, inspect game files, obtain account credentials,
send game input, attach to the game or contact Warframe servers.

Capture starts only when you press **Start OCR**. Images, card readings and
alert history stay in memory. They are not uploaded or automatically saved.
Other visible windows can appear in the selected capture area.

**Always on top** changes only RivenLens' own window layering through Qt. It is
not a game overlay or graphics hook. Closing the app stops capture and releases
its resources.

## Connections

The interface is a native Python/Qt window. It has no HTTP server, web socket,
browser profile, cloud OCR or telemetry.

The app makes one startup check for releases from `remesis/RivenLens` on GitHub
over HTTPS. This sends an ordinary public release request, not
screenshots, card readings, settings, account tokens or personal identifiers
added by the app. GitHub receives normal connection information such as your
IP address. Git checkouts and copies with an empty repository or asset setting
do not make update requests.

A newer stable Windows release offers **Yes, update** or **No**. Yes authorizes downloading
and installing that release, then reopening RivenLens. The updater validates and
stages the archive locally before closing the app. If dependencies changed, it
prepares a separate Python environment using the configured package index.
Only verified, release-managed files are replaced. Local modifications and file
collisions stop installation; unrelated files are preserved. It does not interact with Warframe.
Failed or cancelled downloads are not treated as completed updates.
Linux displays a release link for manual updates instead of running the Windows
installer.

The footer links open their external sites only when clicked. Installing
dependencies uses the configured Python package index. These services have
their own privacy practices.

Opening **Purchase Ideal Starting Seed** sends public HTTPS requests directly to
`api.warframe.market` for the selected weapon and relevant stat identities. No
screen pixels, OCR readings, account credentials or complete settings are sent.
Market receives ordinary connection information such as your IP address and an
identifying RivenLens User-Agent. Searches are rate-limited locally; responses
are cached in memory, not saved to disk. Closing the dialog cancels pending
requests. Clicking a listing opens its page in your browser. The app does not
sign in, buy, bid, message sellers or automatically monitor listings.

If standard 64-bit Python 3.13 or 3.14 is missing, the launcher asks before
downloading a pinned official Python 3.13 installer from python.org. It checks
the published SHA-256 before running it for the current Windows account, without
requesting administrator access or changing PATH and file associations. Compatible existing installs are
reused. Setup logs remain in the app-data `setup` folder; the downloaded installer
is removed after the attempt.

When a selected Windows OCR language is missing, RivenLens offers an optional
installation. Only after you choose **Install OCR language** does it request
Windows administrator approval and ask Windows Update to install that language's
Basic typing and OCR capabilities. No external script is downloaded, and neither
the display language nor keyboard settings are changed. Windows' normal update
policies and privacy practices apply. Windows records servicing diagnostics in
its standard DISM logs. Closing RivenLens does not interrupt an already-approved
Windows feature installation; it does not restart Windows automatically.

RapidOCR setup requires explicit approval to download its pinned packages from
PyPI and recognition models from ModelScope over HTTPS. Package archives are
hash-checked during installation; installed versions and model hashes are checked
before recognition. No screen pixels or recognized text are sent. Package setup
has a time limit, and model downloads have size and time limits. Failures stop
the attempt rather than retrying in a loop. Closing RivenLens requests cancellation;
an in-progress network operation may take up to 30 seconds to finish. These
services receive normal connection information such as your IP address.

## Local storage

Preferences and explicitly chosen sound copies are stored under
`%LOCALAPPDATA%\Arbitrations\RivenLens Native`. This existing folder name is
kept so earlier Python-version settings continue to work. Removing a saved
custom sound does not delete your original audio file.

On Linux, local settings and runtimes use `$XDG_DATA_HOME/Arbitrations/RivenLens Native`,
or `~/.local/share/Arbitrations/RivenLens Native` when that variable is unset.
RapidOCR packages and models live in the `ocr` subfolder of the platform's
app-data directory, separate from the application and reused across updates.

Downloaded updates, installation logs and source backups stay in the `updates`
subfolder of that same app-data directory. Windows Python dependencies live in
`native/.venv` or `native/.runtimes`; Linux uses the app-data `runtime` subfolder.
These remain separate from preferences. Existing runtimes
are kept intact so installation failures can restore the previous version.

Launch failures and unexpected application errors are logged locally in
`logs/application.log`. Routine diagnostic output is capped at 2 MB; a full log is
rotated on the next launch when it is no longer in use. Repeated callback errors
are throttled and show one warning per session. These logs are not uploaded and
are not a recording of your screen or OCR results.

These are implementation boundaries, not an endorsement or a guarantee about
third-party platform rules or enforcement.
