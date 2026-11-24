// Copyright 2026 The Mast Authors

#ifndef MAST_BROWSER_UI_MAST_UPDATE_CHECK_H_
#define MAST_BROWSER_UI_MAST_UPDATE_CHECK_H_

namespace mast {

// "A new Mast is out", where the system doesn't update Mast itself: the tarball install
// (install.sh's ~/.local/opt/mast) and openSUSE (zypper can't use Mast's source). The .deb
// and .rpm add Mast's package source for apt and dnf, which update those
// (packaging/package.sh), and Nix updates NixOS installs (nix/package.nix). Once a day,
// Mast fetches latest.json from its GitHub releases
// and compares it with the mast-version file beside the browser. When the release is
// newer, the current tab says so, with the command that updates (install.sh again) one
// click from the clipboard. Mast never replaces its own files while it runs.
// The request carries no cookies; GitHub sees an address and nothing else.
class MastUpdateCheck {
 public:
  // Once, from the first normal window (mast_one_row_toolbar.cc).
  static void Start();
};

}  // namespace mast

#endif  // MAST_BROWSER_UI_MAST_UPDATE_CHECK_H_
