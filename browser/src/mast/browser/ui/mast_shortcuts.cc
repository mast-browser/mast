// Copyright 2026 The Mast Authors

#include "mast/browser/ui/mast_shortcuts.h"

#include <memory>
#include <string>

#include "base/functional/bind.h"
#include "base/no_destructor.h"
#include "base/values.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/ui/browser_window/public/browser_window_interface.h"
#include "chrome/browser/ui/views/frame/browser_view.h"
#include "components/prefs/pref_service.h"
#include "mast/browser/mast_prefs.h"
#include "ui/base/accelerators/accelerator_manager.h"
#include "ui/base/window_open_disposition.h"
#include "ui/events/event_constants.h"
#include "ui/events/keycodes/keyboard_codes.h"
#include "ui/views/focus/focus_manager.h"

namespace mast {

namespace {

std::map<BrowserWindowInterface*, std::unique_ptr<MastShortcuts>>& All() {
  static base::NoDestructor<
      std::map<BrowserWindowInterface*, std::unique_ptr<MastShortcuts>>>
      all;
  return *all;
}

}  // namespace

// static
void MastShortcuts::Attach(BrowserWindowInterface* browser) {
  if (!browser || browser->GetType() != BrowserWindowInterface::TYPE_NORMAL ||
      !browser->GetProfile() || All().contains(browser)) {
    return;
  }
  BrowserView* browser_view = BrowserView::GetBrowserViewForBrowser(browser);
  if (!browser_view || !browser_view->GetWidget()) {
    return;
  }
  All().emplace(browser, std::make_unique<MastShortcuts>(
                             browser, browser_view->GetWidget()));
}

MastShortcuts::MastShortcuts(BrowserWindowInterface* browser,
                             views::Widget* widget)
    : browser_(browser), widget_(widget) {
  observation_.Observe(widget);
  prefs_.Init(browser_->GetProfile()->GetPrefs());
  prefs_.Add(kShortcutsPref, base::BindRepeating(&MastShortcuts::Register,
                                                 base::Unretained(this)));
  did_close_ = browser_->RegisterBrowserDidClose(
      base::BindRepeating([](BrowserWindowInterface* browser) {
        All().erase(browser);
      }));
  Register();
}

MastShortcuts::~MastShortcuts() {
  Unregister();
}

void MastShortcuts::Register() {
  Unregister();
  views::FocusManager* focus = widget_ ? widget_->GetFocusManager() : nullptr;
  if (!focus) {
    return;
  }
  for (const base::Value& item :
       browser_->GetProfile()->GetPrefs()->GetList(kShortcutsPref)) {
    if (!item.is_dict()) {
      continue;
    }
    const std::string* key = item.GetDict().FindString("key");
    const std::string* url = item.GetDict().FindString("url");
    if (!key || !url || !IsShortcutKey(*key)) {
      continue;
    }
    const GURL gurl(*url);
    if (!gurl.is_valid() || !gurl.SchemeIsHTTPOrHTTPS()) {
      continue;
    }
    const ui::Accelerator accelerator(
        static_cast<ui::KeyboardCode>(ui::VKEY_A + ((*key)[0] - 'A')),
        ui::EF_ALT_DOWN);
    if (urls_.contains(accelerator)) {
      continue;
    }
    urls_.emplace(accelerator, gurl);
    focus->RegisterAccelerator(accelerator,
                               ui::AcceleratorManager::kNormalPriority, this);
  }
}

void MastShortcuts::Unregister() {
  if (widget_) {
    if (views::FocusManager* focus = widget_->GetFocusManager()) {
      focus->UnregisterAccelerators(this);
    }
  }
  urls_.clear();
}

bool MastShortcuts::AcceleratorPressed(const ui::Accelerator& accelerator) {
  const auto it = urls_.find(accelerator);
  if (it == urls_.end()) {
    return false;
  }
  browser_->OpenGURL(it->second, WindowOpenDisposition::kNewForegroundTab);
  return true;
}

bool MastShortcuts::CanHandleAccelerators() const {
  return true;
}

void MastShortcuts::OnWidgetDestroying(views::Widget* widget) {
  Unregister();
  observation_.Reset();
  widget_ = nullptr;
}

}  // namespace mast
