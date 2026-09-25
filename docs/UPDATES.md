# Release updates

The startup checker uses [remesis/RivenLens](https://github.com/remesis/RivenLens).
Release settings live in `native/data/release.json`:

- `version`: this copy's stable version, such as `0.1.0`.
- `repository`: `remesis/RivenLens`. Empty disables update requests.
- `asset_name`: optional exact ZIP asset name, such as `RivenLens.zip`.
  Leave empty to download GitHub's source ZIP for the release tag.

Create a published, non-prerelease [GitHub Release](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)
with a tag such as `v0.1.1`. Update the bundled version to match in each release.
A newer tag alone is not enough; it must have a release. If an asset name is
configured, attach that ZIP to every release. Missing assets are ignored.

Checks run once after startup without blocking the interface. Offline, missing
repository, rate-limit and malformed-response errors are silent. Only a newer
stable version prompts the user. **No** dismisses it for that launch.

**Yes, update** downloads, installs and reopens RivenLens automatically. No save
dialog or manual extraction is needed. Cancel is available during download and
preparation. Once replacement starts, let the updater finish.

The app restricts HTTPS downloads and redirects to GitHub hosts, bounds response
sizes and timeouts, and verifies an asset's size and SHA-256 when supplied.
Archives are checked for unsafe paths, duplicate filenames, links, missing files,
invalid Python syntax and mismatched versions or repositories before installation.

The helper stages the release outside the installation. If requirements changed,
it builds a separate environment at a stable path under `native/.runtimes` without
altering the working environment. It checks dependencies and imports, then waits
for its own RivenLens process to close normally. It does not force-close the app.
The old source is backed up before replacement. Installation failures restore
that backup and reopen the previous version where possible. Successfully installed
releases reopen automatically with capture paused and saved settings unchanged.

Update files, logs and backups are in
`%LOCALAPPDATA%\Arbitrations\RivenLens Native\updates`. They are not published.
If power loss or a filesystem error interrupts replacement, the launcher blocks
startup instead of opening a partially installed app and identifies the recovery
folder. That folder contains `backup/journal.json` and the previous source files.
Do not remove it until recovery is complete. Backups and previous runtimes are
retained, not automatically purged.

Publish source and required assets only. Do not include `native/.venv`,
`native/.runtimes`, `native/runtime.json`, `native/update-pending.json`, Python
caches, preferences, personal sounds or development material. The updater ignores
bundled runtime directories and machine-specific state. Keep dependencies pinned
in root `requirements.txt`; automatic dependency updates require binary wheels.
