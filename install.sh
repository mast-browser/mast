#!/bin/sh
# Install or update Mast on Linux:
#   curl -fsSL https://github.com/mast-browser/mast/releases/latest/download/install.sh | sh
# Uses the .deb (apt) or .rpm (dnf, zypper) when it can, so the system installs missing
# libraries, and adds Mast's package source: updates then come with the system's own.
# Anywhere else it unpacks Mast into ~/.local; Mast says when there's an update, and running
# this again installs it.
# Every download is checked against SHA256SUMS, and SHA256SUMS against Mast's release key
# (below, inside this script) when GnuPG is installed.
# Remove Mast:  curl -fsSL https://github.com/mast-browser/mast/releases/latest/download/install.sh | sh -s -- --uninstall
set -eu

BASE=${MAST_BASE:-https://github.com/mast-browser/mast/releases/latest/download}
DIR=$HOME/.local/opt/mast # used only when there's no apt, dnf or zypper
APPS=${XDG_DATA_HOME:-$HOME/.local/share}/applications
ICONS=${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor

say() { printf '\033[1m› %s\033[0m\n' "$*"; }
die() { printf 'mast: %s\n' "$*" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }

if [ "$(id -u)" -eq 0 ]; then
	SUDO=""
elif has sudo; then
	SUDO=sudo
else
	SUDO=none
fi
root() {
	[ "$SUDO" != none ] || die "this step needs admin rights, and sudo isn't installed: $*"
	$SUDO "$@"
}

uninstall() {
	if has dpkg && dpkg -s mast >/dev/null 2>&1; then
		say "Removing the mast package"
		root apt-get remove -y mast
	elif has rpm && rpm -q mast >/dev/null 2>&1; then
		say "Removing the mast package"
		if has dnf; then root dnf remove -y mast; else root zypper --non-interactive remove mast; fi
	fi
	rm -rf "$DIR" "$APPS/mast.desktop" "$HOME/.local/bin/mast"
	rm -f "$ICONS"/*/apps/mast.png
	say "Mast is removed. Your profile is still in ~/.config/mast; delete it to remove everything."
}

[ "${1:-}" = --uninstall ] && { uninstall; exit 0; }
[ "$(uname -m)" = x86_64 ] || die "Mast is only built for 64-bit Intel/AMD PCs (x86_64) so far."

if has curl; then
	fetch() { curl -fL --retry 3 --progress-bar -o "$2" "$BASE/$1" || die "couldn't download $1"; }
elif has wget; then
	fetch() { wget -q --show-progress -O "$2" "$BASE/$1" || die "couldn't download $1"; }
else
	die "needs curl or wget to download Mast"
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
chmod 755 "$TMP" # apt reads the .deb as its own user
fetch SHA256SUMS "$TMP/SHA256SUMS"
fetch SHA256SUMS.asc "$TMP/SHA256SUMS.asc"
cat > "$TMP/mast-release-key.asc" <<'KEY'
-----BEGIN PGP PUBLIC KEY BLOCK-----

mDMEav29uxYJKwYBBAHaRw8BAQdANcVBxTGyVPyEfFEiyy/KLNjyff4aFJjbjg/5
InZ9xx60KU1hc3QgUmVsZWFzZXMgPHJlbGVhc2VzQG1hc3QtYnJvd3Nlci5jb20+
iJYEExYKAD4WIQQbxsdemic+tuVwTXv4cbzcXFka+wUCav29uwIbAwUJBaOagAUL
CQgHAgYVCgkICwIEFgIDAQIeAQIXgAAKCRD4cbzcXFka+9BOAQCpCJHtZqslvtgc
K1lWKlfX3VHu8Y56RzPkCyhEi7I5mgD+MCTJKll9Ac7LYir3Hd90hkoIKqpp0vYR
bl0+qEg1Rw0=
=d9nn
-----END PGP PUBLIC KEY BLOCK-----
KEY
if has gpg; then
	mkdir -m 700 "$TMP/gnupg"
	GNUPGHOME=$TMP/gnupg gpg --batch --quiet --import "$TMP/mast-release-key.asc" 2>/dev/null
	GNUPGHOME=$TMP/gnupg gpg --batch --quiet --verify "$TMP/SHA256SUMS.asc" "$TMP/SHA256SUMS" 2>/dev/null ||
		die "the download isn't signed by Mast's release key. Nothing was installed."
else
	say "GnuPG isn't installed, so downloads are checked against their checksums only"
fi
VERSION=$(sed -n 's/^# version //p' "$TMP/SHA256SUMS")
get() {
	say "Downloading $1"
	fetch "$1" "$TMP/$1"
	(cd "$TMP" && grep "  $1\$" SHA256SUMS | sha256sum -c --quiet -) ||
		die "the download of $1 is damaged. Please run the installer again."
}

if has apt-get && has dpkg; then
	get mast_amd64.deb
	say "Installing (asks for your password)"
	# apt only knows packages from lists it has downloaded: a fresh or minimal system has none,
	# and then even libcairo2 "isn't installable". dnf and zypper refresh by themselves.
	root apt-get update -qq || true
	root apt-get install -y "$TMP/mast_amd64.deb"
elif has dnf; then
	get mast.x86_64.rpm
	say "Installing (asks for your password)"
	root rpm --import "$TMP/mast-release-key.asc"
	root dnf install -y "$TMP/mast.x86_64.rpm"
elif has zypper; then
	get mast.x86_64.rpm
	say "Installing (asks for your password)"
	root rpm --import "$TMP/mast-release-key.asc"
	root zypper --non-interactive install "$TMP/mast.x86_64.rpm"
else
	get mast-linux-x86_64.tar.xz
	say "Unpacking into $DIR"
	mkdir -p "$DIR.new"
	tar -xJf "$TMP/mast-linux-x86_64.tar.xz" -C "$DIR.new"
	rm -rf "$DIR"
	mv "$DIR.new" "$DIR"
	mkdir -p "$HOME/.local/bin" "$APPS"
	ln -sf "$DIR/chrome" "$HOME/.local/bin/mast"
	sed "s|/opt/mast/|$DIR/|g" "$DIR/mast.desktop" > "$APPS/mast.desktop"
	for s in 24 48 64 128 256; do
		[ -f "$DIR/product_logo_$s.png" ] || continue
		mkdir -p "$ICONS/${s}x$s/apps"
		cp "$DIR/product_logo_$s.png" "$ICONS/${s}x$s/apps/mast.png"
	done
	has update-desktop-database && update-desktop-database -q "$APPS" || true
	# Chromium sandboxes pages with user namespaces. Where the system blocks them, it needs
	# its setuid helper instead, which only an admin can set up.
	if ! { has unshare && unshare -Ur true 2>/dev/null; } && [ -e "$DIR/chrome-sandbox" ]; then
		say "Setting up Mast's security sandbox (asks for your password)"
		root chown root:root "$DIR/chrome-sandbox"
		root chmod 4755 "$DIR/chrome-sandbox"
	fi
	case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) say "Tip: add ~/.local/bin to your PATH to start Mast with the mast command." ;; esac
fi

say "Mast ${VERSION:-} is installed. Open it from your app menu, or run: mast"
