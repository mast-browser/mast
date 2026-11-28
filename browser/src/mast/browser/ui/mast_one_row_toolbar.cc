// Copyright 2026 The Mast Authors

#include "mast/browser/ui/mast_one_row_toolbar.h"

#include <optional>

#include "base/functional/bind.h"
#include "base/memory/weak_ptr.h"
#include "base/task/sequenced_task_runner.h"

#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/ui/browser_window/public/browser_window_interface.h"
#include "chrome/browser/ui/tabs/tab_strip_model.h"
#include "chrome/browser/ui/views/frame/browser_view.h"
#include "chrome/browser/ui/views/frame/toolbar_button_provider.h"
#include "chrome/browser/ui/views/location_bar/location_bar_view.h"
#include "chrome/browser/ui/views/toolbar/reload_button.h"
#include "chrome/browser/ui/views/toolbar/toolbar_button.h"
#include "chrome/browser/ui/views/toolbar/toolbar_view.h"
#include "components/bookmarks/common/bookmark_bar_visibility_state.h"
#include "components/bookmarks/common/bookmark_pref_names.h"
#include "components/prefs/pref_service.h"
#include "content/public/browser/web_contents.h"
#include "ui/base/window_open_disposition.h"
#include "url/gurl.h"
#include "mast/browser/mast_prefs.h"
#include "mast/browser/mast_theme.h"
#include "mast/browser/mast_wallpapers.h"
#include "mast/browser/ui/mast_shortcuts.h"
#include "mast/browser/ui/mast_tab_bar.h"
#include "mast/browser/ui/mast_update_check.h"
#include "mast/browser/ui/mast_widevine.h"
#include "mast/common/mast_features.h"
#include "ui/gfx/geometry/size.h"
#include "ui/views/layout/flex_layout_types.h"
#include "ui/views/view_class_properties.h"

namespace mast {

namespace {

// Mast has no bookmarks bar (saved sites live on the new tab page, and its
// toggle is gone from the menu and Ctrl+Shift+B). Profiles that showed it keep
// it hidden.
void HideBookmarksBar(PrefService* prefs) {
  if (prefs->FindPreference(bookmarks::prefs::kShowBookmarkBar)) {
    prefs->SetBoolean(bookmarks::prefs::kShowBookmarkBar, false);
  }
  if (prefs->FindPreference(bookmarks::prefs::kBookmarkBarVisibilityState)) {
    prefs->SetInteger(
        bookmarks::prefs::kBookmarkBarVisibilityState,
        static_cast<int>(bookmarks::BookmarkBarVisibilityState::kAlwaysHide));
  }
}

// Opens the welcome page: a lone new tab page becomes it; otherwise it opens
// in a new tab.
void OpenWelcome(base::WeakPtr<BrowserWindowInterface> weak) {
  BrowserWindowInterface* browser = weak.get();
  if (!browser) {
    return;
  }
  TabStripModel* tabs = browser->GetTabStripModel();
  content::WebContents* only =
      tabs && tabs->count() == 1 ? tabs->GetActiveWebContents() : nullptr;
  const bool lone_new_tab = only && only->GetVisibleURL().SchemeIs("chrome") &&
                            only->GetVisibleURL().host() == "newtab";
  browser->OpenGURL(GURL("chrome://mast/welcome"),
                    lone_new_tab ? WindowOpenDisposition::kCurrentTab
                                 : WindowOpenDisposition::kNewForegroundTab);
}

// The welcome page (designs C25, C26), once per profile, in its first normal
// window, after the window's startup tabs are in. A profile that never had a
// picture starts on the default built-in one (mast_wallpapers.h), so the
// welcome page and the new tab page open on it.
void MaybeWelcome(BrowserWindowInterface* browser) {
  Profile* profile = browser->GetProfile();
  PrefService* prefs = profile->GetPrefs();
  if (browser->GetType() != BrowserWindowInterface::TYPE_NORMAL ||
      profile->IsOffTheRecord() || prefs->GetBoolean(kWelcomedPref)) {
    return;
  }
  prefs->SetBoolean(kWelcomedPref, true);
  if (prefs->GetInteger(kWallpaperPref) == 0) {
    UseWallpaperPreset(profile, kDefaultWallpaperPreset,
                       base::BindOnce(
                           [](base::WeakPtr<BrowserWindowInterface> weak, bool) {
                             OpenWelcome(weak);
                           },
                           browser->GetWeakPtr()));
    return;
  }
  base::SequencedTaskRunner::GetCurrentDefault()->PostTask(
      FROM_HERE, base::BindOnce(&OpenWelcome, browser->GetWeakPtr()));
}

}  // namespace

void OnBrowserWindowReady(BrowserWindowInterface* browser) {
  // Customize's palette and backdrop, before the window first paints. Later
  // picks apply from the new tab page (mast_ui.cc).
  if (browser && browser->GetProfile()) {
    ApplyTheme(browser->GetProfile()->GetPrefs());
    HideBookmarksBar(browser->GetProfile()->GetPrefs());
    MaybeWelcome(browser);
  }
  // The user's Alt + letter shortcuts, in every normal window.
  MastShortcuts::Attach(browser);
  // Widevine on request (Spotify, Netflix), and the tarball's update check:
  // once for the whole browser.
  MastWidevine::Start();
  MastUpdateCheck::Start();
  if (!IsOneRowWindow() || !browser ||
      browser->GetType() != BrowserWindowInterface::TYPE_NORMAL) {
    return;
  }
  // Customize's "Tab bar on websites" for this window.
  MastTabBar::Attach(browser);
  BrowserView* browser_view = BrowserView::GetBrowserViewForBrowser(browser);
  ToolbarView* toolbar = browser_view ? browser_view->toolbar() : nullptr;
  LocationBarView* location_bar =
      toolbar ? toolbar->location_bar_view() : nullptr;
  if (!location_bar) {
    return;
  }

  // The address bar has a fixed width instead of filling the row: the tabs
  // take the room (the layout puts the toolbar at the end of the tab row).
  location_bar->SetPreferredSize(
      gfx::Size(kOneRowLocationBarWidth,
                location_bar->GetPreferredSize().height()));
  location_bar->SetProperty(
      views::kFlexBehaviorKey,
      views::FlexSpecification(views::MinimumFlexSizeRule::kPreferred,
                               views::MaximumFlexSizeRule::kPreferred));

  // back, address, forward (the design's ← [ address ☆ ] →), then the rest
  // (extensions, downloads, the menu). Forward starts before the address bar,
  // so taking it out moves the bar one place left: its new place is the bar's
  // old index. (Bar + 1 put it after the extensions.)
  if (views::View* forward = toolbar->forward_button()) {
    const std::optional<size_t> at = toolbar->GetIndexOf(location_bar);
    const std::optional<size_t> from = toolbar->GetIndexOf(forward);
    if (at && from) {
      toolbar->ReorderChildView(forward, *from < *at ? *at : *at + 1);
    }
  }
  // No reload button in the one row; Ctrl+R and F5 still reload.
  if (views::View* reload = toolbar->reload_button()) {
    reload->SetVisible(false);
  }
  toolbar->InvalidateLayout();
  browser_view->InvalidateLayout();
}

}  // namespace mast
