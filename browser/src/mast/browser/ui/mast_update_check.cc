// Copyright 2026 The Mast Authors

#include "mast/browser/ui/mast_update_check.h"

#include <algorithm>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "base/base_paths.h"
#include "base/files/file_path.h"
#include "base/files/file_util.h"
#include "base/functional/bind.h"
#include "base/json/json_reader.h"
#include "base/no_destructor.h"
#include "base/path_service.h"
#include "base/strings/string_number_conversions.h"
#include "base/strings/string_split.h"
#include "base/strings/string_util.h"
#include "base/strings/utf_string_conversions.h"
#include "base/task/thread_pool.h"
#include "base/time/time.h"
#include "base/timer/timer.h"
#include "base/values.h"
#include "base/version.h"
#include "chrome/browser/browser_process.h"
#include "chrome/browser/infobars/confirm_infobar_creator.h"
#include "chrome/browser/ui/browser_window/public/browser_window_interface.h"
#include "chrome/browser/ui/browser_window/public/browser_window_interface_iterator.h"
#include "components/infobars/content/content_infobar_manager.h"
#include "components/infobars/core/confirm_infobar_delegate.h"
#include "components/infobars/core/infobar.h"
#include "components/prefs/pref_service.h"
#include "components/tabs/public/tab_interface.h"
#include "content/public/browser/web_contents.h"
#include "mast/browser/mast_prefs.h"
#include "mast/common/mast_version.h"
#include "net/base/load_flags.h"
#include "net/traffic_annotation/network_traffic_annotation.h"
#include "services/network/public/cpp/resource_request.h"
#include "services/network/public/cpp/shared_url_loader_factory.h"
#include "services/network/public/cpp/simple_url_loader.h"
#include "services/network/public/mojom/url_response_head.mojom.h"
#include "ui/base/clipboard/scoped_clipboard_writer.h"
#include "url/gurl.h"

namespace mast {

namespace {

// Where releases are published (packaging/publish.sh, mast.env).
constexpr char kLatestUrl[] =
    "https://github.com/mast-browser/mast/releases/latest/download/latest.json";
constexpr char16_t kUpdateCommand[] =
    u"curl -fsSL https://github.com/mast-browser/mast/releases/latest/download/"
    u"install.sh | sh";
constexpr base::TimeDelta kEvery = base::Days(1);
constexpr base::TimeDelta kFirstDelay = base::Minutes(1);

constexpr net::NetworkTrafficAnnotationTag kAnnotation =
    net::DefineNetworkTrafficAnnotation("mast_update_check", R"(
      semantics {
        sender: "Mast update check"
        description:
          "Once a day, a Mast installed from its tarball (not a .deb or .rpm, "
          "which the system updates) fetches the version of the newest Mast "
          "release, to say when there's an update."
        trigger: "A day after the last check, while Mast is running."
        data: "None: a plain GET, without cookies."
        destination: OTHER
        destination_other: "Mast's GitHub releases."
      }
      policy {
        cookies_allowed: NO
        setting: "Installing Mast from its .deb or .rpm skips this check."
        policy_exception_justification: "Not implemented."
      })");

// "157.0.8103.78-202611181200": Chromium's version, then the build's time.
bool Newer(const std::string& candidate, const std::string& installed) {
  const auto split = [](const std::string& v) {
    const std::vector<std::string> parts = base::SplitString(
        v, "-", base::TRIM_WHITESPACE, base::SPLIT_WANT_NONEMPTY);
    return std::make_pair(base::Version(parts.empty() ? "" : parts[0]),
                          parts.size() > 1 ? parts[1] : std::string());
  };
  const auto [a, a_build] = split(candidate);
  const auto [b, b_build] = split(installed);
  if (!a.IsValid() || !b.IsValid()) {
    return false;
  }
  const int c = a.CompareTo(b);
  return c > 0 || (c == 0 && a_build > b_build);  // same-length digits
}

class Bar : public ConfirmInfoBarDelegate {
 public:
  explicit Bar(std::string version) : version_(std::move(version)) {}

  // ConfirmInfoBarDelegate:
  infobars::InfoBarDelegate::InfoBarIdentifier GetIdentifier() const override {
    return MAST_UPDATE_INFOBAR_DELEGATE;
  }
  std::u16string GetMessageText() const override {
    return u"Mast " + base::UTF8ToUTF16(version_) +
           u" is out. To update, run the installer again in a terminal.";
  }
  int GetButtons() const override { return BUTTON_OK; }
  std::u16string GetButtonLabel(InfoBarButton button) const override {
    return u"Copy the command";
  }
  bool Accept() override {
    ui::ScopedClipboardWriter(ui::ClipboardBuffer::kCopyPaste)
        .WriteText(kUpdateCommand);
    return true;
  }
  bool EqualsDelegate(infobars::InfoBarDelegate* delegate) const override {
    return delegate->GetIdentifier() == GetIdentifier();
  }

 private:
  const std::string version_;
};

class Checker {
 public:
  Checker() = default;

  void Begin() {
    // mast-version, beside the browser (every package has it; a development
    // build doesn't). Settings > About shows it whatever the install
    // (mast/common/mast_version.h); the check below only runs where the system
    // doesn't update Mast.
    base::ThreadPool::PostTaskAndReplyWithResult(
        FROM_HERE, {base::MayBlock(), base::TaskPriority::USER_VISIBLE},
        base::BindOnce([]() -> Installed {
          Installed installed;
          base::FilePath dir;
          if (!base::PathService::Get(base::DIR_EXE, &dir)) {
            return installed;
          }
          std::string version;
          if (!base::ReadFileToString(dir.AppendASCII("mast-version"),
                                      &version)) {
            return installed;  // a development build
          }
          installed.version =
              std::string(base::TrimWhitespaceASCII(version, base::TRIM_ALL));
          // A .deb or .rpm under /opt: the system updates it where Mast's
          // package source works (apt, dnf). zypper can't read it (it won't
          // fetch packages from another host than its metadata), so openSUSE
          // gets this check too.
          const auto exists = [](const char* path) {
            return base::PathExists(base::FilePath(path));
          };
          const bool system_updates =
              exists("/etc/apt/sources.list.d/mast.sources") ||
              (exists("/etc/yum.repos.d/mast.repo") &&
               (exists("/usr/bin/dnf") || exists("/usr/bin/dnf5")));
          // NixOS (nix/package.nix): the store is read-only and Nix updates it
          // (nix flake update, then nixos-rebuild switch).
          const bool nix = base::StartsWith(dir.value(), "/nix/store/");
          installed.check = !nix && !((base::StartsWith(dir.value(), "/opt/") ||
                                       base::StartsWith(dir.value(), "/usr/")) &&
                                      system_updates);
          return installed;
        }),
        base::BindOnce(&Checker::OnInstalled, base::Unretained(this)));
  }

 private:
  struct Installed {
    std::string version;  // empty: a development build
    bool check = false;   // the system doesn't update this install
  };

  void OnInstalled(Installed installed) {
    SetInstalledVersion(installed.version);
    if (installed.version.empty() || !installed.check) {
      return;
    }
    installed_ = installed.version;
    // A day after the last check (Local State), at least a minute from now.
    base::TimeDelta wait = kFirstDelay;
    if (PrefService* p = g_browser_process->local_state()) {
      const base::Time last = p->GetTime(kUpdateCheckedPref);
      wait = std::max(wait, last + kEvery - base::Time::Now());
    }
    timer_.Start(FROM_HERE, wait,
                 base::BindOnce(&Checker::Fetch, base::Unretained(this)));
  }

  void Fetch() {
    auto request = std::make_unique<network::ResourceRequest>();
    request->url = GURL(kLatestUrl);
    request->credentials_mode = network::mojom::CredentialsMode::kOmit;
    request->load_flags = net::LOAD_BYPASS_CACHE | net::LOAD_DISABLE_CACHE;
    loader_ = network::SimpleURLLoader::Create(std::move(request), kAnnotation);
    loader_->DownloadToString(
        g_browser_process->shared_url_loader_factory().get(),
        base::BindOnce(&Checker::OnLatest, base::Unretained(this)),
        /*max_body_size=*/16 * 1024);
  }

  void OnLatest(std::optional<std::string> body) {
    loader_.reset();
    if (PrefService* p = g_browser_process->local_state()) {
      p->SetTime(kUpdateCheckedPref, base::Time::Now());
    }
    timer_.Start(FROM_HERE, kEvery,
                 base::BindOnce(&Checker::Fetch, base::Unretained(this)));
    if (!body) {
      return;  // offline: tomorrow
    }
    std::optional<base::Value> json =
        base::JSONReader::Read(*body, base::JSON_PARSE_RFC);
    const std::string* latest =
        json && json->is_dict() ? json->GetDict().FindString("version")
                                : nullptr;
    if (!latest || !Newer(*latest, installed_)) {
      return;
    }
    BrowserWindowInterface* browser =
        GetLastActiveBrowserWindowInterfaceWithAnyProfile();
    tabs::TabInterface* tab =
        browser ? browser->GetActiveTabInterface() : nullptr;
    auto* manager = tab ? infobars::ContentInfoBarManager::FromWebContents(
                              tab->GetContents())
                        : nullptr;
    if (manager) {
      manager->AddInfoBar(CreateConfirmInfoBar(std::make_unique<Bar>(*latest)),
                          /*replace_existing=*/true);
    }
  }

  std::string installed_;
  base::OneShotTimer timer_;
  std::unique_ptr<network::SimpleURLLoader> loader_;
};

}  // namespace

// static
void MastUpdateCheck::Start() {
  static base::NoDestructor<Checker> checker;
  static bool started = false;
  if (!started) {
    started = true;
    checker->Begin();
  }
}

}  // namespace mast
