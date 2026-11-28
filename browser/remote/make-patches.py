"""Regenerate Mast's upstream patches against the base tree (git HEAD in src: Chromium plus
ungoogled-chromium's core patches and domain substitution; see remote/ungoogle.sh).

Run on the builder: python3 ~/mast/remote/make-patches.py  -> /root/pw/*.patch
Each edit asserts its anchor is found exactly once, so a Chromium upgrade that
moves code fails loudly here instead of silently producing a broken patch.
"""
import json, os, re, shutil, subprocess, sys, tempfile

SRC = "/root/chromium/src"
WORK = os.environ.get("MAST_PATCH_WORK", "/root/pw")
# Local mode (./mast check-patches): read base files rebuilt from Chromium's source site plus
# ungoogled's patches by tools/base-file.mjs, so anchors are checked without the builder.
LOCAL = os.environ.get("MAST_LOCAL_BASE")
# ./mast check-rebase: report every edit that fails on another release instead of stopping at
# the first (MAST_KEEP_GOING=1). Every run writes edits.json next to the patches: each edit's
# patch, upstream file and line here, which tools/rebase-check.mjs reads.
KEEP_GOING = os.environ.get("MAST_KEEP_GOING") == "1"
# The tree Mast's patches go on (docs/BASE-PLAN.md): "ungoogled" (pristine Chromium plus
# ungoogled-chromium's core patches and domain substitution; what Mast ships today) or
# "chromium" (pristine Chromium, with Mast's own privacy layer from privacy.json instead).
BASE = os.environ.get("MAST_BASE", "ungoogled")
assert BASE in ("ungoogled", "chromium"), f"MAST_BASE must be ungoogled or chromium, not {BASE!r}"
UNGOOGLED = BASE == "ungoogled"
shutil.rmtree(WORK, ignore_errors=True)


def pristine(path):
    if LOCAL:
        local = subprocess.run(["node", os.path.join(LOCAL, "tools/base-file.mjs"), path],
                               capture_output=True, text=True, check=True).stdout.strip()
        return open(local).read()
    return subprocess.run(["git", "-C", SRC, "show", f"HEAD:{path}"],
                          capture_output=True, text=True, check=True).stdout


def once(text, old, new):
    n = text.count(old)
    assert n == 1, f"anchor found {n}x: {old[:70]!r}"
    return text.replace(old, new)


UG_TAG = os.environ.get("MAST_UNGOOGLED_TAG") or open(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "ungoogled_version.txt")).read().strip()


def ungoogled_patch(name):
    """One of ungoogled-chromium's patches: base-file.mjs's cache locally, the checkout on
    the builder."""
    for p in (os.path.expanduser(f"~/.cache/mast/ungoogled/{UG_TAG}/p/{name}"),
              f"/root/ungoogled/patches/{name}"):
        if os.path.exists(p):
            return open(p).read()
    raise FileNotFoundError(name)


def unpatch(text, path, name):
    """`text` with ungoogled patch `name`'s change to `path` undone (patch -R), so Mast can
    keep the rest of ungoogled while restoring one feature it removed. Fails loudly if the
    patch no longer reverses cleanly (an ungoogled or Chromium update moved it)."""
    sections = re.split(r"(?m)^(?=--- a/)", ungoogled_patch(name))
    section = "".join(s for s in sections if s.startswith(f"--- a/{path}\n"))
    assert section, f"{name} doesn't change {path}"
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "file")
        open(f, "w").write(text)
        r = subprocess.run(["patch", "-R", "-s", "-F0", "--no-backup-if-mismatch", "-r", "-", f],
                           input=section, text=True, capture_output=True)
        assert r.returncode == 0, f"can't undo {name} in {path}: {r.stdout}{r.stderr}"
        return open(f).read()


def exactly(text, old, new, n):
    """Like once(), for an anchor that must appear exactly n times (all replaced)."""
    c = text.count(old)
    assert c == n, f"anchor found {c}x (want {n}): {old[:70]!r}"
    return text.replace(old, new)


def feature_off(text, name):
    """Every BASE_FEATURE(name, ...) declaration in `text` ships disabled, whatever its
    platform branches say (Brave's Plaster: set_feature_flag_default_state). Fails if the
    feature isn't declared here or is already off everywhere (then drop it from
    privacy.json)."""
    decl = re.compile(r"BASE_FEATURE\(\s*" + re.escape(name) + r"\s*,.*?\);", re.S)
    spans = list(decl.finditer(text))
    assert spans, f"{name} isn't declared here"
    changed = 0
    for m in reversed(spans):
        body = m.group(0)
        n = body.count("base::FEATURE_ENABLED_BY_DEFAULT")
        changed += n
        body = body.replace("base::FEATURE_ENABLED_BY_DEFAULT",
                            "base::FEATURE_DISABLED_BY_DEFAULT /* Mast: privacy.json */")
        text = text[:m.start()] + body + text[m.end():]
    assert changed, f"{name} is already disabled by default: drop it from privacy.json"
    return text


def add_dep(text, target_header, dep):
    i = text.index(target_header)
    j = text.index("  deps = [\n", i) + len("  deps = [\n")
    return text[:j] + f'    "{dep}",\n' + text[j:]


PATCHES = {}
EDITS = []  # {patch, path, line, error}


def edit(patch, path, fn):
    record = {"patch": patch, "path": path, "line": sys._getframe(1).f_lineno}
    EDITS.append(record)
    a, b = (os.path.join(WORK, patch + ".d", side, path) for side in ("a", "b"))
    for p in (a, b):
        os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        base = pristine(path)
        # A file edited twice in one patch: the second edit builds on the first
        # (starting from the pristine file again silently dropped the first).
        text = open(b).read() if path in PATCHES.get(patch, []) else base
        new = fn(text)
        assert new != text, f"no change in {path}"
    except (AssertionError, subprocess.CalledProcessError, ValueError, FileNotFoundError) as e:
        if not KEEP_GOING:
            raise
        missing = isinstance(e, subprocess.CalledProcessError) and "404" in (e.stderr or "")
        record["error"] = "file not in this release" if missing else str(e).splitlines()[0][:240]
        print(f"FAIL {patch} {path} (make-patches.py:{record['line']}): {record['error']}")
        return
    open(a, "w").write(base)
    open(b, "w").write(new)
    if path not in PATCHES.setdefault(patch, []):
        PATCHES[patch].append(path)


# ---------------------------------------------------------------- hooks
H = "mast-hooks.patch"

# Every navigation passes Mast's throttle: blocked sites, the always-blocked feeds, YouTube's
# Music only (mast_navigation_throttle.h).
edit(H, "chrome/browser/chrome_content_browser_client_navigation_throttles.cc", lambda t: once(once(t,
    '#include "chrome/browser/supervised_user/classify_url_navigation_throttle.h"\n',
    '#include "chrome/browser/supervised_user/classify_url_navigation_throttle.h"\n#include "mast/browser/mast_navigation_throttle.h"\n'),
    '  supervised_user::ClassifyUrlNavigationThrottle::MaybeCreateAndAdd(registry);\n',
    '  supervised_user::ClassifyUrlNavigationThrottle::MaybeCreateAndAdd(registry);\n  mast::MastNavigationThrottle::MaybeCreateAndAdd(registry);\n'))
edit(H, "chrome/browser/BUILD.gn", lambda t: add_dep(t, 'source_set("core") {', "//mast/browser"))

# Mast's prefs: blocks in Local State (shared by every profile, incognito too), the rest per
# profile, registered last so Mast can change upstream defaults.
edit(H, "chrome/browser/prefs/browser_prefs.cc", lambda t: once(once(once(once(t,
    '#include "chrome/browser/prefs/incognito_mode_prefs.h"\n',
    '#include "chrome/browser/prefs/incognito_mode_prefs.h"\n#include "mast/browser/mast_blocks.h"\n#include "mast/browser/mast_prefs.h"\n'),
    '  BrowserProcessImpl::RegisterPrefs(registry);\n',
    '  BrowserProcessImpl::RegisterPrefs(registry);\n  mast::RegisterLocalStatePrefs(registry);\n'),
    # Last in RegisterProfilePrefs(), so Mast can change upstream defaults.
    '  registry->RegisterBooleanPref(prefs::kStaticStorageQuotaEnabled, false);\n}\n',
    '  registry->RegisterBooleanPref(prefs::kStaticStorageQuotaEnabled, false);\n  mast::RegisterProfilePrefs(registry);\n}\n'),
    # Last in RegisterLocalState() too (before the migration prefs, which upstream keeps
    # last), so upstream's Local State prefs exist when Mast changes their defaults.
    '  // This is intentionally last.\n  RegisterLocalStatePrefsForMigration(registry);\n',
    '  mast::SetLocalStateDefaults(registry);\n'
    '  // This is intentionally last.\n  RegisterLocalStatePrefsForMigration(registry);\n'))
edit(H, "chrome/browser/prefs/BUILD.gn", lambda t: add_dep(t, 'source_set("impl") {', "//mast/browser"))

# Widevine on request (mast/browser/ui/mast_widevine.h), the way Brave does it. The component
# updater registers Widevine only, and only once the user said yes on a page that needed it:
# with Google's real update address back (substitution-exclude.list), every other component
# would call Google too, and under ungoogled-chromium none of them ever downloaded anyway.
edit(H, "chrome/browser/component_updater/registration.cc", lambda t: once(once(t,
    '#include "chrome/browser/component_updater/registration.h"\n',
    '#include "chrome/browser/component_updater/registration.h"\n#include "mast/browser/component_updater/mast_components.h"\n'),
    'void RegisterComponentsForUpdate() {\n  auto* const cus = g_browser_process->component_updater();\n',
    'void RegisterComponentsForUpdate() {\n  auto* const cus = g_browser_process->component_updater();\n'
    '  // Mast: Widevine only, on the user\'s word (mast_components.h).\n'
    '  if (cus) {\n    MastRegisterComponents(cus);\n    return;\n  }\n'))
edit(H, "chrome/browser/component_updater/BUILD.gn", lambda t: add_dep(once(t,
    '    "registration.cc",\n',
    '    "registration.cc",\n    "//mast/browser/component_updater/mast_components.cc",\n    "//mast/browser/component_updater/mast_components.h",\n'),
    'source_set("impl") {', "//mast/common"))
# Its download runs now, not at the next scheduled check (hours away): a friend, the way
# speech and translation's one-off downloads are.
edit(H, "components/component_updater/component_updater_service.h", lambda t: once(t,
    '  friend class OnDemandTester;\n',
    '  friend class OnDemandTester;\n  friend class MastWidevineUpdater;  // Mast: mast_components.cc\n'))
# A page starting to use protected media (EME) is what asks the user.
edit(H, "content/browser/media/key_system_support_impl.cc", lambda t: once(once(t,
    '#include "content/public/browser/render_frame_host.h"\n',
    '#include "content/public/browser/render_frame_host.h"\n#include "mast/common/mast_widevine_state.h"\n'),
    '  observer_remote_.Bind(std::move(observer));\n',
    '  observer_remote_.Bind(std::move(observer));\n'
    '  // Mast: the page wants protected media; Mast may offer Widevine.\n'
    '  mast::ProtectedMediaRequested(static_cast<WebContents*>(\n'
    '      WebContentsImpl::FromRenderFrameHostImpl(\n'
    '          static_cast<RenderFrameHostImpl*>(&render_frame_host()))));\n'))
edit(H, "content/browser/BUILD.gn", lambda t: add_dep(t, 'source_set("browser") {', "//mast/common"))
# ungoogled's block-requests.patch makes every component update check return at once without
# answering, so Widevine's download waited forever (the bar went away, nothing followed).
# Undone for Mast: the only component ever registered is Widevine, after a yes.
if UNGOOGLED:
    edit(H, "components/update_client/update_checker.cc", lambda t: unpatch(
        t, "components/update_client/update_checker.cc", "core/ungoogled-chromium/block-requests.patch"))
edit(H, "components/infobars/core/infobar_delegate.h", lambda t: once(t,
    '    CONTEXTUAL_CUE_INFOBAR_DELEGATE_IOS = 140,\n  };\n',
    '    CONTEXTUAL_CUE_INFOBAR_DELEGATE_IOS = 140,\n    MAST_WIDEVINE_INFOBAR_DELEGATE = 141,  // Mast: mast_widevine.cc\n'
    '    MAST_UPDATE_INFOBAR_DELEGATE = 142,  // Mast: mast_update_check.cc\n  };\n'))
# Incognito stays available: blocks live in Local State and the navigation throttle runs
# for every profile, so a private window is blocked exactly like a normal one.

# chrome://mast, and Mast's page as the new tab page (both NTP hosts, so the
# omnibox still treats it as the NTP).
edit(H, "chrome/browser/ui/webui/chrome_web_ui_configs.cc", lambda t: once(once(once(once(t,
    '#include "content/public/browser/webui_config_map.h"\n',
    '#include "content/public/browser/webui_config_map.h"\n#include "mast/browser/webui/mast_ui.h"\n'),
    '  auto& map = content::WebUIConfigMap::GetInstance();\n',
    '  auto& map = content::WebUIConfigMap::GetInstance();\n  map.AddWebUIConfig(std::make_unique<mast::MastUIConfig>(mast::kMastHost));\n'),
    '  map.AddWebUIConfig(std::make_unique<NewTabPageUIConfig>());\n',
    '  map.AddWebUIConfig(\n      std::make_unique<mast::MastUIConfig>(chrome::kChromeUINewTabPageHost));\n'),
    '  map.AddWebUIConfig(std::make_unique<NewTabPageThirdPartyUIConfig>());\n',
    '  map.AddWebUIConfig(std::make_unique<mast::MastUIConfig>(\n      chrome::kChromeUINewTabPageThirdPartyHost));\n'))
edit(H, "chrome/browser/ui/webui/BUILD.gn", lambda t: add_dep(t, 'source_set("configs") {', "//mast/browser/webui"))
# The new tab is Mast's page whatever the search engine. With any engine but Google,
# Chromium opened the engine's own new tab page instead (Brave Search, Bing). The early
# return for Google now covers every profile that reaches it (off-the-record ones returned
# just above); it isn't written as `true`, so the code after it doesn't trip
# -Wunreachable-code.
edit(H, "chrome/browser/search/search.cc", lambda t: once(t,
    '    const bool default_is_google = DefaultSearchProviderIsGoogle(profile);\n',
    '    // Mast: always Mast\'s new tab page, never the search engine\'s.\n'
    '    const bool default_is_google =\n'
    '        DefaultSearchProviderIsGoogle(profile) || !profile->IsOffTheRecord();\n'))

# The star (and Ctrl+D) saves to the Desktop folder behind the new tab page. Mast's
# provider takes the slot of the shopping one (commerce is off in Mast); the shopping
# provider stays as the fallback so upstream code is unchanged and still compiles.
edit(H, "chrome/browser/bookmarks/chrome_bookmark_client.cc", lambda t: once(once(t,
    '#include "chrome/browser/bookmarks/chrome_bookmark_client.h"\n',
    '#include "chrome/browser/bookmarks/chrome_bookmark_client.h"\n\n#include "mast/browser/desktop/mast_desktop.h"\n'),
    '  shopping_save_location_provider_ =\n      std::make_unique<ShoppingCollectionProvider>(model, profile_);\n',
    '  // Mast: new bookmarks go to the Desktop folder (the new tab page).\n'
    '  shopping_save_location_provider_ =\n      mast::CreateDesktopSaveLocationProvider(model);\n'
    '  if (!shopping_save_location_provider_) {\n'
    '    shopping_save_location_provider_ =\n        std::make_unique<ShoppingCollectionProvider>(model, profile_);\n  }\n'))
edit(H, "chrome/browser/bookmarks/BUILD.gn", lambda t: add_dep(t, 'source_set("impl") {', "//mast/browser/desktop"))

# Toolbar: no profile button (Mast has no sign-in).
edit(H, "chrome/browser/ui/views/toolbar/toolbar_view.cc", lambda t: once(t,
    '        AvatarToolbarButtonInterface::CanShowForProfile(browser_->GetProfile());\n',
    '        false;  // Mast: no sign-in, so no profile button.\n'))
# Profiles are hidden for now: the ⋮ menu has no profile row ("Your Mast": close, customize,
# add profile, guest). Adding people and guest mode are off too (mast::SetLocalStateDefaults).
edit(H, "chrome/browser/ui/toolbar/app_menu_model.cc", lambda t: once(t,
    '#if !BUILDFLAG(IS_CHROMEOS)\n  sub_menus_.push_back(std::make_unique<ProfileSubMenuModel>(\n',
    '#if 0  // Mast: no profile row; profiles are hidden for now.\n'
    '  sub_menus_.push_back(std::make_unique<ProfileSubMenuModel>(\n'))

# The one-row window's toolbar (mast_one_row_layout.h) is exactly as wide as it wants to be.
# The extensions area and the pinned actions (downloads) slide their size in while their
# icons change, and report that in-between size as what they want, so a new extension icon
# made the row resize every frame, which restarted the slide: the toolbar shook, and the
# icons stopped at a wrong height. In one row they report their final size at once (their
# icons still slide inside it), and there's no divider after the extensions.
edit(H, "chrome/browser/ui/views/toolbar/toolbar_view.cc", lambda t: once(once(once(once(t,
    '#include "chrome/browser/ui/views/toolbar/toolbar_divider.h"\n',
    '#include "chrome/browser/ui/views/toolbar/toolbar_divider.h"\n#include "mast/common/mast_features.h"\n'),
    '    extensions_container_->SetProperty(views::kFlexBehaviorKey,\n'
    '                                       extensions_flex_rule);\n',
    '    extensions_container_->SetProperty(views::kFlexBehaviorKey,\n'
    '                                       extensions_flex_rule);\n'
    '    if (mast::IsOneRowWindow()) {  // Mast: final size at once (see make-patches.py)\n'
    '      extensions_container_->GetAnimatingLayoutManager()->SetBoundsAnimationMode(\n'
    '          views::AnimatingLayoutManager::BoundsAnimationMode::kUseHostBounds);\n'
    '    }\n'),
    '            .WithOrder(kToolbarActionsFlexOrder));\n',
    '            .WithOrder(kToolbarActionsFlexOrder));\n'
    '    if (mast::IsOneRowWindow()) {  // Mast: final size at once\n'
    '      pinned_toolbar_actions_container_->GetAnimatingLayoutManager()\n'
    '          ->SetBoundsAnimationMode(views::AnimatingLayoutManager::\n'
    '                                       BoundsAnimationMode::kUseHostBounds);\n'
    '    }\n'),
    '        .SetViewHidden(toolbar_divider_, !extensions_container_->GetVisible());\n',
    '        .SetViewHidden(toolbar_divider_, mast::IsOneRowWindow() ||  // Mast: none\n'
    '                                             !extensions_container_->GetVisible());\n'))

# Extensions aren't pinned to the toolbar when installed (157 started pinning them): they
# live behind the extensions (puzzle) button, which the one row has room for.
edit(H, "chrome/browser/ui/ui_features.cc", lambda t: once(t,
    'BASE_FEATURE(kExtensionsPinnedByDefault, base::FEATURE_ENABLED_BY_DEFAULT);',
    'BASE_FEATURE(kExtensionsPinnedByDefault, base::FEATURE_DISABLED_BY_DEFAULT);  // Mast'))

# (Page info's "Block this site" row, opening mast/browser/ui/mast_block_bubble.cc, was
# removed 2026-11-09: few would find it there. The bubble's code is parked in the repo.)

# Mast's block bubbles subclass BubbleDialogDelegateView, whose constructor is
# limited to a friend list (like the home button's undo bubble). Kept while the bubble is
# parked: the header is included everywhere, so changing it rebuilds most of Chromium.
edit(H, "ui/views/bubble/bubble_dialog_delegate_view.h", lambda t: once(once(t,
    'class HomePageUndoBubble;\n',
    'class HomePageUndoBubble;\nnamespace mast {\nclass MastBlockBubble;\nclass MastUndoBubble;\n}  // namespace mast\n'),
    '  friend class ::HomePageUndoBubble;\n',
    '  friend class ::HomePageUndoBubble;\n  friend class ::mast::MastBlockBubble;\n  friend class ::mast::MastUndoBubble;\n'))

# Theme: dark and not the GTK theme on Linux. The colours themselves come from
# Mast's palette (mast-theme.patch), so no seeded user colour.
def theme(t):
    t = once(t, 'registry->RegisterIntegerPref(\n      prefs::kBrowserColorScheme,\n      std::to_underlying(ThemeService::BrowserColorScheme::kSystem));',
             'registry->RegisterIntegerPref(\n      prefs::kBrowserColorScheme,\n      std::to_underlying(ThemeService::BrowserColorScheme::kDark));')
    t = once(t, 'static_cast<int>(ui::GetDefaultSystemTheme()));',
             'static_cast<int>(ui::SystemTheme::kDefault));')
    return t
edit(H, "chrome/browser/themes/theme_service.cc", theme)

# Settings without Google: no "You and Google" page (account, Google services, import),
# no AI page, no Privacy Guide. Import moves to Mast's own onboarding (IMPORT-PLAN.md).
edit(H, "chrome/browser/ui/webui/settings/settings_ui.cc", lambda t: once(t,
    '  TryShowHatsSurveyWithTimeout();\n}\n',
    '  // Mast: no AI or Google pages in Settings.\n'
    '  html_source->AddBoolean("showAiPage", false);\n'
    '  html_source->AddBoolean("showGlicSettings", false);\n'
    '  html_source->AddBoolean("showGeicSettings", false);\n'
    '  html_source->AddBoolean("showPrivacyGuide", false);\n\n'
    '  TryShowHatsSurveyWithTimeout();\n}\n'))
# Settings > About: Mast's version in front of Chromium's, like Brave's ("Version 0.1.0
# (Chromium 157.0.8103.78) (Official Build) (64-bit)"); mast/common/mast_version.h.
edit(H, "chrome/browser/ui/webui/settings/settings_localized_strings_provider.cc", lambda t: once(once(t,
    '#include "chrome/browser/ui/webui/version/version_ui.h"\n',
    '#include "chrome/browser/ui/webui/version/version_ui.h"\n#include "mast/common/mast_version.h"\n'),
    '  std::u16string browser_version = VersionUI::GetAnnotatedVersionStringForUi();\n',
    '  std::u16string browser_version =\n'
    '      mast::WithMastVersion(VersionUI::GetAnnotatedVersionStringForUi());\n'))
edit(H, "chrome/browser/ui/webui/settings/BUILD.gn", lambda t: add_dep(t, 'source_set("impl") {', "//mast/common"))
edit(H, "chrome/browser/resources/settings/page_visibility.ts", lambda t: once(t,
    "  if (!loadTimeData.getBoolean('isGuest')) {\n    return undefined;\n  }\n",
    "  if (!loadTimeData.getBoolean('isGuest')) {\n"
    "    // Mast: no Google account page and no AI page. Every key is listed: the\n"
    "    // menu hides pages whose entry is missing.\n"
    "    return {\n"
    "      a11y: true, ai: false, autofill: true, defaultBrowser: true, downloads: true,\n"
    "      extensions: true, languages: true, onStartup: true, people: false,\n"
    "      performance: true, reset: true, safetyHub: true, system: true, yourSavedInfo: true,\n"
    "      appearance: {bookmarksBar: false, homeButton: true, hoverCard: true, pageZoom: true,\n"
    "                   setTheme: true, sidePanel: true},\n"
    "      privacy: {networkPrediction: true, searchPrediction: true},\n"
    "    };\n  }\n"))

# mast:// for Mast's own pages, like Brave's brave://. Internally they stay chrome://
# pages: the scheme is registered (so the omnibox treats mast://settings as a URL),
# typing mast:// is rewritten to chrome:// first, and the omnibox shows chrome:// as mast://.
edit(H, "chrome/common/chrome_content_client.cc", lambda t: once(t,
    '  for (auto* standard_scheme : kChromeStandardURLSchemes)\n    schemes->standard_schemes.push_back(standard_scheme);\n',
    '  for (auto* standard_scheme : kChromeStandardURLSchemes)\n    schemes->standard_schemes.push_back(standard_scheme);\n'
    '  schemes->standard_schemes.push_back("mast");  // Mast: mast:// opens chrome:// pages.\n'))
edit(H, "chrome/browser/chrome_content_browser_client.cc", lambda t: once(once(t,
    '#include "chrome/browser/chrome_content_browser_client.h"\n',
    '#include "chrome/browser/chrome_content_browser_client.h"\n\n#include "mast/browser/mast_scheme.h"\n'),
    '    BrowserURLHandler* handler) {\n  // The group policy NTP URL handler must be registered before',
    '    BrowserURLHandler* handler) {\n  // Mast: mast://x -> chrome://x, before every other handler.\n'
    '  handler->AddHandlerPair(&mast::HandleMastScheme,\n                          BrowserURLHandler::null_handler());\n\n'
    '  // The group policy NTP URL handler must be registered before'))
edit(H, "components/omnibox/browser/location_bar_model_impl.cc", lambda t: once(t,
    '  return gfx::TruncateString(formatted_text, max_url_display_chars_,\n                             gfx::CHARACTER_BREAK);\n',
    '  // Mast: its own pages read mast://, not chrome://.\n'
    '  std::u16string shown = formatted_text;\n'
    '  if (url.SchemeIs("chrome") && shown.starts_with(u"chrome://")) {\n'
    '    shown.replace(0, 6, u"mast");\n  }\n'
    '  return gfx::TruncateString(shown, max_url_display_chars_,\n                             gfx::CHARACTER_BREAK);\n'))
# Typing mast://version in the address bar: the omnibox only treats schemes the browser
# handles itself as URLs, and mast:// is only rewritten at navigation, so it searched.
edit(H, "chrome/browser/autocomplete/chrome_autocomplete_scheme_classifier.cc", lambda t: once(t,
    '       base::EqualsCaseInsensitiveASCII(scheme, content::kViewSourceScheme) ||\n',
    '       base::EqualsCaseInsensitiveASCII(scheme, content::kViewSourceScheme) ||\n'
    '       // Mast: mast:// is chrome:// (mast/browser/mast_scheme.h).\n'
    '       base::EqualsCaseInsensitiveASCII(scheme, "mast") ||\n'))

# New tabs show the Mast new-tab icon from the start. Before the page commits the favicon
# driver has no icon yet, and the tab showed Chromium's default globe for a moment.
edit(H, "chrome/browser/ui/tab_ui_helper.cc", lambda t: once(once(t,
    '#include "chrome/grit/generated_resources.h"\n',
    '#include "chrome/grit/generated_resources.h"\n#include "chrome/grit/theme_resources.h"\n'),
    '  if (!favicon_driver || !favicon_driver->FaviconIsValid()) {\n',
    '  if (!favicon_driver || !favicon_driver->FaviconIsValid()) {\n'
    '    // Mast: a new tab shows the new-tab icon while it loads, not the globe.\n'
    '    if (IsNTP(web_contents()->GetVisibleURL())) {\n'
    '      return ui::ImageModel::FromResourceId(IDR_NTP_FAVICON);\n    }\n'))
# The new tab's sails stayed grey: before the page commits a new tab has no entry, so its
# icon isn't themed; after it commits the icon is the same image, and SetIcon() returned
# early on "same icon", dropping the now-true themify flag. It re-themes when only the flag
# changes, and new tabs are themed from their first frame.
edit(H, "chrome/browser/ui/views/tabs/tab/tab_icon.cc", lambda t: once(t,
    '  if (favicon_ == icon) {\n    return;\n  }\n\n  favicon_ = icon;\n',
    '  if (favicon_ == icon && should_themify_favicon_ == should_themify_favicon) {\n'
    '    return;  // Mast: a themify change alone re-themes\n  }\n\n  favicon_ = icon;\n'))
edit(H, "chrome/browser/ui/tab_ui_helper.cc", lambda t: once(t,
    'bool TabUIHelper::ShouldThemifyFavicon() {\n',
    'bool TabUIHelper::ShouldThemifyFavicon() {\n'
    '  // Mast: a new tab\'s sails follow the tab text from the first frame.\n'
    '  if (IsNTP(tab().GetContents()->GetVisibleURL())) {\n    return true;\n  }\n'))
# ...and in the one-row window a browser page's icon takes its own tab's text colour: bright
# on the active tab, quieter on the others, like the title next to it. (Recolouring with
# one colour for every tab left the active tab's sails grey beside its white title.)
edit(H, "chrome/browser/ui/views/tabs/tab/tab_icon.cc", lambda t: once(once(t,
    '#include "chrome/browser/ui/views/dotted_icon.h"\n',
    '#include "chrome/browser/ui/views/dotted_icon.h"\n#include "mast/common/mast_features.h"\n'
    '#include "ui/gfx/image/image_skia_operations.h"\n'),
    '  if (!GetNonDefaultFavicon() || should_themify_favicon_) {\n'
    '    themed_favicon_ = tabs::ThemeFaviconForTab(\n'
    '        favicon_.Rasterize(GetColorProvider()), *GetColorProvider());\n',
    '  if (mast::IsOneRowWindow() &&\n'
    '      (!GetNonDefaultFavicon() || should_themify_favicon_)) {\n'
    '    themed_favicon_ = gfx::ImageSkiaOperations::CreateColorMask(\n'
    '        favicon_.Rasterize(GetColorProvider()),\n'
    '        GetColorProvider()->GetColor(\n'
    '            is_active_tab_ ? kColorTabForegroundActiveFrameActive\n'
    '                           : kColorTabForegroundInactiveFrameActive));\n'
    '  } else if (!GetNonDefaultFavicon() || should_themify_favicon_) {\n'
    '    themed_favicon_ = tabs::ThemeFaviconForTab(\n'
    '        favicon_.Rasterize(GetColorProvider()), *GetColorProvider());\n'))
edit(H, "chrome/browser/ui/views/tabs/tab/BUILD.gn", lambda t: add_dep(t, 'source_set("impl") {', "//mast/common"))
edit(H, "chrome/browser/ui/BUILD.gn", lambda t: add_dep(t, '  source_set("tab_ui_helper") {', "//chrome/app/theme:theme_resources"))

# Opening a tab (Ctrl+T, +) asks Chromium's incognito-only chrome://newtab config whether it
# serves this profile before the URL is redirected to chrome://new-tab-page; it says no and
# logged an ERROR every time. Same answer, logged only at verbose level.
edit(H, "chrome/browser/ui/webui/ntp/new_tab_ui.cc", lambda t: once(t,
    '      LOG(ERROR) << "Requested load of chrome://newtab/ for incorrect "\n'
    '                    "profile type.";\n',
    '      DVLOG(1) << "Requested load of chrome://newtab/ for incorrect "\n'
    '                  "profile type.";  // Mast: expected on every new tab\n'))

# ---------------------------------------------------------------- bookmarks
# Saved sites live on the new tab page (a bookmarks folder, mast/browser/desktop). There is
# no bookmarks bar, and the star doesn't ask for a folder: it puts the page on the new tab.

# The star's bubble: name and Remove, no folder picker (the folder comes from Mast's
# suggested save location, the new tab's folder).
edit(H, "chrome/browser/ui/views/bookmarks/bookmark_bubble_view.cc", lambda t: once(once(t,
    '      .AddCombobox(\n'
    '          kBookmarkFolderFieldId,\n'
    '          l10n_util::GetStringUTF16(IDS_BOOKMARK_BUBBLE_FOLDER_LABEL),\n'
    '          std::make_unique<RecentlyUsedFoldersComboModel>(\n'
    '              bookmark_model,\n'
    '              bookmark_model->GetMostRecentlyAddedUserNodeForURL(url)),\n'
    '          ui::DialogModelCombobox::Params().SetCallback(\n'
    '              base::BindRepeating(&BookmarkBubbleDelegate::OnComboboxAction,\n'
    '                                  base::Unretained(bubble_delegate))))\n',
    '      // Mast: no folder picker; the star saves to the new tab page.\n'),
    '    GetFolderModel()->MaybeChangeParent(\n'
    '        node, dialog_model()\n'
    '                  ->GetComboboxByUniqueId(kBookmarkFolderFieldId)\n'
    '                  ->selected_index());\n',
    ''))
edit(H, "chrome/app/bookmarks_strings.grdp", lambda t: once(once(t,
    '  <message name="IDS_BOOKMARK_BUBBLE_PAGE_BOOKMARKED" desc="Title of the bubble after bookmarking a page.">\n'
    '    Bookmark added\n',
    '  <message name="IDS_BOOKMARK_BUBBLE_PAGE_BOOKMARKED" desc="Title of the bubble after bookmarking a page.">\n'
    '    Added to New Tab\n'),
    '  <message name="IDS_BOOKMARK_BUBBLE_PAGE_BOOKMARK" desc="Title of the bubble when re-clicking on a bookmark">\n'
    '    Edit bookmark\n',
    '  <message name="IDS_BOOKMARK_BUBBLE_PAGE_BOOKMARK" desc="Title of the bubble when re-clicking on a bookmark">\n'
    '    On your New Tab\n'))
# The bookmarked (filled) star painted in Chromium's "cascading accent", which falls back to
# the focus ring colour, kept deliberately faint in Mast: a starred page looked unstarred.
# It uses the strong foreground of the icons around it; filled vs outline tells the state.
edit(H, "chrome/browser/ui/views/bookmarks/bookmark_page_action_controller.cc", lambda t: once(t,
    '      starred ? page_actions::PageActionColorSource::kCascadingAccent\n'
    '              : page_actions::PageActionColorSource::kForeground,\n',
    '      // Mast: the foreground either way (see make-patches.py).\n'
    '      page_actions::PageActionColorSource::kForeground,\n'))
# No bookmarks bar: its items leave the Bookmarks menu, and its toggle (Ctrl+Shift+B too)
# is never enabled. mast::OnBrowserWindowReady keeps the bar hidden for old profiles.
edit(H, "chrome/browser/ui/toolbar/bookmark_sub_menu_model.cc", lambda t: once(t,
    '    SetElementIdentifierAt(GetIndexOfCommandId(IDC_SHOW_BOOKMARK_BAR).value(),\n'
    '                           kShowBookmarkBarMenuItem);\n  }\n',
    '    SetElementIdentifierAt(GetIndexOfCommandId(IDC_SHOW_BOOKMARK_BAR).value(),\n'
    '                           kShowBookmarkBarMenuItem);\n  }\n'
    '  // Mast: no bookmarks bar.\n'
    '  for (int id : {IDC_SHOW_BOOKMARK_BAR, IDC_BOOKMARK_BAR_SUBMENU}) {\n'
    '    if (const std::optional<size_t> index = GetIndexOfCommandId(id)) {\n'
    '      RemoveItemAt(*index);\n    }\n  }\n'))
edit(H, "chrome/browser/ui/browser_command_controller.cc", lambda t: once(t,
    '  command_updater_->UpdateCommandEnabled(\n'
    '      IDC_SHOW_BOOKMARK_BAR,\n'
    '      common_enabled && !profile()->GetPrefs()->IsManagedPreference(\n'
    '                            bookmarks::prefs::kShowBookmarkBar));\n',
    '  // Mast: no bookmarks bar, so no toggle (or Ctrl+Shift+B) for it.\n'
    '  command_updater_->UpdateCommandEnabled(IDC_SHOW_BOOKMARK_BAR, false);\n'))
# Settings: the bookmarks bar's tab-group options go with it (the bar's own row is hidden
# through page_visibility.ts above).
edit(H, "chrome/browser/resources/settings/appearance_page/appearance_page.html.ts", lambda t: once(once(t,
    '        label="$i18n{showTabGroupsInBookmarksBar}"\n'
    '        ?hidden="${this.showOrganizerPanelEnabled_}">\n',
    '        label="$i18n{showTabGroupsInBookmarksBar}"\n'
    '        hidden>\n'),
    # No leading indent: 157 moved the label onto the pref-key line.
    'label="$i18n{autoPinNewTabGroups}"\n'
    '        ?hidden="${this.showOrganizerPanelEnabled_}">\n',
    'label="$i18n{autoPinNewTabGroups}"\n'
    '        hidden>\n'))

# ---------------------------------------------------------------- theme
T = "mast-theme.patch"

# Chromium's own cool dark neutrals (match the demo) and surface tokens.
edit(T, "ui/base/ui_base_features.cc", lambda t: once(once(t,
    'BASE_FEATURE(kChromeDarkNeutrals26, base::FEATURE_DISABLED_BY_DEFAULT);',
    'BASE_FEATURE(kChromeDarkNeutrals26, base::FEATURE_ENABLED_BY_DEFAULT);'),
    'BASE_FEATURE(kDarkModeSurfaceTokens, base::FEATURE_DISABLED_BY_DEFAULT);',
    'BASE_FEATURE(kDarkModeSurfaceTokens, base::FEATURE_ENABLED_BY_DEFAULT);'))

# Mast accent palette, right after the reference palette.
edit(T, "ui/color/color_mixers.cc", lambda t: once(once(t,
    '#include "ui/color/color_mixers.h"\n',
    '#include "ui/color/color_mixers.h"\n\n#include "mast/ui/color/mast_ref_color_mixer.h"\n'),
    '  AddRefColorMixer(provider, key);\n',
    '  AddRefColorMixer(provider, key);\n  mast::AddMastRefColorMixer(provider, key);\n'))
edit(T, "ui/color/BUILD.gn", lambda t: once(t,
    '    "color_mixers.cc",\n',
    '    "color_mixers.cc",\n    "//mast/ui/color/mast_palette.h",\n    "//mast/ui/color/mast_ref_color_mixer.cc",\n    "//mast/ui/color/mast_ref_color_mixer.h",\n'))

# Settings (and other WebUI pages) paint their page and cards from two hard-coded dark
# greys in shared CSS, so the palette never reached them. They read the browser's colours
# instead (chrome://theme/colors.css, which Mast's mixer sets per palette), keeping the old
# values for pages that don't load colors.css.
edit(T, "ui/webui/resources/css/md_colors.css", lambda t: once(t,
    '    --md-background-color: rgb(32, 33, 36);  /* --google-grey-900 */\n',
    '    /* Mast: the palette\'s page colour. */\n'
    '    --md-background-color: var(--color-webui-page-background, rgb(32, 33, 36));\n'))
edit(T, "ui/webui/resources/cr_elements/cr_shared_vars.css", lambda t: once(t,
    '    --cr-card-background-color: var(--google-grey-900-white-4-percent);\n',
    '    /* Mast: the palette\'s raised surface. */\n'
    '    --cr-card-background-color: var(--color-webui-card-background,\n'
    '        var(--google-grey-900-white-4-percent));\n'))

# Settings only loads the browser's colours (chrome://theme/colors.css) behind Chromium's
# 2026 redesign flags; without them it paints Google's greys and blues, whatever the
# palette. It always loads them in Mast, and follows palette changes live.
edit(T, "chrome/browser/resources/settings/settings_ui/settings_ui.ts", lambda t: once(t,
    "    const enableThemedColors =\n"
    "        loadTimeData.getString('webuiRefresh2026') !== '' ||\n"
    "        loadTimeData.getString('settingsRefresh2026') !== '';\n",
    "    // Mast: always themed, so Settings follows the palette.\n"
    "    const enableThemedColors = true;\n"))
# The selected Settings menu item was Google blue; it's the palette's raised surface.
edit(T, "ui/webui/resources/cr_elements/cr_nav_menu_item_style_lit.css", lambda t: once(t,
    '    --cr-nav-menu-item-color: white;\n'
    '    --cr-nav-menu-item-color-selected: var(--google-grey-900);\n\n'
    '    --cr-nav-menu-item-background-color-hover: var(--google-grey-800);\n'
    '    --cr-nav-menu-item-background-color-selected: var(--google-blue-300);\n\n'
    '    --cr-nav-menu-item-icon-color: var(--google-grey-500);\n'
    '    --cr-nav-menu-item-icon-color-hover: white;\n'
    '    --cr-nav-menu-item-icon-color-selected: black;\n',
    '    /* Mast: the palette (colors.css), Chromium\'s values as fallbacks. */\n'
    '    --cr-nav-menu-item-color: var(--color-primary-foreground, white);\n'
    '    --cr-nav-menu-item-color-selected: var(--color-primary-foreground,\n'
    '        var(--google-grey-900));\n\n'
    '    --cr-nav-menu-item-background-color-hover: var(\n'
    '        --color-webui-card-background, var(--google-grey-800));\n'
    '    --cr-nav-menu-item-background-color-selected: var(\n'
    '        --color-menu-item-background-selected, var(--google-blue-300));\n\n'
    '    --cr-nav-menu-item-icon-color: var(--color-secondary-foreground,\n'
    '        var(--google-grey-500));\n'
    '    --cr-nav-menu-item-icon-color-hover: var(--color-primary-foreground, white);\n'
    '    --cr-nav-menu-item-icon-color-selected: var(--color-primary-foreground,\n'
    '        black);\n'))

# Mast surfaces, after all of Chrome's mixers.
edit(T, "chrome/browser/ui/color/chrome_color_mixers.cc", lambda t: once(once(t,
    '#include "chrome/browser/ui/color/chrome_color_mixers.h"\n',
    '#include "chrome/browser/ui/color/chrome_color_mixers.h"\n\n#include "mast/browser/ui/color/mast_chrome_color_mixer.h"\n'),
    '  AddMaterialTabStripColorMixer(provider, key);\n',
    '  AddMaterialTabStripColorMixer(provider, key);\n  mast::AddMastChromeColorMixer(provider, key);\n'))
edit(T, "chrome/browser/ui/color/BUILD.gn", lambda t: add_dep(once(t,
    '    "chrome_color_mixers.cc",\n',
    '    "chrome_color_mixers.cc",\n    "//mast/browser/ui/color/mast_chrome_color_mixer.cc",\n    "//mast/browser/ui/color/mast_chrome_color_mixer.h",\n    "//mast/ui/color/mast_site_color.h",\n'),
    'source_set("mixers") {', "//mast/common"))

# Calm UI: no ink ripples (the demo has none), no profile button (no sign-in),
# Inter for internal pages instead of Roboto.
edit(T, "ui/views/style/platform_style.h", lambda t: once(t,
    'static constexpr bool kUseRipples = !BUILDFLAG(IS_MAC);',
    'static constexpr bool kUseRipples = false;  // Mast: no ink ripples.'))
edit(T, "ui/base/webui/web_ui_util.cc", lambda t: once(t,
    'return "Roboto, " + GetFontFamily();', 'return "Inter, \\"Inter Variable\\", " + GetFontFamily();'))

# Native UI shape (docs/NATIVE-UI.md). The omnibox focus ring gets its own colour id,
# which skips Chromium's 3:1 contrast lift, and a 1px halo: a hairline, not a glow.
edit(T, "chrome/browser/ui/views/location_bar/location_bar_view.cc", lambda t: once(t,
    '    views::FocusRing::Get(this)->SetOutsetFocusRingDisabled(true);\n',
    '    views::FocusRing::Get(this)->SetOutsetFocusRingDisabled(true);\n'
    '    // Mast: a hairline focus ring (kColorLocationBarBorder in Mast\'s mixer).\n'
    '    views::FocusRing::Get(this)->SetColorId(kColorLocationBarBorder);\n'
    '    views::FocusRing::Get(this)->SetHaloThickness(1.f);\n'))

# One radius table for dialogs, menus, comboboxes, textfields and buttons: tighter
# corners (Mast's bubbles are 10px, its buttons 6px; Chromium's are 12-16px and pills).
def radii(t):
    t = once(t, '    case ShapeSysTokens::kSmall:\n      return 8;\n', '    case ShapeSysTokens::kSmall:\n      return 6;  // Mast\n')
    t = once(t, '    case ShapeSysTokens::kMediumSmall:\n      return 12;\n', '    case ShapeSysTokens::kMediumSmall:\n      return 10;  // Mast\n')
    t = once(t, '    case ShapeSysTokens::kMedium:\n      return 16;\n', '    case ShapeSysTokens::kMedium:\n      return 10;  // Mast\n')
    t = once(t, '          {ShapeContextTokens::kButtonRadius, ShapeSysTokens::kFull},\n',
             '          {ShapeContextTokens::kButtonRadius, ShapeSysTokens::kSmall},  // Mast\n')
    return t
edit(T, "ui/views/layout/layout_provider.cc", radii)

# Inter for the native UI too. "Inter Variable" is the name of the variable fonts Mast ships
# beside the browser (fonts/, remote/build.sh): every weight in 1.8 MB, where the static set
# named "Inter" is 13 MB.
edit(T, "ui/gfx/platform_font_skia.cc", lambda t: once(t,
    '    params = linux_ui->GetDefaultFontRenderParams();\n',
    '    params = linux_ui->GetDefaultFontRenderParams();\n    family = "Inter Variable";  // Mast\n'))
# ...and the fonts themselves: mast-fonts/ beside the browser joins the system's fonts when
# fontconfig starts (as ChromeOS adds Google Sans here), so Inter shows on systems that
# don't have it installed, in the UI and in pages alike.
edit(T, "ui/gfx/linux/fontconfig_util.cc", lambda t: once(once(t,
    '#include "base/check_op.h"\n',
    '#include "base/check_op.h"\n#include "base/base_paths.h"\n#include "base/files/file_path.h"\n'
    '#include "base/files/file_util.h"\n#include "base/path_service.h"\n'),
    '    // Set rescan interval to 0 to disable re-scan. Re-scanning in the\n',
    '    // Mast: its UI and page font (Inter), shipped beside the browser.\n'
    '    base::FilePath mast_exe_dir;\n'
    '    if (base::PathService::Get(base::DIR_EXE, &mast_exe_dir)) {\n'
    '      const base::FilePath mast_fonts = mast_exe_dir.Append("mast-fonts");\n'
    '      if (base::PathExists(mast_fonts)) {\n'
    '        FcConfigAppFontAddDir(\n'
    '            fc_config_, reinterpret_cast<const FcChar8*>(mast_fonts.value().c_str()));\n'
    '      }\n'
    '    }\n\n'
    '    // Set rescan interval to 0 to disable re-scan. Re-scanning in the\n'))

# Tabs: 8px top corners and a small foot (4px) instead of Chrome's swoop. The foot
# stays above the separator width so the tab-overlap maths stays positive.
edit(T, "chrome/browser/ui/tabs/tab_style.cc", lambda t: once(once(t,
    'int TabStyle::GetTopCornerRadius() const {\n  return 10;\n',
    'int TabStyle::GetTopCornerRadius() const {\n  return 8;  // Mast\n'),
    'int TabStyle::GetBottomCornerRadius() const {\n  return 12;\n',
    'int TabStyle::GetBottomCornerRadius() const {\n  return 4;  // Mast\n'))

# ---------------------------------------------------------------- one row
# Tabs, then back / address / forward, then the menu, in one row that is also the
# titlebar (mast/common/mast_features.h; --disable-features=MastOneRowWindow for two
# rows). The geometry is mast/browser/ui/mast_one_row_layout.h; these are the hooks.
R = "mast-onerow.patch"

# Tabs take the row up to where the toolbar starts; the toolbar's top container is
# exactly the slice at the end, so it never paints over the tabs.
edit(R, "chrome/browser/ui/views/frame/layout/browser_view_tabbed_layout_impl.cc", lambda t: once(once(once(t,
    '#include "chrome/browser/ui/views/frame/layout/browser_view_tabbed_layout_impl.h"\n',
    '#include "chrome/browser/ui/views/frame/layout/browser_view_tabbed_layout_impl.h"\n\n#include "mast/browser/ui/mast_one_row_layout.h"\n'),
    '          params, views().horizontal_tab_strip_region_view, leading_margin);\n'
    '      params.SetTop(tabstrip_bounds.bottom() -\n'
    '                    GetLayoutConstant(LayoutConstant::kTabstripToolbarOverlap));\n'
    '      needs_exclusion = false;\n    }\n    layout.AddChild(views().horizontal_tab_strip_region_view, tabstrip_bounds,\n'
    '                    layout_data_->tab_strip_type == TabStripType::kHorizontal);\n  }\n\n  // This will be',
    '          params, views().horizontal_tab_strip_region_view, leading_margin);\n'
    '      if (mast::IsOneRowWindow()) {\n'
    '        // Mast: the toolbar shares this row (see CalculateTopContainerLayout).\n'
    '        params.SetTop(mast::FitTabStripInRow(\n'
    '            tabstrip_bounds, params.visual_client_area.x(),\n'
    '            GetLayoutConstant(LayoutConstant::kTabstripToolbarOverlap),\n'
    '            views().horizontal_tab_strip_region_view, views().toolbar));\n'
    '      } else {\n'
    '      params.SetTop(tabstrip_bounds.bottom() -\n'
    '                    GetLayoutConstant(LayoutConstant::kTabstripToolbarOverlap));\n'
    '      }\n      needs_exclusion = false;\n    }\n    layout.AddChild(views().horizontal_tab_strip_region_view, tabstrip_bounds,\n'
    '                    layout_data_->tab_strip_type == TabStripType::kHorizontal);\n  }\n\n  // This will be'),
    '    bool needs_exclusion) const {\n  const int original_top = params.visual_client_area.y();\n',
    '    bool needs_exclusion) const {\n  const int original_top = params.visual_client_area.y();\n\n'
    '  // Mast: one row. The top container is the toolbar\'s slice at the end of\n'
    '  // the tab row, just above this area (no bookmarks bar or separator in it).\n'
    '  if (const gfx::Rect slice =\n'
    '          mast::IsOneRowWindow() &&\n'
    '                  layout_data_->tab_strip_type == TabStripType::kHorizontal &&\n'
    '                  IsParentedTo(views().toolbar, views().top_container) &&\n'
    '                  IsParentedTo(views().horizontal_tab_strip_region_view,\n'
    '                               views().browser_view)\n'
    '              ? mast::TakeOneRowToolbarSlice(params.visual_client_area)\n'
    '              : gfx::Rect();\n'
    '      !slice.IsEmpty()) {\n'
    '    layout.AddChild(views().toolbar, gfx::Rect(slice.size()),\n'
    '                    delegate().IsToolbarVisible());\n'
    '    if (IsParentedTo(views().bookmark_bar, views().top_container)) {\n'
    '      layout.AddChild(views().bookmark_bar, gfx::Rect(), false);\n    }\n'
    '    if (IsParentedTo(views().top_container_separator, views().top_container)) {\n'
    '      layout.AddChild(views().top_container_separator, gfx::Rect(), false);\n    }\n'
    '    return slice;\n  }\n'))
edit(R, "chrome/browser/ui/views/frame/BUILD.gn", lambda t: add_dep(t, 'source_set("browser_view_tabbed_layout_impl") {', "//mast/common"))

# Tabs are pills: every tab shape (fill, border, clip) uses the detached rounded rect
# Chromium already draws for hover and hit tests, instead of the swoop into the toolbar.
edit(R, "chrome/browser/ui/views/tabs/common/horizontal_tab_style_views.cc", lambda t: once(once(once(t,
    '#include "chrome/browser/ui/views/tabs/common/horizontal_tab_style_views.h"\n',
    '#include "chrome/browser/ui/views/tabs/common/horizontal_tab_style_views.h"\n\n#include "mast/common/mast_features.h"\n'),
    '  if (path_type == TabStyle::PathType::kHighlight ||\n      path_type == TabStyle::PathType::kHitTest) {\n    float top_left_corner_radius',
    '  if (path_type == TabStyle::PathType::kHighlight ||\n      path_type == TabStyle::PathType::kHitTest ||\n'
    '      mast::IsOneRowWindow()) {  // Mast: pill tabs.\n    float top_left_corner_radius'),
    '    float right = aligned_bounds.right() - extension_corner_radius;\n    const int bottom = top + tab_height;\n',
    '    float right = aligned_bounds.right() - extension_corner_radius;\n'
    '    // Mast: in one row the pill is centred, with an even gap above and below.\n'
    '    if (mast::IsOneRowWindow() && path_type != TabStyle::PathType::kHitTest) {\n'
    '      top = aligned_bounds.y() + mast::kOneRowTabPillInset * scale;\n'
    '      tab_height =\n          aligned_bounds.height() - 2 * mast::kOneRowTabPillInset * scale;\n    }\n'
    '    const int bottom = top + tab_height;\n'))
edit(R, "chrome/browser/ui/views/tabs/common/BUILD.gn", lambda t: add_dep(t, 'source_set("impl") {', "//mast/common"))

# The suggestion list opens wider than the narrow address bar, growing left.
edit(R, "chrome/browser/ui/views/omnibox/omnibox_popup_presenter_base.cc", lambda t: once(once(t,
    '#include "chrome/browser/ui/views/omnibox/omnibox_popup_presenter_base.h"\n',
    '#include "chrome/browser/ui/views/omnibox/omnibox_popup_presenter_base.h"\n\n#include "mast/browser/ui/mast_one_row_layout.h"\n'),
    '    gfx::Rect widget_bounds = location_bar_->BoundsInScreen();\n',
    '    gfx::Rect widget_bounds = location_bar_->BoundsInScreen();\n'
    '    if (widget_->parent()) {  // Mast: the popup\'s parent is the browser window.\n'
    '      mast::WidenOmniboxPopup(widget_bounds,\n'
    '                              widget_->parent()->GetWindowBoundsInScreen());\n    }\n'))
edit(R, "chrome/browser/ui/views/omnibox/BUILD.gn", lambda t: add_dep(t, 'source_set("impl") {', "//mast/common"))

# The toolbar's one-row arrangement, once the window and its toolbar exist (the
# embedder hook Chromium leaves for forks).
edit(R, "chrome/browser/ui/browser_window/internal/embedder_browser_window_features.cc", lambda t: once(once(t,
    '#include "chrome/browser/ui/views/frame/browser_view.h"\n',
    '#include "chrome/browser/ui/views/frame/browser_view.h"\n#include "mast/browser/ui/mast_one_row_toolbar.h"\n'),
    'void EmbedderBrowserWindowFeatures::InitPostWindowConstruction(\n    BrowserWindowInterface* browser) {\n  // Embedder implementation goes here.\n}',
    'void EmbedderBrowserWindowFeatures::InitPostWindowConstruction(\n    BrowserWindowInterface* browser) {\n  mast::OnBrowserWindowReady(browser);\n}'))
edit(R, "chrome/browser/ui/browser_window/internal/BUILD.gn", lambda t: add_dep(add_dep(add_dep(add_dep(add_dep(add_dep(add_dep(add_dep(add_dep(once(t,
    '      "embedder_browser_window_features.cc",\n',
    '      "embedder_browser_window_features.cc",\n      "//mast/browser/ui/mast_one_row_toolbar.cc",\n      "//mast/browser/ui/mast_one_row_toolbar.h",\n'
    '      "//mast/browser/ui/mast_tab_bar.cc",\n      "//mast/browser/ui/mast_tab_bar.h",\n'
    '      "//mast/browser/ui/mast_shortcuts.cc",\n      "//mast/browser/ui/mast_shortcuts.h",\n'
    '      "//mast/browser/ui/mast_widevine.cc",\n      "//mast/browser/ui/mast_widevine.h",\n'
    '      "//mast/browser/ui/mast_update_check.cc",\n      "//mast/browser/ui/mast_update_check.h",\n'),
    'source_set("internal") {', "//mast/common"),
    'source_set("internal") {', "//components/prefs"),
    'source_set("internal") {', "//content/public/browser"),
    'source_set("internal") {', "//skia"),
    'source_set("internal") {', "//components/viz/common"),
    'source_set("internal") {', "//ui/gfx/animation"),
    'source_set("internal") {', "//ui/gfx/geometry"),
    'source_set("internal") {', "//third_party/widevine/cdm:buildflags"),  # mast_widevine.cc
    'source_set("internal") {', "//services/network/public/cpp"))  # mast_update_check.cc

# No dividers between tabs in the one-row window: tabs are pills with space between them.
# (Chromium paints dividers with SkColorSetA(colour, opacity), which overrides the colour's
# alpha, so a transparent divider colour came out as an opaque black line on a picture.)
edit(R, "chrome/browser/ui/views/tabs/common/horizontal_tab_style_views.cc", lambda t: once(t,
    'void HorizontalTabStyleViews::PaintSeparators(gfx::Canvas* canvas) const {\n',
    'void HorizontalTabStyleViews::PaintSeparators(gfx::Canvas* canvas) const {\n'
    '  if (mast::IsOneRowWindow()) {\n    return;  // Mast: pills, no dividers\n  }\n'))

# Browser pages' tab icons (the new tab's sails, Settings' gear...) are one colour in the
# one-row window: the tabs' text colour, so they read on any palette or picture, light or
# dark. Chromium only recolours them when their contrast looks low, and it measures that
# against the pill colour as if it were opaque. Sites' own favicons are untouched.
edit(R, "chrome/browser/ui/tabs/tab_favicon_theming.cc", lambda t: once(once(t,
    '#include "ui/gfx/image/image_skia.h"\n',
    '#include "ui/gfx/image/image_skia.h"\n#include "ui/gfx/image/image_skia_operations.h"\n'
    '#include "mast/common/mast_features.h"\n'),
    'gfx::ImageSkia ThemeFaviconForTab(const gfx::ImageSkia& source,\n'
    '                                  const ui::ColorProvider& color_provider) {\n',
    'gfx::ImageSkia ThemeFaviconForTab(const gfx::ImageSkia& source,\n'
    '                                  const ui::ColorProvider& color_provider) {\n'
    '  if (mast::IsOneRowWindow()) {\n'
    '    return gfx::ImageSkiaOperations::CreateColorMask(\n'
    '        source,\n'
    '        color_provider.GetColor(kColorTabForegroundInactiveFrameActive));\n'
    '  }\n'))
edit(R, "chrome/browser/ui/tabs/BUILD.gn", lambda t: add_dep(t, 'source_set("favicon_theming") {', "//mast/common"))

# Tab groups as pills (design C22, option A). In the one-row window a group's header is a
# pill like the tabs, tinted with the group's colour, and the group's header and pills sit
# in a faint lane of that colour. Chromium's 2px underline along the row's edge and the
# group-coloured outline around the active tab go.
edit(R, "chrome/browser/ui/views/tabs/tab_group_header.cc", lambda t: once(once(once(once(once(once(once(once(t,
    '#include "chrome/browser/ui/layout_constants.h"\n',
    '#include "chrome/browser/ui/layout_constants.h"\n#include "mast/common/mast_features.h"\n'),
    'int TabGroupHeader::GetChipHeight() const {\n',
    'int TabGroupHeader::GetChipHeight() const {\n'
    '  if (mast::IsOneRowWindow()) {\n'
    '    // Mast: as tall as the tab pills, from the header\'s own height (the\n'
    '    // one-row tabs are taller than kTabHeight).\n'
    '    const int row = height() > 0 ? height()\n'
    '                                 : GetLayoutConstant(LayoutConstant::kTabHeight);\n'
    '    return row - 2 * mast::kOneRowTabPillInset;\n  }\n'),
    '  title_chip_->SetBounds(named_origin.x(), chip_y, title_chip_width,\n                         chip_height);\n',
    '  // Mast: the tabs\' side gap (4) before the pill, as each tab pill has.\n'
    '  title_chip_->SetBounds(named_origin.x() + (mast::IsOneRowWindow() ? 4 : 0),\n'
    '                         chip_y, title_chip_width, chip_height);\n'),
    '  return overlap_margin + title_chip_->width();\n',
    '  // Mast: room for the side gap on both sides of the pill.\n'
    '  return overlap_margin + title_chip_->width() +\n'
    '         (mast::IsOneRowWindow() ? 8 : 0);\n'),
    'int TabGroupHeader::GetChipY() const {\n',
    'int TabGroupHeader::GetChipY() const {\n'
    '  if (mast::IsOneRowWindow()) {\n    return mast::kOneRowTabPillInset;\n  }\n'),
    '  title_chip_->SetBackground(\n      views::CreateRoundedRectBackground(color_, corner_radius));\n',
    '  // Mast: a pill tinted with the group\'s colour (its name is in that colour).\n'
    '  title_chip_->SetBackground(views::CreateRoundedRectBackground(\n'
    '      mast::IsOneRowWindow() ? SkColorSetA(color_, 0x40) : color_,\n'
    '      mast::IsOneRowWindow() ? 8 : corner_radius));\n'),
    '      title_->SetEnabledColor(color_utils::GetColorWithMaxContrast(color_));\n',
    '      title_->SetEnabledColor(mast::IsOneRowWindow()\n'
    '                                  ? color_\n'
    '                                  : color_utils::GetColorWithMaxContrast(color_));\n'),
    '  title_chip_->SetBounds(empty_origin.x(), chip_y, empty_width, chip_height);\n'
    '  title_chip_->SetBackground(group_style_->GetEmptyTitleChipBackground(color_));\n',
    '  title_chip_->SetBounds(empty_origin.x(), chip_y, empty_width, chip_height);\n'
    '  title_chip_->SetBackground(group_style_->GetEmptyTitleChipBackground(color_));\n'
    '  if (mast::IsOneRowWindow()) {\n'
    '    // Mast: an unnamed group is a dot of its colour, centred in the row.\n'
    '    constexpr int kDot = 12;\n'
    '    title_chip_->SetBounds(empty_origin.x() + (empty_width - kDot) / 2,\n'
    '                           chip_y + (chip_height - kDot) / 2, kDot, kDot);\n'
    '    title_chip_->SetBackground(\n'
    '        views::CreateRoundedRectBackground(color_, kDot / 2));\n  }\n'))
edit(R, "chrome/browser/ui/views/tabs/tab_group_underline.cc", lambda t: once(once(once(once(t,
    '    : tab_group_views_(tab_group_views), group_(group), style_(style) {}\n',
    '    : tab_group_views_(tab_group_views), group_(group), style_(style) {\n'
    '  // Mast: in one row this is the group\'s lane, as tall as the tabs. It\'s\n'
    '  // only paint: the tab strip hands clicks to the first non-tab view it hits\n'
    '  // (TabContainerImpl::TargetForRect), so a hit-testable lane would swallow\n'
    '  // every click on its tabs.\n'
    '  if (mast::IsOneRowWindow()) {\n    SetCanProcessEventsWithinSubtree(false);\n  }\n}\n'),
    '#include "chrome/browser/ui/views/tabs/tab_group_views.h"\n',
    '#include "chrome/browser/ui/views/tabs/tab_group_views.h"\n#include "mast/common/mast_features.h"\n\n#include <algorithm>\n'),
    'void TabGroupUnderline::OnPaint(gfx::Canvas* canvas) {\n',
    'void TabGroupUnderline::OnPaint(gfx::Canvas* canvas) {\n'
    '  if (mast::IsOneRowWindow()) {\n'
    '    // Mast: the group\'s lane, a faint pill of its colour behind its tabs.\n'
    '    cc::PaintFlags lane;\n    lane.setAntiAlias(true);\n'
    '    lane.setColor(SkColorSetA(tab_group_views_->GetGroupColor(), 0x24));\n'
    '    canvas->DrawRoundRect(GetLocalBounds(), 10, lane);\n    return;\n  }\n'),
    '    const views::View* const trailing_view) const {\n'
    '  gfx::RectF leading_bounds = views::View::ConvertRectToTarget(\n',
    '    const views::View* const trailing_view) const {\n'
    '  if (mast::IsOneRowWindow()) {\n'
    '    // Mast: the lane wraps the header pill and the tab pills, 3px around\n'
    '    // them (tab pills are inset by the bottom corner radius at the sides and\n'
    '    // kOneRowTabPillInset above and below).\n'
    '    const gfx::Rect leading = ToEnclosingRect(views::View::ConvertRectToTarget(\n'
    '        leading_view->parent(), underline_view->parent(),\n'
    '        gfx::RectF(leading_view->bounds())));\n'
    '    const gfx::Rect trailing = ToEnclosingRect(views::View::ConvertRectToTarget(\n'
    '        trailing_view->parent(), underline_view->parent(),\n'
    '        gfx::RectF(trailing_view->bounds())));\n'
    '    const int side = TabStyle::Get()->GetBottomCornerRadius();\n'
    '    const int left =\n'
    '        leading.x() - 3 +\n'
    '        (views::IsViewClass<TabGroupHeader>(leading_view)\n'
    '             ? TabGroupStyle::GetTitleChipOffset(std::nullopt).x() + 4\n'
    '             : side);\n'
    '    const int right = trailing.right() + 3 - side;\n'
    '    const int inset = mast::kOneRowTabPillInset - 3;\n'
    '    return gfx::Rect(left, leading.y() + inset, std::max(0, right - left),\n'
    '                     std::max(0, leading.height() - 2 * inset));\n  }\n'
    '  gfx::RectF leading_bounds = views::View::ConvertRectToTarget(\n'))
edit(R, "chrome/browser/ui/views/tabs/z_orderable_tab_container_element.cc", lambda t: once(once(t,
    '#include "chrome/browser/ui/views/tabs/tab_style_views.h"\n',
    '#include "chrome/browser/ui/views/tabs/tab_style_views.h"\n#include "mast/common/mast_features.h"\n'),
    '  if (header || underline) {\n',
    '  // Mast: in one row the underline is the group\'s lane, below every tab.\n'
    '  if (header || (underline && !mast::IsOneRowWindow())) {\n'))
# No group-coloured outline around a grouped tab's pill (the lane shows the group).
edit(R, "chrome/browser/ui/views/tabs/common/horizontal_tab_style_views.cc", lambda t: once(t,
    '  PaintBackgroundStroke(canvas, group_color.value_or(tab_stroke_color));\n',
    '  PaintBackgroundStroke(canvas, mast::IsOneRowWindow()\n'
    '                                    ? tab_stroke_color\n'
    '                                    : group_color.value_or(tab_stroke_color));\n'))
edit(R, "chrome/browser/ui/views/tabs/BUILD.gn", lambda t: add_dep(add_dep(t,
    'source_set("tab_group_impl") {', "//mast/common"), 'source_set("tab_container_impl") {', "//mast/common"))

# No 1px line between the tab row and the page: in the one-row window the page meets the
# row directly (with a wallpaper the line cut the picture in two).
edit(R, "chrome/browser/ui/views/frame/layout/browser_view_tabbed_layout_impl.cc", lambda t: once(t,
    '  views().multi_contents_view->SetShouldShowTopSeparator(\n'
    '      separator_info.multi_contents_separator);\n',
    '  views().multi_contents_view->SetShouldShowTopSeparator(\n'
    '      separator_info.multi_contents_separator && !mast::IsOneRowWindow());\n'))

# On Customize's image the address field is translucent (mast_chrome_color_mixer.cc). Its
# textfield copies the field's colour as its own background, which would paint a darker
# rectangle inside the pill; with a translucent field it paints none.
edit(T, "chrome/browser/ui/views/location_bar/location_bar_view.cc", lambda t: once(t,
    '  omnibox_view_->SetBackgroundColor(background_color_);\n',
    '  // Mast: no second (darker) layer inside a translucent field.\n'
    '  omnibox_view_->SetBackgroundColor(\n'
    '      SkColorGetA(background_color_) == SK_AlphaOPAQUE ? background_color_\n'
    '                                                       : SK_ColorTRANSPARENT);\n'))

# Customize's image behind the tab row (mast/browser/ui/mast_wallpaper.h). The two places
# Chromium paints a theme's frame image: the frame view's GetFrameImage() (every platform's
# frame tiles it behind the tabs and window buttons) and ThemedBackground (the toolbar,
# which is in the row in the one-row window). Both align it to the same origin.
edit(R, "chrome/browser/ui/views/frame/browser_frame_view.cc", lambda t: once(once(t,
    '#include "chrome/grit/theme_resources.h"\n',
    '#include "chrome/grit/theme_resources.h"\n#include "mast/browser/ui/mast_wallpaper.h"\n'),
    'gfx::ImageSkia BrowserFrameView::GetFrameImage(\n    BrowserFrameActiveState active_state) const {\n',
    'gfx::ImageSkia BrowserFrameView::GetFrameImage(\n    BrowserFrameActiveState active_state) const {\n'
    '  // Mast: Customize\'s image continues behind the tab row.\n'
    '  if (gfx::ImageSkia wallpaper = mast::WallpaperFrameImage(GetBrowserView());\n'
    '      !wallpaper.isNull()) {\n    return wallpaper;\n  }\n'))
# Behind a new tab page (mast_wallpaper.h, WallpaperBehindPage): the page area is
# transparent there, and its container paints the whole picture under it, so a new tab
# shows the picture at once instead of black until the page has painted.
edit(R, "chrome/browser/ui/views/frame/contents_web_view.cc", lambda t: once(once(once(t,
    '#include "chrome/browser/ui/views/frame/contents_web_view.h"\n',
    '#include "chrome/browser/ui/views/frame/contents_web_view.h"\n\n#include "mast/browser/ui/mast_wallpaper.h"\n'),
    '  auto* background_layer = layer()->AsSolidColor();\n'
    '  background_layer->SetColor(\n'
    '      SkColor4f::FromColor(background_visible_ ? color : SK_ColorTRANSPARENT));\n'
    '\n'
    '  SafeInvoke(web_contents())\n'
    '      .Then(&content::WebContents::GetRenderWidgetHostView)\n'
    '      .Then(&content::RenderWidgetHostView::SetBackgroundColor,\n'
    '            background_visible_ ? color : SK_ColorTRANSPARENT);\n',
    '  // Mast: transparent over Customize\'s picture on a new tab page; the\n'
    '  // container paints the picture under it.\n'
    '  const bool mast_picture = mast::WallpaperBehindPage(this);\n'
    '  const bool visible = background_visible_ && !mast_picture;\n'
    '  auto* background_layer = layer()->AsSolidColor();\n'
    '  background_layer->SetColor(\n'
    '      SkColor4f::FromColor(visible ? color : SK_ColorTRANSPARENT));\n'
    '\n'
    '  SafeInvoke(web_contents())\n'
    '      .Then(&content::WebContents::GetRenderWidgetHostView)\n'
    '      .Then(&content::RenderWidgetHostView::SetBackgroundColor,\n'
    '            visible ? color : SK_ColorTRANSPARENT);\n'
    '  if (parent()) {\n    parent()->SchedulePaint();\n  }\n'),
    # Another tab in this view (a tab switch): the page area follows it.
    '  SafeInvoke(\n'
    '      web_modal::WebContentsModalDialogManager::FromWebContents(web_contents))\n'
    '      .Then(&web_modal::WebContentsModalDialogManager::UpdateDialogHost);\n}\n',
    '  SafeInvoke(\n'
    '      web_modal::WebContentsModalDialogManager::FromWebContents(web_contents))\n'
    '      .Then(&web_modal::WebContentsModalDialogManager::UpdateDialogHost);\n'
    '  if (GetWidget()) {\n    UpdateBackgroundColor();  // Mast: a new tab page shows the picture\n  }\n}\n'))
edit(R, "chrome/browser/ui/views/frame/contents_container_view.cc", lambda t: once(once(t,
    '#include "chrome/browser/ui/views/frame/contents_web_view.h"\n',
    '#include "chrome/browser/ui/views/frame/contents_web_view.h"\n#include "mast/browser/ui/mast_wallpaper.h"\n'),
    '  contents_view_->SetID(VIEW_ID_TAB_CONTAINER);\n',
    '  contents_view_->SetID(VIEW_ID_TAB_CONTAINER);\n'
    '  // Mast: Customize\'s picture under a new tab page (mast_wallpaper.h).\n'
    '  SetBackground(mast::CreateNewTabPageBackground(contents_view_));\n'))
edit(R, "chrome/browser/ui/views/frame/themed_background.cc", lambda t: once(once(t,
    '#include "chrome/browser/ui/color/chrome_color_id.h"\n',
    '#include "chrome/browser/ui/color/chrome_color_id.h"\n#include "mast/browser/ui/mast_wallpaper.h"\n'),
    '                                       ThemeChoice theme_choice) {\n  bool painted =\n',
    '                                       ThemeChoice theme_choice) {\n'
    '  // Mast: the toolbar\'s part of the tab row shows Customize\'s image too.\n'
    '  if (mast::PaintWallpaperBackground(canvas, view, browser_view)) {\n    return;\n  }\n'
    '  bool painted =\n'))
edit(R, "chrome/browser/ui/views/frame/BUILD.gn", lambda t: add_dep(add_dep(add_dep(add_dep(add_dep(once(t,
    'source_set("themed_background_impl") {\n  sources = [ "themed_background.cc" ]\n',
    'source_set("themed_background_impl") {\n  sources = [\n    "themed_background.cc",\n'
    '    "//mast/browser/ui/mast_wallpaper.cc",\n    "//mast/browser/ui/mast_wallpaper.h",\n  ]\n'),
    'source_set("themed_background_impl") {', "//mast/common"),
    'source_set("themed_background_impl") {', "//ui/display"),
    'source_set("themed_background_impl") {', "//ui/views/controls/webview"),
    'source_set("themed_background_impl") {', "//content/public/browser"),
    'source_set("themed_background_impl") {', "//ui/gfx/codec"))

# Customize's "Tab bar on websites" (mast/browser/ui/mast_tab_bar.h): each one-row window's
# row can take the active website's colour, or the palette instead of the picture. Done
# the way Chromium recolours an installed web app's window from its theme-color: the
# window's ColorProviderKey carries the colour (app_background_color, which only app
# windows set and no mixer reads), and mast_chrome_color_mixer.cc builds the row from it.
edit(R, "chrome/browser/ui/views/frame/browser_widget.cc", lambda t: once(once(t,
    '#include "chrome/browser/ui/views/frame/browser_widget.h"\n',
    '#include "chrome/browser/ui/views/frame/browser_widget.h"\n\n#include "mast/common/mast_tab_bar_state.h"\n'),
    '    key.app_controller = app_controller;\n'
    '    key.app_background_color = app_controller->GetBackgroundColor();\n  }\n',
    '    key.app_controller = app_controller;\n'
    '    key.app_background_color = app_controller->GetBackgroundColor();\n'
    '  } else if (const std::optional<uint32_t> mast_row =\n'
    '                 mast::TabBarColor(browser_view_.get())) {\n'
    '    // Mast: what the tab row sits on for the active tab.\n'
    '    key.app_background_color = *mast_row;\n  }\n'))
edit(R, "chrome/browser/ui/views/frame/BUILD.gn", lambda t: add_dep(t, 'source_set("browser_widget_impl") {', "//mast/common"))
# Each website colour gets its own ColorProvider, and Chromium keeps every provider it
# builds. Keep the 24 newest Mast row colours; older ones go once no window shows them.
edit(T, "ui/color/color_provider_manager.cc", lambda t: once(once(once(t,
    '#include <algorithm>\n',
    '#include <algorithm>\n#include <deque>\n'),
    '#include "ui/webui/buildflags.h"\n',
    '#include "ui/webui/buildflags.h"\n\n#include "mast/common/mast_tab_bar_state.h"\n'),
    '    iter = color_providers_.emplace(key, std::move(provider)).first;\n',
    '    // Mast: "Match site" (mast/common/mast_tab_bar_state.h) makes a provider\n'
    '    // per website colour. Drop the oldest that no window shows any more.\n'
    '    if (key.app_background_color && !key.app_controller) {\n'
    '      static base::NoDestructor<std::deque<ColorProviderKey>> mast_rows;\n'
    '      mast_rows->push_back(key);\n'
    '      while (mast_rows->size() > 24) {\n'
    '        const auto old = std::ranges::find_if(\n'
    '            *mast_rows, [](const ColorProviderKey& k) {\n'
    '              return !mast::TabBarColorInUse(*k.app_background_color);\n'
    '            });\n'
    '        if (old == mast_rows->end()) {\n          break;\n        }\n'
    '        color_providers_.erase(*old);\n'
    '        mast_rows->erase(old);\n      }\n    }\n'
    '    iter = color_providers_.emplace(key, std::move(provider)).first;\n'))
edit(T, "ui/color/BUILD.gn", lambda t: add_dep(t, 'component("mixers") {', "//mast/common"))

# ---------------------------------------------------------------- branding
B = "mast-branding.patch"

# Product names in UI strings are renamed at build time by substitute-names.py
# (every .grd/.grdp), not by patch.

# The shared WebUI "chrome-product" glyph (Settings header, About) -> Mast sails.
SAILS = "M10 7.25V21.25H2.6Q3.4 12.75 10 7.25ZM13 2.75V21.25H21.4Q20.7 9.75 13 2.75Z"
def sails_glyph(t):
    t, n1 = re.subn(r'(<g id="chrome-product">\s*<path d=")[^"]*(")', r"\g<1>" + SAILS + r"\g<2>", t)
    t, n2 = re.subn(r'<g id="chrome-product" viewBox="0 -960 960 960">\s*<path d="[^"]*">\s*</path>',
                    '<g id="chrome-product" viewBox="0 0 24 24">\n        <path d="' + SAILS + '"></path>', t)
    assert (n1, n2) == (1, 1), (n1, n2)
    return t
edit(B, "ui/webui/resources/cr_elements/icons.html.ts", sails_glyph)

# The empty address bar says "Search": the one-row bar is narrow, and the engine's
# name isn't worth the room. (Other languages fall back to this English text.)
edit(B, "chrome/app/generated_resources.grd", lambda t: once(t,
    '        Search <ph name="SEARCH_ENGINE_NAME">$1<ex>Google</ex></ph> or type a URL\n      </message>\n      <if expr="not use_titlecase">',
    '        Search\n      </message>\n      <if expr="not use_titlecase">'))

# Linux identity: desktop file (and so WM_CLASS) is "mast".
edit(B, "chrome/common/channel_info_posix.cc", lambda t: once(t,
    'return "chromium-browser.desktop";', 'return "mast.desktop";'))
edit(B, "chrome/common/chrome_paths_linux.cc", lambda t: once(t,
    'std::string data_dir_basename = "chromium";', 'std::string data_dir_basename = "mast";'))
edit(B, "chrome/browser/ui/startup/infobar_utils.cc", lambda t: once(t,
    '  if (!google_apis::HasAPIKeyConfigured()) {\n',
    '  // Mast doesn\'t use Google API keys, so never nag about missing ones.\n  if (false) {\n'))

# ---------------------------------------------------------------- ungoogled adjustments
U = "mast-ungoogled.patch"

# Google stays the default search engine. Domain substitution rewrote kGoogleHomepageURL
# (to a dead *.qjz9zk host), and the Google engine's search and suggest URLs are built from
# this one value. Only searches the user types go to Google; background services stay off.
if UNGOOGLED:
    edit(U, "components/search_engines/search_terms_data.cc", lambda t: once(t,
        '  return google_util::kGoogleHomepageURL;\n',
        '  // Mast: searches the user types still go to Google (see mast-ungoogled.patch).\n  return "https://www.google.com/";\n'))

# Page preloading off by default. With Google as the search engine, Chromium otherwise
# prefetches Google's results page (and its trackers) while you type in the omnibox.
# People can still turn it on in Settings > Performance. (On pristine Chromium it's part
# of the privacy layer, mast-privacy.patch.)
edit(U if UNGOOGLED else "mast-privacy.patch", "chrome/browser/preloading/preloading_prefs.cc", lambda t: once(t,
    '      static_cast<int>(NetworkPredictionOptions::kDefault),\n',
    '      static_cast<int>(NetworkPredictionOptions::kDisabled),  // Mast\n'))

# ---------------------------------------------------------------- web store
# The Chrome Web Store works, like Brave ("Web Store"): browse, install, and extensions
# update. ungoogled-chromium cuts the store off twice: domain substitution makes its URLs
# dead *.qjz9zk hosts, and disable-webstore-urls.patch stops update checks and store
# recognition. Mast undoes that one patch (keeping the rest of ungoogled) and restores the
# store's real URLs; Mast's patches apply after substitution, so these stay real.
# Privacy: installs and update checks go to Google (extension IDs and versions, every few
# hours). Usage pings stay off: the downloader's ping domain is still the substituted one.
W = "mast-webstore.patch"
DWU = "core/ungoogled-chromium/disable-webstore-urls.patch"

if UNGOOGLED:
    # Undo disable-webstore-urls.patch in these files.
    for path in ("chrome/browser/extensions/chrome_content_browser_client_extensions_part.cc",
                 "chrome/browser/extensions/chrome_content_verifier_delegate.cc",
                 "chrome/browser/extensions/extension_migrator.cc",
                 "chrome/browser/extensions/extension_migrator.h",
                 "extensions/browser/updater/extension_downloader.cc"):
        edit(W, path, lambda t, path=path: unpatch(t, path, DWU))

    # The store's real URLs, and no utm_source tag on store links.
    edit(W, "extensions/common/extension_urls.cc", lambda t: once(once(once(once(once(
        unpatch(t, "extensions/common/extension_urls.cc", DWU),
        'const char kChromeWebstoreBaseURL[] = "https://chrome.9oo91e.qjz9zk/webstore";\n',
        'const char kChromeWebstoreBaseURL[] = "https://chrome.google.com/webstore";\n'),
        'const char kNewChromeWebstoreBaseURL[] = "https://chromewebstore.9oo91e.qjz9zk/";\n',
        'const char kNewChromeWebstoreBaseURL[] = "https://chromewebstore.google.com/";\n'),
        '    "https://clients2.9oo91e.qjz9zk/service/update2/crx";\n',
        '    "https://clients2.google.com/service/update2/crx";\n'),
        'const char kChromeWebstoreApiURL[] = "https://chromewebstore.9oo91eapis.qjz9zk/";\n',
        'const char kChromeWebstoreApiURL[] = "https://chromewebstore.googleapis.com/";\n'),
        # Store links carry no utm_source tag (which menu or page you came from).
        '  return net::AppendQueryParameter(url, "utm_source", utm_source_value);\n',
        '  return url;  // Mast: no tracking tag on store links\n'))
    # The store's Install button: inox's 0005-disable-default-extensions.patch (in ungoogled's
    # series) empties WebstoreInstaller's download and install steps, so a store install was
    # accepted and then never started (the button just spun). Undo that file's part only; the
    # rest of the patch (no default extensions) stays.
    edit(W, "extensions/browser/webstore_installer.cc", lambda t: unpatch(
        t, "extensions/browser/webstore_installer.cc",
        "core/inox-patchset/0005-disable-default-extensions.patch"))
    # The store's pages may use the install API (webstorePrivate) and management again.
    edit(W, "extensions/common/api/_api_features.json", lambda t: exactly(t,
        '    "matches": ["https://chromewebstore.9oo91e.qjz9zk/*"]\n',
        '    "matches": ["https://chromewebstore.google.com/*"]\n', 2))
    edit(W, "chrome/browser/resources/webstore_app/manifest.json", lambda t: exactly(t,
        'https://chrome.9oo91e.qjz9zk/webstore', 'https://chrome.google.com/webstore', 2))
    # Links: the extensions page's "Web Store" (empty state) and Settings' current-theme link.
    edit(W, "chrome/app/extensions_strings.grdp", lambda t: once(t,
        'href="https://chrome.9oo91e.qjz9zk/webstore/category/extensions"',
        'href="https://chromewebstore.google.com/category/extensions"'))
    edit(W, "chrome/browser/resources/settings/appearance_page/appearance_page.ts", lambda t: once(t,
        "this.themeUrl_ = 'https://chrome.9oo91e.qjz9zk/webstore/detail/' + themeId;",
        "this.themeUrl_ = 'https://chromewebstore.google.com/detail/' + themeId;"))
else:
    # Pristine Chromium: the store already works (nothing to undo); only the tracking tag
    # on store links goes.
    edit(W, "extensions/common/extension_urls.cc", lambda t: once(t,
        '  return net::AppendQueryParameter(url, "utm_source", utm_source_value);\n',
        '  return url;  // Mast: no tracking tag on store links\n'))

# ---------------------------------------------------------------- privacy
# Pristine Chromium only (MAST_BASE=chromium): every Google service Mast turns off, the way
# Brave does, generated from privacy.json (the list, with a reason for each). On the
# ungoogled base, ungoogled-chromium's patches do this instead.
P = "mast-privacy.patch"
PRIVACY = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "privacy.json")))


def privacy_edit(path, fn):
    """privacy.json's entries, grouped per file: one edit per upstream file, so a file
    changed by several entries builds on itself (and on the edits.json line of this one)."""
    edit(P, path, fn)


if not UNGOOGLED:
    by_file = {}
    for f in PRIVACY["features_off"]:
        by_file.setdefault(f["file"], []).append(lambda t, f=f: feature_off(t, f["feature"]))
    for s in PRIVACY["stubs"]:
        by_file.setdefault(s["file"], []).append(lambda t, s=s: once(
            t, s["at"], s["at"] + "  // Mast: privacy.json\n" + s["insert"]))
    for r in PRIVACY["replace"]:
        by_file.setdefault(r["file"], []).append(lambda t, r=r: once(
            t, r["old"], r["new"].rstrip("\n") + "  // Mast: privacy.json\n"))
    for path, fns in by_file.items():
        def apply(t, fns=fns):
            for fn in fns:
                t = fn(t)
            return t
        privacy_edit(path, apply)

    # Command-line switches, appended at startup unless given already (Brave sets its own in
    # app/brave_main_delegate.cc).
    def switch_line(s):
        name, value = s["switch"], s.get("value")
        add = (f'AppendSwitchASCII("{name}", "{value}")' if value is not None
               else f'AppendSwitch("{name}")')
        return f'    if (!mast_command_line->HasSwitch("{name}")) {{\n      mast_command_line->{add};\n    }}\n'
    privacy_edit("chrome/app/chrome_main_delegate.cc", lambda t: once(t,
        "std::optional<int> ChromeMainDelegate::BasicStartupComplete() {\n",
        "std::optional<int> ChromeMainDelegate::BasicStartupComplete() {\n"
        "  // Mast: switches from privacy.json.\n  {\n"
        "    base::CommandLine* mast_command_line = base::CommandLine::ForCurrentProcess();\n"
        + "".join(switch_line(s) for s in PRIVACY["switches"]) + "  }\n"))

    # Profile pref defaults, set once every profile pref is registered (Brave:
    # OverrideDefaultPrefValues in browser/brave_profile_prefs.cc). Each pref name must
    # still be declared where privacy.json says.
    def pref_line(pref):
        decl = pristine(pref["declared_in"])
        assert f'"{pref["pref"]}"' in decl, f'{pref["pref"]} is no longer declared in {pref["declared_in"]}'
        return f'  registry->SetDefaultPrefValue("{pref["pref"]}", base::Value({json.dumps(pref["value"])}));\n'
    privacy_edit("chrome/browser/prefs/browser_prefs.cc", lambda t: once(t,
        "  ash::TokenHandleStoreImpl::RegisterPrefs(registry);\n#endif\n}\n",
        "  ash::TokenHandleStoreImpl::RegisterPrefs(registry);\n#endif\n"
        "  // Mast: privacy.json's pref defaults.\n"
        + "".join(pref_line(pref) for pref in PRIVACY["profile_prefs"]) + "}\n"))

os.makedirs(WORK, exist_ok=True)
json.dump(EDITS, open(os.path.join(WORK, "edits.json"), "w"), indent=1)
failed = [e for e in EDITS if "error" in e]
if failed:
    print(f"{len(failed)} of {len(EDITS)} edits fail")
    sys.exit(1)

for patch in PATCHES:
    out = subprocess.run(["diff", "-ruN", "a", "b"], cwd=os.path.join(WORK, patch + ".d"),
                         capture_output=True, text=True).stdout
    # Drop timestamps from ---/+++ headers so a patch only changes when its content does.
    out = re.sub(r"^(---|\+\+\+) (\S+)\t.*$", r"\1 \2", out, flags=re.M)
    open(os.path.join(WORK, patch), "w").write(out)
    print(f"{patch}: {len(PATCHES[patch])} files, +{sum(1 for l in out.splitlines() if l.startswith('+') and not l.startswith('+++'))} lines")
