#!/usr/bin/env bash
# Build the base tree Mast's patches go on. base.txt picks it (docs/BASE-PLAN.md):
#   chromium:  pristine Chromium at the pinned tag (Mast's privacy layer is one of its patches)
#   ungoogled: what Mast shipped up to 0.1.0, as follows.
# The ungoogled base: pristine Chromium at the pinned tag, ungoogled-chromium's
# core patches (minus patches/ungoogled-drop.list) plus the few "extra" patches in
# patches/ungoogled-extra.list, then domain substitution (minus
# patches/substitution-exclude.list). The result is committed in src as the base that
# Mast's own patches are written and applied against. Order matters (substitution can't be
# undone): reset -> patch -> substitute -> commit. See docs/DEGOOGLE.md.
set -euo pipefail
export PATH=$HOME/depot_tools:$PATH
M=$HOME/mast
C=$HOME/chromium
S=$C/src
UG=$HOME/ungoogled
VERSION=$(cat "$M/chromium_version.txt")
UG_TAG=$(cat "$M/ungoogled_version.txt")
BASE=$(cat "$M/base.txt")
say() { printf '\n== %s\n' "$*"; }
command -v patch >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q patch >/dev/null

if [ "$BASE" = ungoogled ] && [ "$(git -C "$UG" describe --tags --exact-match 2>/dev/null)" != "$UG_TAG" ]; then
	say "Fetching ungoogled-chromium $UG_TAG"
	rm -rf "$UG"
	git clone -q --depth 1 --branch "$UG_TAG" https://github.com/ungoogled-software/ungoogled-chromium.git "$UG"
fi
[ "$BASE" != ungoogled ] || [ "$(cat "$UG/chromium_version.txt")" = "$VERSION" ] ||
	{ echo "ungoogled $UG_TAG is for Chromium $(cat "$UG/chromium_version.txt"), we pin $VERSION"; exit 1; }

say "Resetting to pristine Chromium $VERSION"
REV=$(git -C "$S" rev-parse "refs/tags/$VERSION^{commit}")
git -C "$S" checkout -qf --detach "$REV"
git -C "$S" clean -fdq -e /mast -e /out
cd "$C"
# --reset puts every dependency repo back too (substitution edits files across them).
gclient sync --nohooks --no-history -D --force --reset --revision "src@$REV"

if [ "$BASE" = chromium ]; then
	say "Committing the base: pristine Chromium $VERSION"
	cd "$S"
	grep -qxF /mast .git/info/exclude 2>/dev/null || echo /mast >> .git/info/exclude
	git add -A
	git -c user.name=mast -c user.email=mast@localhost commit -qm "base: pristine Chromium $VERSION" --no-verify --allow-empty
	git log --oneline -1
	exit 0
fi

say "Applying ungoogled-chromium core patches"
cd "$S"
n=0
while read -r p; do
	p=${p%%#*}; p=${p//[[:space:]]/}
	[ -z "$p" ] && continue
	name=$(basename "$p")
	case $p in extra/*)
		grep -qxF "$name" <(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$M/patches/ungoogled-extra.list") || continue ;;
	esac
	if grep -qxF "$name" <(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$M/patches/ungoogled-drop.list"); then
		echo "  skip $p"
		continue
	fi
	patch -p1 --forward --no-backup-if-mismatch -s -i "$UG/patches/$p" || { echo "FAILED: $p"; exit 1; }
	n=$((n + 1))
done < "$UG/patches/series"
echo "  applied $n patches"

say "Domain substitution"
grep -v -F -f <(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$M/patches/substitution-exclude.list") \
	"$UG/domain_substitution.list" > /tmp/mast-domsub.list
echo "  $(wc -l < /tmp/mast-domsub.list) files ($(($(wc -l < "$UG/domain_substitution.list") - $(wc -l < /tmp/mast-domsub.list))) excluded)"
rm -f /tmp/mast-domsub-cache.tar.gz
python3 "$UG/utils/domain_substitution.py" apply -r "$UG/domain_regex.list" -f /tmp/mast-domsub.list \
	-c /tmp/mast-domsub-cache.tar.gz "$S"

say "Committing the base"
grep -qxF /mast .git/info/exclude 2>/dev/null || echo /mast >> .git/info/exclude
git add -A
git -c user.name=mast -c user.email=mast@localhost commit -qm "base: ungoogled-chromium $UG_TAG + domain substitution" --no-verify
git log --oneline -1
