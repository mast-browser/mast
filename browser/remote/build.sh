#!/usr/bin/env bash
# Build Mast on the builder. Each stage only reruns when its inputs change:
#   1. sync Chromium to the pinned version
#   2. the de-Googled base (ungoogled-chromium core patches + domain substitution)
#   3. Mast's hook patches, regenerated against that base
#   4. Mast's own code, pages and blocklist; build; package.
set -euo pipefail
export PATH=$HOME/depot_tools:$PATH
M=$HOME/mast
C=$HOME/chromium
S=$C/src
OUT=$S/out/Mast
VERSION=$(cat "$M/chromium_version.txt")
UG_TAG=$(cat "$M/ungoogled_version.txt")
say() { printf '\n== %s\n' "$*"; }

cd "$C"

# 1. Chromium version
if [ "$(cat .mast-version 2>/dev/null)" != "$VERSION" ]; then
	say "Syncing Chromium $VERSION (slow the first time)"
	git -C src reset --hard -q || true
	rm -f .mast-base .mast-patches
	# Fetch only this tag, then sync by commit hash: gclient skips its own fetch when the
	# commit is already present (a tag ref makes it fetch every branch with full history).
	git -C src fetch --depth 1 origin "+refs/tags/$VERSION:refs/tags/$VERSION"
	REV=$(git -C src rev-parse "refs/tags/$VERSION^{commit}")
	gclient sync --nohooks --no-history -D --force --reset --revision "src@$REV"
	gclient runhooks
	echo "$VERSION" > .mast-version
fi

# 2. De-Googled base, committed in src so Mast's patches have a fixed starting point
BASE=$( { echo "$VERSION $UG_TAG"; cat "$M/patches/ungoogled-drop.list" "$M/patches/ungoogled-extra.list" \
	"$M/patches/substitution-exclude.list" \
	"$M/remote/ungoogle.sh"; } | sha256sum | cut -d' ' -f1)
if [ "$(cat .mast-base 2>/dev/null)" != "$BASE" ]; then
	bash "$M/remote/ungoogle.sh"
	echo "$BASE" > "$C/.mast-base"
	rm -f "$C/.mast-patches"
fi
cd "$C"

# 3. Mast's hook patches: regenerated from make-patches.py against the base (it reads git
# HEAD, not the working tree), so they always match. The generated files are copied into
# patches/ and pulled back to the repo. Re-applied from a clean base only when they change.
python3 "$M/remote/make-patches.py"
cp /root/pw/*.patch "$M/patches/"
mapfile -t PATCHES < <(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$M/patches/series")
SUM=$( { cat "$M/patches/series"; for p in "${PATCHES[@]}"; do cat "$M/patches/$p"; done; } | sha256sum | cut -d' ' -f1)
if [ "$(cat .mast-patches 2>/dev/null)" != "$SUM" ]; then
	say "Applying ${#PATCHES[@]} Mast patch(es)"
	python3 "$M/remote/keep-mtimes.py" save "$S" /root/mast-mtimes.json
	git -C src reset --hard -q
	for p in "${PATCHES[@]}"; do
		echo "  $p"
		git -C src apply -p1 "$M/patches/$p"
	done
	echo "$SUM" > .mast-patches
fi

# 4. Mast's own code (rsync keeps mtimes, so unchanged files don't rebuild), and
# whole-file overrides of upstream files (branding, icons). --checksum so an
# override only touches mtimes when its content really differs.
mkdir -p "$S/mast"
rsync -a --delete "$M/src/mast/" "$S/mast/"
if [ -d "$M/src/overrides" ]; then
	rsync -a --checksum "$M/src/overrides/" "$S/"
fi
# Mast's HTML pages and the blocklist -> C++ strings, in the generated-sources dir so the
# rsync of src/mast above never deletes them (unchanged files keep their mtime).
python3 "$M/remote/embed-pages.py" "$S/mast/browser/resources" "$OUT/gen/mast/browser/resources" \
	"$M/blocklist/blocklist.json" "$S/mast/browser/resources/youtube_music.js"
# Product name in every UI string file (only rewrites files that change).
python3 "$M/remote/substitute-names.py" "$S"
# Files that came back byte-identical keep their old timestamps (no needless recompiles).
python3 "$M/remote/keep-mtimes.py" restore "$S" /root/mast-mtimes.json
# The blocklist must never have gone through domain substitution.
if grep -q qjz9zk "$M/blocklist/blocklist.json" "$S"/mast/browser/*.cc; then
	echo "domain substitution reached Mast's own code"; exit 1
fi

# 5. Configure + build: ungoogled-chromium's flags, then Mast's (Mast wins on conflicts).
mkdir -p "$OUT"
python3 "$M/remote/merge-args.py" "$OUT/args.gn" "$HOME/ungoogled/flags.gn" "$M/remote/args.gn"
cd "$S"
say "gn gen"
gn gen "$OUT"
# Preflight: compile Mast's own sources first, so a mistake there fails in minutes rather
# than after the whole browser has built.
# (Some compile inside upstream targets, e.g. the toolbar button, so match by file name.)
mapfile -t OWN < <(ninja -C "$OUT" -t targets all 2>/dev/null | grep -oE '^obj/[^:]*mast_[a-z_]+\.o' | sort -u)
if ((${#OWN[@]})); then
	say "Preflight: ${#OWN[@]} Mast object(s)"
	autoninja -C "$OUT" -k 0 "${OWN[@]}"
fi
say "Building chrome"
# -k 0: keep going past failures so one pass reports every error.
# chrome_sandbox: the setuid helper the packages install as chrome-sandbox, for distros that
# restrict unprivileged user namespaces (Ubuntu 24.04+, Linux Mint 22).
time autoninja -C "$OUT" -k 0 chrome chrome_sandbox

# 6. Package only what the browser needs at runtime
# The adult and gambling feeds: data files beside the browser, read at startup (mast_feeds.cc).
say "Feeds"
python3 "$M/remote/build-feeds.py" "$M/blocklist" "$OUT/mast-feeds" /root/mast-feeds-cache
# Customize's built-in pictures (wallpapers/CREDITS.md), beside the browser too.
rm -rf "$OUT/mast-wallpapers" && mkdir -p "$OUT/mast-wallpapers" && cp "$M"/wallpapers/*.jpg "$OUT/mast-wallpapers/"
# Inter, Mast's UI and page font (fonts/), for systems that don't have it: the browser adds
# this folder to its fonts at startup (ui/gfx/linux/fontconfig_util.cc, mast-theme.patch).
# Its licence (SIL OFL 1.1) travels with it.
rm -rf "$OUT/mast-fonts" && mkdir -p "$OUT/mast-fonts" && cp "$M"/fonts/* "$OUT/mast-fonts/"
say "Packaging"
cd "$OUT"
# Chromium looks for the helper beside the browser under this name.
cp -p chrome_sandbox chrome-sandbox
keep=()
for f in chrome chrome-sandbox chrome_crashpad_handler icudtl.dat vk_swiftshader_icd.json locales MEIPreload \
	mast-feeds mast-wallpapers mast-fonts *.pak *.bin *.so *.so.*; do
	[ -e "$f" ] && keep+=("$f")
done
mkdir -p "$M/dist"
echo "$VERSION" > "$M/dist/VERSION"
tar --zstd -cf "$M/dist/mast-linux.tar.zst" "${keep[@]}"
ls -lh "$M/dist/mast-linux.tar.zst"
say "Done"
