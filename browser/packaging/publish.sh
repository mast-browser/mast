#!/usr/bin/env bash
# Put the signed release in dist/release (./mast package) online:
#   - a GitHub release in MAST_RELEASES_REPO (this repo, which must be public), uploaded as
#     a draft and made "latest" only once every file is there, so the apt source and the
#     download links switch to it all at once;
#   - the dnf/zypper source (rpm/repodata), the public key and the installer on the repo's
#     GitHub Pages site (MAST_PAGES_URL), from its gh-pages branch, which holds nothing else.
#     Its metadata points at this release's own folder, so older metadata never points at a
#     newer file.
# Needs gh, logged in (gh auth login). Refuses an unsigned release.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=../mast.env
source "$ROOT/mast.env"
OUT=$ROOT/dist/release
REPO=$MAST_RELEASES_REPO

die() { echo "mast publish: $*" >&2; exit 1; }
log() { printf '\033[1;33m›\033[0m %s\n' "$*"; }

command -v gh >/dev/null || die "gh not found"
gh auth status >/dev/null 2>&1 || die "gh isn't logged in (gh auth login)"
[[ -f $OUT/TAG && -f $OUT/CHROMIUM ]] || die "no release in dist/release. Run: ./mast package"
TAG=$(cat "$OUT/TAG")
FULL=${TAG#v}
CHROMIUM=$(cat "$OUT/CHROMIUM")
TITLE="Mast $FULL (Chromium $CHROMIUM)"
for f in mast_amd64.deb mast.x86_64.rpm mast-linux-x86_64.tar.xz install.sh SHA256SUMS SHA256SUMS.asc \
	Packages Packages.gz Release InRelease Release.gpg latest.json mast-release-key.asc \
	rpm/repodata/repomd.xml rpm/repodata/repomd.xml.asc; do
	[[ -f $OUT/$f ]] || die "dist/release/$f is missing (an unsigned or unfinished build?)"
done
gpg --verify "$OUT/SHA256SUMS.asc" "$OUT/SHA256SUMS" 2>/dev/null || die "SHA256SUMS isn't signed by your key"

[[ $(gh repo view "$REPO" --json visibility -q .visibility 2>/dev/null) == PUBLIC ]] ||
	die "$REPO must be public: apt, dnf and the installer download from it without logging in"
gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1 && die "$TAG is already published"

# The notes (packaging/notes.py): install, what's new from CHANGELOG.md, Chromium, checks.
CHANGES=$ROOT/../CHANGELOG.md
notes_py() { uv run --no-project --quiet python "$ROOT/packaging/notes.py" "$@"; }
# The Chromium the last release was built on, from its title: "Mast 0.1.0 (Chromium 157.0.8103.78)",
# or the first releases' "Mast 157.0.8103.78-202611191046".
prev=$(gh release view --repo "$REPO" --json name -q .name 2>/dev/null |
	grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
notes=$(mktemp)
notes_py notes "$CHANGES" "$CHROMIUM" "$(cat "$ROOT/ungoogled_version.txt")" "$REPO" "$prev" > "$notes" ||
	{ rm -f "$notes"; die "no release notes (see above)"; }

log "uploading $TAG (a draft until every file is there)"
assets=(mast_amd64.deb mast.x86_64.rpm mast-linux-x86_64.tar.xz install.sh SHA256SUMS SHA256SUMS.asc
	Packages Packages.gz Release InRelease Release.gpg latest.json mast-release-key.asc)
(cd "$OUT" && gh release create "$TAG" --repo "$REPO" --draft --title "$TITLE" --notes-file "$notes" "${assets[@]}")
rm -f "$notes"
gh release edit "$TAG" --repo "$REPO" --draft=false --latest >/dev/null
log "published: https://github.com/$REPO/releases/tag/$TAG"

# CHANGELOG.md: this release's list goes under its version, and "Unreleased" starts empty.
# nix/release.json: the Nix package (nix/package.nix) installs this release from now on.
notes_py file "$CHANGES" "$FULL (Chromium $CHROMIUM)"
uv run --no-project --quiet python "$ROOT/packaging/nix_release.py" "$OUT/mast-linux-x86_64.tar.xz" "$FULL" "$REPO" \
	"$ROOT/../nix/release.json"
git -C "$ROOT/.." commit -q -m "release: $FULL (CHANGELOG, Nix package)" -- CHANGELOG.md nix/release.json &&
	log "CHANGELOG.md and nix/release.json: $FULL, committed (push it with the rest)"

# GitHub Pages: the gh-pages branch holds the dnf/zypper source and a copy of the key and
# installer. Rewritten each release (only the newest metadata is ever needed).
log "updating the dnf/zypper source on $MAST_PAGES_URL"
site=$(mktemp -d)
trap 'rm -rf "$site"' EXIT
cp -r "$OUT/rpm" "$site/rpm"
cp "$OUT/mast-release-key.asc" "$OUT/install.sh" "$OUT/latest.json" "$site/"
touch "$site/.nojekyll" # serve every file as it is
cat > "$site/index.html" <<EOF
<!doctype html><meta charset="utf-8"><title>Mast</title>
<p>Mast $FULL. Install or update on Linux:</p>
<pre>curl -fsSL https://github.com/$REPO/releases/latest/download/install.sh | sh</pre>
<p><a href="https://github.com/$REPO/releases/latest">All downloads</a> ·
<a href="mast-release-key.asc">Release key</a> (F871BCDC5C591AFB)</p>
EOF
(
	cd "$site"
	git init -q -b gh-pages
	git add -A
	git -c user.name="Mast Releases" -c user.email=releases@mast-browser.com commit -qm "Mast $FULL"
	# The same way this repo's own pushes go (its origin's address, and so its credentials).
	url=https://github.com/$REPO.git
	origin=$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)
	[[ $origin == *"$REPO"* ]] && url=$origin
	git push -q --force "$url" gh-pages
)
if ! gh api "repos/$REPO/pages" >/dev/null 2>&1; then
	gh api -X POST "repos/$REPO/pages" -f "source[branch]=gh-pages" -f "source[path]=/" >/dev/null
	log "GitHub Pages turned on (the first deploy can take a few minutes)"
fi
log "done. apt and dnf users get $FULL with their next system update."
