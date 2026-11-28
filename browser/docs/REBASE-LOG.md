# Rebase log

One entry per release we check or move to. Record what `./mast check-rebase` found, what the
build and screenshots found that it didn't (then teach the check, if it can be taught), and how
long it took. Reports: `docs/rebase/<version>.md`. What is checked: [UPSTREAM.md](UPSTREAM.md).

## 158.0.8127.88, pristine Chromium base: moved 2026-11-28, not built yet

Moved together with the switch from ungoogled-chromium to Mast's own privacy layer
([BASE-PLAN.md](BASE-PLAN.md), step 3), so one full rebuild covers both.
**Check** ([rebase/158.0.8127.88.md](rebase/158.0.8127.88.md), on the ungoogled base): 1 of 104
edits failed, the tab-group header, where 158 added a "focused group" chip
(`GetUnfocusChipWidthWithGap()`) and per-corner radii. Four of its eight anchors were rewritten
for 158; the pills look the same. On pristine Chromium one more failed: the privacy layer's
pref defaults, anchored on a ChromeOS line 158 renamed; now anchored on the function after it.
All 13 feature switches and 7 stubs matched 158 unchanged. No headers, names or GN deps gone.

**Review items (18), triaged:** all fine. New includes; `DCHECK`→`CHECK`; removed flags and
an Android-only block; `kWebUIRoundedIcons` now on by default (WebUI icons only); the tab
"foot" is now `GetBottomExtensionWidth()`, which returns Mast's 4 px bottom radius, so the
overlap maths stays positive; a Widevine validity check that now accepts a query in progress.
**Look after the build:** tab groups (`tabgroups` screenshots), since 158 reworked the header.

## 157.0.8103.78 (ungoogled 157.0.8103.78-1): checked 2026-11-18, not built yet

A security point release. 158 is Stable upstream, but ungoogled hasn't tagged it yet.
**Check:** 0 of 92 edits failed, nothing to review (a first run reported 3 failures that were
network errors fetching base files, not moved anchors). Report:
[rebase/157.0.8103.78.md](rebase/157.0.8103.78.md). Built together with AAC/H.264 and Widevine
on request (ROADMAP §L), which need about a full rebuild anyway.

## 157.0.8103.50 (ungoogled 157.0.8103.50-1): moved 2026-11-11

Moved once the features were in, to the newest release both projects had (158 reaches
Stable on 2026-11-17; ungoogled tags it days later). The last 156 commit is tagged
`v156-last`, and its builder snapshot is kept, labelled `mast=builder-156`.
Built unattended (`./mast unattended`).

**Check:** 1 of 87 patch edits failed: the appearance page's auto-pin toggle, where upstream
moved `label=` onto the `pref-key=` line. Fixed in make-patches.py to match both releases.
No headers, names, overridden methods or GN deps gone. Report:
[rebase/157.0.8103.50.md](rebase/157.0.8103.50.md).

**Review items, triaged by hand:**

| File | Verdict |
|---|---|
| `ui/views/layout/layout_provider.cc` | Look after the build. `ShapeContextTokens` became `ShapeContextToken`, and the omnibox-expanded, find-bar and extensions-menu radius tokens are gone. Our edit only changes radius values, so it compiles, but the suggestion popup may no longer get the rounder corners. |
| `chrome/browser/ui/views/location_bar/location_bar_view.cc` | Look after the build. The focus ring now also shows while the full WebUI suggestion popup is open. Our hairline ring will show there too. |
| `chrome/browser/ui/views/frame/layout/browser_view_tabbed_layout_impl.cc` | Probably fine. `top_offset` is the vertical-tab organizer tray, which the one-row window doesn't use. Check the one-row screenshots. |
| `chrome/browser/resources/settings/settings_ui/settings_ui.ts` | Fine: the themed-colours block moved up into `connectedCallback`; our edit still lands in it. |
| `chrome/browser/resources/settings/appearance_page/appearance_page.html.ts` | Fine: the reflowed lines are the fix above. |
| `chrome/browser/ui/webui/chrome_web_ui_configs.cc` | Fine: History and the third-party NTP configs were added or moved near our registration. |
| `chrome/browser/chrome_content_browser_client.cc` | Fine: forced-colours code next to ours, unrelated. |
| `chrome/browser/ui/webui/settings/settings_ui.cc` | Fine: universal opt-out and tab styling changes, unrelated. |
| `chrome/browser/ui/views/omnibox/omnibox_popup_presenter_base.cc` | Fine: new includes only. |
| `chrome/common/chrome_content_client.cc` | Fine: a scheme line added next to ours. |
| `ui/base/ui_base_features.cc` | Fine: a GlassFrame feature param added next to ours. |

**ungoogled series:** `disable-tos-dialog.patch` and `wasm-interpreter-fixes.patch` left the
series. On first run after the move, check that no terms-of-service dialog appears.

**Build:** unattended, 2026-11-11, first try: sync + ungoogled base + patches ~13 min,
compile ~1 h 40 min (54.8k steps), screenshots 6 min; about 2 h of builder time, ~$0.45.
The builder's regenerated patches matched the repo byte for byte, and all 62 screenshots
rendered, the tab-group menu scene included. The check predicted this: nothing it missed.
Still to look at on a real machine: the two review items above (suggestion popup corners,
focus ring with the popup open) and the first-run terms-of-service dialog.
