# Releasing Mast (Linux)

A release is packaged, signed and published on GitHub (.github/workflows/release.yml), so
nothing large goes through your connection:

```
# CHANGELOG.md: list what's new under "## Unreleased", commit and push (main is what's read)
MAST_SKIP_FETCH=1 ./mast build && ./mast shots && ./mast release; ./mast down --keep
```

`./mast release` serves the builder's build through a temporary Cloudflare tunnel at a
secret address and starts the workflow with that address and the build's SHA-256. The
workflow's first job downloads the build and checks it. The second runs in the `release`
environment, which holds the key (`MAST_SIGNING_KEY`, `MAST_SIGNING_PASSPHRASE`) and only
main can use: it runs `package.sh` and `publish.sh` and pushes the CHANGELOG commit to main.
`./mast release` waits for it (the idle builder costs about $0.02 meanwhile), says where the
release is, and pulls that commit. `--dry-run` signs but keeps the files in the run instead.

By hand, from the laptop (the way it worked before, and still works):

```
./mast build      # downloads the build into dist/mast
./mast package    # dist/release: .deb, .rpm, tarball, installer, apt/dnf sources; signed
./mast publish    # GitHub release + GitHub Pages; apt and dnf users update from there
```

The release's notes come from `packaging/notes.py`, laid out like Brave's: the install
command and which file is for which system, **What's new** (the Unreleased list), the
Chromium it's built on (with Chromium's changes since the last release, when it moved), and
how to verify the files. Afterwards publish files that list under the release's version in
CHANGELOG.md and commits it.

`package` asks for the release key's passphrase once. The key is `MAST_SIGNING_KEY` in
`mast.env` (F871BCDC5C591AFB, "Mast Releases"). It never leaves your GnuPG: the RPM's
signature needs the key as a file, so it's exported for a few seconds into `/run/user`
(memory, not disk) and removed. `MAST_UNSIGNED=1 ./mast package` makes a local test build,
which `publish` refuses.

## Version numbers

Mast numbers its releases like Brave: **Mast 0.1.0 (Chromium 157.0.8103.78)**, tag `v0.1.0`.
`browser/mast_version.txt` holds MAJOR.MINOR (`0.1`); `package.sh` counts PATCH up from the
tags already published in that series, so releases go 0.1.0, 0.1.1, 0.1.2 … on their own.
Bump the file (`0.2`) for a bigger release and the next one is 0.2.0. The Chromium version
goes beside it, in the release title and the CHANGELOG heading.

The first four releases were numbered after Chromium (`157.0.8103.78-202611191046`), so the
packages carry **epoch 1** (`1:0.1.0-1`): without it apt and dnf would rank 0.1.0 below them.
The epoch stays 1 for good. Tarball installs from those four releases compare plain version
numbers, so their daily check never reports 0.x; running install.sh again moves them over.

## How each install gets updates

| Install | How it updates | What checks it |
|---|---|---|
| `.deb` (Ubuntu, Debian, Mint, Pop!_OS) | With the system's updates. The package installs `/etc/apt/sources.list.d/mast.sources` and the key in `/usr/share/keyrings/mast.asc` | apt verifies `InRelease` against the key, then each file's SHA256 |
| `.rpm` (Fedora, RHEL-likes) | With the system's updates: the package installs `/etc/yum.repos.d/mast.repo`, whose `gpgkey=` dnf trusts (install.sh imports it beforehand) | Every RPM is signed |
| `.rpm` on openSUSE | Mast's daily check, like the tarball (zypper can't use the source) | The same signed RPM |
| Tarball (`~/.local/opt/mast`) | Mast checks `latest.json` once a day and says when a release is newer, with the update command to copy (install.sh again) | install.sh verifies `SHA256SUMS.asc` with the key built into it (when GnuPG is installed), then each file's SHA256 |
| NixOS (the repo's flake, `nix/package.nix`) | `nix flake update mast`, then `nixos-rebuild switch`: publish.sh commits each release's version and tarball hash to `nix/release.json`, so main always names the newest. Mast's own check stays off (the store is read-only) | Nix checks the tarball against the hash in `nix/release.json` |

## Where things are served from

- **The GitHub release** (`MAST_RELEASES_REPO`: this repo, public): the packages, the installer,
  `SHA256SUMS(.asc)`, `latest.json`, and the apt source itself: a flat repository
  (`Packages`, `Release`, `InRelease`) read through `releases/latest/download/`. A release
  is uploaded as a draft and made "latest" only once complete, so its files switch together.
- **GitHub Pages** (`MAST_PAGES_URL`, the repo's `gh-pages` branch, which holds only this): the dnf metadata (`rpm/repodata`), which
  can't be a release file because it lives in a folder. It points at the RPM in its own
  release's folder (`releases/download/<tag>/`), never at "latest", so old metadata never
  names a newer file. Also a copy of the key and the installer.

## The key

- A copy lives in GitHub, as the `release` environment's secrets, for the workflow. Anyone
  who takes over your GitHub account (or gets write access to the repo) could start a signed
  release, so keep two-factor on and write access to yourself.
  If the key is ever replaced, update both secrets: `gpg --armor --export-secret-keys KEY |
  gh secret set MAST_SIGNING_KEY --env release`, and `gh secret set MAST_SIGNING_PASSPHRASE --env release`.
- Back up the private key and its revocation certificate offline (password manager or a
  USB stick). Lose the key, and every install has to be told to trust a new one.
- It expires on 2029-11-17. Extend it before then: `gpg --quick-set-expire F871BCDC5C591AFB 3y`,
  then export the public key again into `packaging/mast-release-key.asc` and release.
- If it's ever stolen: publish the revocation certificate, make a new key, and ship a
  release (signed with the old key, while it still works) that installs the new one.

## Tested on fresh Hetzner servers (2026-11-19)

| Distro | README install | Update arrives (first release → second) |
|---|---|---|
| Fedora 44 | ✓ | ✓ `dnf upgrade`, signature checked |
| Rocky Linux 9 | ✓ | ✓ `dnf upgrade` (rpm 4.16 accepts ed25519) |
| Debian 13 | ✓ since the t64 fix | ✓ `apt upgrade` from the signed source |
| Ubuntu 24.04, 26.04 | ✓ since the t64 fix and `apt-get update` first | apt reads the signed source (same as Debian) |
| openSUSE Leap 16 | ✓ | ✗ zypper won't fetch a package from another host than its metadata (GitHub Pages vs releases), so the .rpm no longer adds a zypper source; Mast's own daily check tells openSUSE users (from the build after 157.0.8103.78-202611190455) |

Two bugs these found, both fixed: the .deb needed Debian's "t64" library names, and on a
system that never downloaded its package lists (fresh or minimal) apt knew no packages
at all, so install.sh runs `apt-get update` first.

## Known limits

- Older rpm releases (openSUSE Leap 15, RHEL/Alma/Rocky 8) may not verify ed25519 signatures.
  Fedora 44, Rocky 9 and openSUSE Leap 16 do (tested).
- `chrome --headless --dump-dom` never finishes (any profile, any distro, the laptop too):
  something in Mast's start-up waits for a window. The normal windowed start is fine.
- dnf doesn't check the repository metadata's signature (`repo_gpgcheck=0`, like Google
  Chrome's source): dnf keeps those keys apart from rpm's and would stop to ask about the key,
  which background updaters can't answer. Every package it installs is still signature-checked.
