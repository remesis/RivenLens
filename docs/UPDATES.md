# Release updates

Release copies check [remesis/RivenLens](https://github.com/remesis/RivenLens)
once at startup. A newer stable release prompts the user. **No** dismisses it for
that launch. **Yes, update** downloads, installs and reopens RivenLens with
capture paused and saved settings unchanged. Git checkouts update through Git.
Linux shows a release-page link for manual updates; the automatic installer is
Windows-only. Optional RapidOCR packages and models stay in the user's app-data
directory and are not bundled in release ZIPs.

Windows versions 0.2.13 and earlier cannot install the renamed launchers directly.
Install the [0.2.15 compatibility release](https://github.com/remesis/RivenLens/releases/tag/v0.2.15)
first, then update normally, or extract the latest ZIP into a new folder.

Versions 0.1.0 and 0.1.1 used GitHub's generated source archive, which has no
managed-file manifest. For those versions, download **RivenLens.zip** once and
extract it into a new folder. Settings and sounds remain available. This gives
future updates the verified file list required by the current installer.

## Publishing

1. Set `version`, `repository` and the exact `asset_name` (`RivenLens.zip`) in
   `native/data/release.json`. An empty repository or asset name disables checks.
2. Review and commit the release. From the clean checkout, run
   `python docs/build_release.py C:\release-output\RivenLens.zip`, choosing an
   existing output folder outside the checkout.
3. Publish a non-prerelease GitHub Release with the matching `vX.Y.Z` tag and
   attach that ZIP. The builder includes committed application files and a
   checksum manifest, excluding local environments, settings and caches. It reads
   Git's committed archive, not checkout bytes, and fixes ZIP timestamps so local
   line-ending settings and file dates do not change the package.

Screenshots and full-resolution branding under `assets/` stay in the repository,
outside the application ZIP. A smaller local logo is bundled for offline use.

The checker requires the named asset, a positive size and GitHub's SHA-256 digest.
It does not fall back to GitHub's generated source ZIP. Source downloads and copies
with modified application files show a manual-install notice instead of offering
an unusable download. Extract `RivenLens.zip` into a new folder; saved settings and
sounds stay available.

## Protection and recovery

Downloads use approved GitHub HTTPS hosts with size and timeout limits. The
installer verifies the archive's size and hash again before extraction, then
checks its paths, manifest, Python syntax, version and repository. These hashes
detect corruption; they are not an independent publisher signature.

Only files listed in the installed manifest can be replaced or removed. Modified
application files, Git checkouts and collisions with unrelated local files stop
the update. Other local files are left alone. Dependencies are fully pinned with
wheel hashes in `requirements.txt`; changed requirements get a separate runtime
without altering the working one. Dependency and import checks run before the app
closes normally. The updater never force-closes RivenLens.

Cancel is available during download and preparation. Once replacement starts,
let it finish. Installation failures restore the verified backup where possible.
The app and installer share an OS-released file lock. No saved process number is
used to decide whether RivenLens is open. The backup journal is saved before any
replacement begins.

After an interrupted update, the launcher offers recovery before starting the app.
Recovery refuses an active update or missing, damaged or mismatched recovery data.
A completed update is verified and never rolled back just because its pending
marker remains. A malformed marker is reported rather than guessed or deleted.

Update logs and backups are kept in
`%LOCALAPPDATA%\Arbitrations\RivenLens Native\updates`. Keep the indicated recovery
folder until recovery is complete. Backups and previous runtimes are retained,
not automatically purged. Nothing in this process accesses Warframe.
