# Mast's own base: leaving ungoogled-chromium, ready for Windows and macOS

Plan as of 2026-11-28. Nothing here is built yet. Brave is the reference throughout: facts
about it come from reading brave-core `master` at `1922673406` (1.102.48, 2026-11-27); paths
below are brave-core paths. What Mast does today is in [DEGOOGLE.md](DEGOOGLE.md).

## Why

Mast builds on pristine Chromium plus ungoogled-chromium's 36 core patches and its domain
substitution (`remote/ungoogle.sh`). That worked for Linux, but:

- **Mast keeps undoing it.** Google search (`ungoogled-drop.list`), the Web Store (two
  patches reversed with `unpatch()`), the component updater (`block-requests` reversed),
  Widevine's download address (`substitution-exclude.list`), Google's base URL
  (`mast-ungoogled.patch`). Network time and certificate revocation lists are next.
- **Every platform is another dependency.** Windows and macOS need
  `ungoogled-chromium-windows` and `-macos`, each its own volunteer project with its own lag.
- **Security fixes wait for ungoogled.** Mast can only move to a Chromium release once
  ungoogled has tagged it.
- **Brave doesn't use it.** It starts from pristine Chromium and switches off, keeps or
  reroutes each Google service one by one, on every platform with the same code.

Decisions already made: keep Google search, the Web Store and Widevine as today; leave Safe
Browsing off for now; Windows and macOS come later, from the same pipeline.

## What ungoogled-chromium gives Mast today (157.0.8103.78-1)

| Group | Patches | Changed lines |
|---|---|---|
| Building with Safe Browsing compiled out (`safe_browsing_mode=0`), and the sign-in/Safe Browsing prefs that go with it | `fix-building-without-safebrowsing` (124 files), `remove-unused-preferences-fields` (232 files), inox `0001-fix-building-without-safebrowsing`, iridium `safe_browsing-*` (2) | ≈5,600 of 7,900 |
| Switching off individual services | ~20, listed in the table below | ≈2,000 |
| Already undone or skipped by Mast | `replace-google-search-engine-with-nosearch`, `disable-webstore-urls`, inox `0005-disable-default-extensions`, `block-requests` (update checker) | — |
| Build fixes for ungoogled's own choices | `fix-building-with-prunned-binaries` (Mast doesn't prune), the mDNS fix, `missing-dependencies` | ≈90 |
| The safety net | domain substitution: 21 regexes over ≈17,900 files, and `block-trk-and-subdomains` | — |

**The biggest saving: compile Safe Browsing in, leave it off.** Brave builds with
`safe_browsing_mode = 1` (`build/args/desktop_defaults.gni`). Mast can too, with no API key
and the pref defaulting to off, so it makes no requests. The 5 patches and ≈5,600 lines above
then have nothing left to do, and turning Safe Browsing on later (through Google's API or a
Mast proxy, like Brave's `safebrowsing.brave.com`) becomes a setting, not a rebase.

## How Brave changes Chromium, and what Mast takes from it

Brave has three mechanisms (`docs/patching_and_chromium_src.md`, `docs/plaster.md`):

1. **`chromium_src/`** (2,009 files): an override at the same path as an upstream file.
   Headers win through an `-iquote` include path; `.cc` files are swapped in at compile time
   by a Siso build hook. Overrides use `#define` renames to wrap upstream functions.
2. **`patches/`** (1,513 files, one per upstream file), kept to one line where possible.
3. **Plaster** (`rewrite/**.yaml`, ≈1,036 rules): rewrite rules that *generate* most of those
   patches, with operations like `set_feature_flag_default_state`, `preempt_function_impl`
   and `insert_into_list`.

**Mast's `remote/make-patches.py` is already a small Plaster**: anchored edits that must match
exactly once, generating the patches against the base tree. Mast keeps it and adds
Plaster-style helpers instead of adopting `chromium_src/` (which needs build-system hooks
Mast doesn't have, and is worth it at Brave's scale, not at Mast's):

| Helper | Brave's equivalent | Does |
|---|---|---|
| `feature_off(path, "kName")` | `set_feature_flag_default_state` | Flips a `BASE_FEATURE` default to `FEATURE_DISABLED_BY_DEFAULT` |
| `stub(path, signature, body)` | `preempt_function_impl` | Returns early from one function (for services with no feature flag) |
| `add_dep(...)` | `insert_into_list` | Exists already |
| `switch_on_start("--name")` | switches in `app/brave_main_delegate.cc` | One Mast hook appends command-line switches at startup |
| Pref defaults | `OverrideDefaultPrefValues` in `browser/brave_profile_prefs.cc` | Exists already: `mast::RegisterProfilePrefs`, `mast::SetLocalStateDefaults` |

Plus Brave's readable record of what's off, `app/feature_defaults_unittest.cc`: Mast gets a
**`privacy.json`** listing every feature, switch, pref and stub with a one-line reason, which
`make-patches.py` reads (one source of truth) and `./mast check-patches` verifies.

## Service by service

"Mast" is the plan; "Brave" is what brave-core does today.

| Service | ungoogled | Brave | Mast |
|---|---|---|---|
| Field trials / variations seed | `disable-fetching-field-trials` | Rerouted to `variations.brave.com/seed`, signed with Brave's key; `disable_fieldtrial_testing_config = true` | **Off**: `disable_fieldtrial_testing_config = true` and stub the seed fetch (no Mast server needed) |
| X-Client-Data header | `disable-google-host-detection` (547 lines) | `GetClientDataHeaders()` returns `nullptr` (`chromium_src/components/variations/variations_ids_provider.*`) | **Stub**, as Brave (one function, not 10 files) |
| Domain reliability | `disable-domain-reliability` (627 lines) | `--disable-domain-reliability` switch | **Switch**, as Brave |
| Push messaging (GCM) | `disable-gcm` | Off by default, opt-in (`BraveGCMDriverDesktop`) | **Stub** the driver start; no opt-in yet |
| Sign-in (Gaia) | `disable-gaia` | `kLsoUrl` → `https://no-thanks.invalid`; sign-in pref off; cookie-jar accounts empty | **As Brave**: URL, pref, stub |
| Sync | (no keys) | Rerouted to Brave's server | **Off**: no keys, sign-in off |
| Crash reporting | `disable-crash-reporter` | Rerouted to `cr.brave.com` in official builds, empty otherwise | **Empty upload URL** |
| Metrics / UMA | (no keys, reporting off) | `IsReportingEnabled()` → `false`; providers stubbed; structured metrics off | **As Brave** |
| WebRTC log uploader | `disable-webrtc-log-uploader` | Upload functions pre-empted; log prefs off | **As Brave** (prefs + stub) |
| Component updater | `block-requests`, inox `0015` (Mast reverses them) | Brave's own server; Widevine left going to Google | **Google, as today** (only Widevine is registered); later a Mast proxy for CRLSets |
| Web Store / extension updates | `disable-webstore-urls`, inox `0005` (Mast reverses them) | Rerouted to Brave's server | **Google, as today**: `mast-webstore.patch` shrinks to the rename and the store links, nothing to reverse |
| Safe Browsing | compiled out (5 patches) | Kept, proxied through `safebrowsing.brave.com`; Enhanced forced off | **Compiled in, off** (pref default, no key) |
| Spellcheck | `block-requests` (dictionaries) | Dictionaries from `dict.brave.com`; cloud spelling off | **Cloud spelling off** (pref); dictionaries stay off until a source is chosen |
| Translate | `toggle-translation-via-switch` | Rerouted to `translate.brave.com`; ranker fetch stubbed | **Off** (pref + ranker stub) |
| Network time | `disable-network-time-tracker` | `kNetworkTimeServiceQuerying` disabled | **Feature off**; a Mast time server later for clock-proof blocks |
| Autofill server predictions | inox `0003` | `kAutofillServerCommunication` disabled | **Feature off** |
| RLZ (Windows, macOS) | inox `0021` | Nothing: upstream already has `enable_rlz = is_chrome_branded && …` | **Nothing**: off in Chromium builds |
| Privacy sandbox / Topics | `disable-privacy-sandbox` | `kBrowsingTopics` and the ads APIs off; settings stubbed | **Features off**, as Brave |
| Gemini in Chrome (Glic), AI APIs | `disable-ai` (583 lines, 57 files) | `IsEnabledByGlobalCriteria` → `false`; `kGeic`, `kAIPromptAPI` and others off | **As Brave**: one stub plus feature flags (Mast already hides the AI settings pages) |
| Optimization guide / model downloads | (parts of `disable-ai`) | Permission check → `false`; `PredictionManager` stubbed; features off | **As Brave** |
| Lens, AI mode in the address bar | (substitution) | `kLensOverlay`, `kAimEnabled` off | **Features off** |
| New tab page modules, One Google bar | `block-requests` | Own new tab page; NTP modules off | **Nothing**: Mast's page replaces it already |
| fonts.googleapis.com references | `disable-fonts-googleapis-references` | Fonts downloaded into the repo; Reading Mode's CSP patched | **As Brave** where they still appear; Mast's pages ship Inter already |
| Default search engine | `replace-google-search-engine-with-nosearch` (Mast skips it) | Own list, by country | **Google, as today** |
| DoH defaults, navigation-source param, untraceable URLs, F1 help, MEI preload, avatar downloads, JS optimizer | small patches | mostly upstream defaults | **Case by case** in `privacy.json`; several need nothing without API keys |

Feature names are as of Chromium 157 and Brave's master; each one is confirmed by
`./mast check-patches` when it's written (an edit whose anchor isn't found fails loudly).

## Replacing the safety net

Domain substitution made anything missed fail closed. Brave has no such net; it has two
checks instead, and Mast takes both.

1. **A network audit** (Brave: `browser/net/brave_network_audit_browsertest.cc`). Brave starts
   the browser with `--log-net-log --net-log-capture-mode=Everything`, sets Google as the
   search engine, visits its own pages, saves a password, waits 30 s per page, then fails on
   any request outside `kAllowedUrlPrefixes` ("get approval from the security team" before
   adding to it). Mast's version runs in `./mast shots`, which already records a net log:
   a fresh profile, **idle for 10 minutes** with no websites open, then the new tab page,
   Settings, a block and the welcome flow. Every request not on Mast's allowlist (Widevine and
   Web Store on Google, Mast's own update check on GitHub) fails the run. Websites visited by
   the later screenshots are kept out of this phase.
2. **A per-release diff of Chromium's network registry.** Chromium requires every network
   request to be registered, with what it sends and why, in
   `tools/traffic_annotation/summary/annotations.xml` (544 entries in 157). `./mast
   check-rebase <version>` lists every entry added or changed since the pinned release: each
   is a new way the browser can talk to the network, to be decided in `privacy.json` before
   moving.

## One pipeline, three platforms

Brave's layout (`build/commands/lib/config.ts`) is one tree and one patch set, a
`--target_os` switch, and an output folder per target (`out/<target_os>_<config>`).
Platform code lives in `*_win.cc` / `*_mac.mm` files picked by GN conditions
(`browser/sources.gni`: `if (is_win) { … brave_shell_integration_win.cc }`). Brave
cross-compiles macOS from Linux officially (`build/mac/cross_compile/README.md`: build and
DMG on Linux, signing on a real Mac over SSH). Windows-specific steps (its updater) need
a Windows host.

Mast, the same way:

| | Linux | Windows | macOS |
|---|---|---|---|
| Base | pristine Chromium | same | same |
| Patches | `make-patches.py` (one series) | same | same |
| Mast code | `src/mast/`, `*_linux.cc` where needed | `*_win.cc` beside it | `*_mac.mm` beside it |
| Output | `out/Mast` | `out/MastWin` | `out/MastMac` |
| SDK | — | toolchain zip made once on a Windows VM (`package_from_installed.py`) | macOS SDK package (`tools/mac-sdk.sh`, in progress) |
| Packaging | .deb, .rpm, tarball, Nix | installer (`mini_installer` or NSIS), signed with `osslsigncode` | .dmg; signing on a Mac |
| Updates | apt, dnf, Nix, Mast's check | Mast's check | Mast's check (later Sparkle) |

`./mast build [--target linux|win|mac]`, with `linux` the default. Already known Linux-only
pieces that need a sibling file: font registration (`ui/gfx/linux/fontconfig_util.cc`),
the update check, the default-browser desktop name and the profile folder name.

## Steps

Each step ends with Mast working; nothing is removed before its replacement is proven.

1. **Baseline audit** (free locally; runs on the next build): add the network-audit phase
   to `./mast shots` and record what today's ungoogled build sends. This is the bar the new
   base has to meet.
2. **Write the Mast privacy layer** (free): `privacy.json`, the helpers and the edits, applied
   on top of today's ungoogled base, where they're harmless duplicates. `./mast
   check-patches` verifies every anchor against pristine Chromium too, using
   `tools/base-file.mjs` with ungoogled switched off.
3. **Switch the base** (one long build, roughly 3–6 hours on the `cpx62`, about $1–2): a
   `MAST_BASE=chromium` setting makes `ungoogle.sh` skip ungoogled; `safe_browsing_mode = 1`;
   ungoogled's `flags.gn` replaced by Mast's own args. Run the audit and compare with step 1;
   fix every difference.
4. **Remove ungoogled**: `ungoogle.sh`, the three lists, `unpatch()`,
   `mast-ungoogled.patch`, most of `mast-webstore.patch`, `ungoogled_version.txt`; the
   README's "built on ungoogled-chromium" becomes a link to `privacy.json`. Add the
   `annotations.xml` diff to `check-rebase`.
5. **Windows, then macOS**, on this base.

Steps 3 and 5 need `MAST_BUDGET` in `mast.env` raised first ($7.78 of $9 is spent).

## Open questions

- Whether pristine Chromium without API keys fetches the variations seed at all. The audit
  in step 3 answers it; the plan stubs it either way.
- A Mast proxy for certificate revocation lists (CRLSets), the one component Mast wants that
  Google serves. Brave serves its own; until Mast has one, revocation lists stay as ungoogled
  leaves them today (none).
- Whether to keep binary pruning's idea (everything built from source). Brave doesn't prune;
  Mast doesn't today.
