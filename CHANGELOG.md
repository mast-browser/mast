# Changelog

What's new in each Mast release. Add a line under **Unreleased** as things land;
`./mast publish` puts that list in the GitHub release's notes, then files it under the
release's version (docs/RELEASING.md).

## Unreleased

- NixOS: install Mast from its flake (`github:mast-browser/mast`) and update it with the rest of the system. See the README.
## 0.1.0 (Chromium 157.0.8103.78)

- New version numbers: this is Mast 0.1.0, built on Chromium 157.0.8103.78. Releases count up from here (0.1.1, 0.1.2 …) instead of carrying Chromium's version and a timestamp.
- Settings → About shows Mast's version beside Chromium's: "Version 0.1.0 (Chromium 157.0.8103.78)".
- Truer-colour scans of *Among the Sierra Nevada* and *Ulysses Deriding Polyphemus* for the built-in backgrounds.
- While a website loads, the page area takes the tab bar's colour (the site's own with Match site) instead of Chromium's grey.
- Customize no longer credits NASA when no built-in background is picked; a built-in painting still shows its credit.
- The new tab page stays Mast's with any search engine. Brave Search and Bing used to replace it with their own.
- Profiles are hidden for now: no profile row in the menu, and no adding profiles or guest mode.
- The welcome screen shows its picture as it is, with no dark wash that set the page apart from the tab bar.

## 157.0.8103.78-202611191046

- New built-in backgrounds, all public-domain paintings: *Among the Sierra Nevada* by Albert Bierstadt (the new default), *Ulysses Deriding Polyphemus* by J. M. W. Turner and *The Ninth Wave* by Ivan Aivazovsky. They replace the NASA photos.
- Icons on the new tab page lie flat, like on a desktop, with a lighter shadow under their names.
- Importing from Firefox puts its toolbar's sites on the new tab page and everything else in a "From Mozilla Firefox" folder, like the other browsers. Firefox bookmarks used to go into a bookmarks bar Mast doesn't show.
- Importing from Firefox while it's open now asks you to close it first, instead of reporting an import that didn't happen.
- Import finds Chrome, Brave, Edge, Vivaldi, Opera and Chromium installed as a Snap or Flatpak (Ubuntu's Chromium is a Snap).
- openSUSE: Mast tells you when a new version is out, with the command that updates it.

## 157.0.8103.78-202611190537

The first public release, for Linux.

- **Block**: pick a length of time and every distraction category closes until it's over.
- **YouTube your way**: allow it, block Shorts, allow music only, or block it all.
- **Your own categories**: add sites to Mast's lists, or make lists of your own.
- **Your sites on the new tab**: the star saves a site there, on a background you pick.
- **Bring everything over**: bookmarks, history and extensions from Chrome, Brave, Edge, Vivaldi or Opera, in one click.
- **No Google by default**: built on ungoogled-chromium; Chrome extensions still work, and Widevine (Spotify, Netflix) downloads only if you say yes.
- **Updates with your system**: the .deb and .rpm add Mast's signed package source for apt and dnf.
