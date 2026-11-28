"""Drive Mast through fixed scenes and screenshot the whole (virtual) screen.

CDP navigates pages; xdotool sends real key presses for native UI (omnibox, app menu).
UI DevTools (--enable-ui-devtools=9223) is the inspector for the native UI: each dump
records every view's class, screen bounds and properties (with Background/Border colour
ids) next to the screenshot, and finds native controls to click by their properties.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

OUT = sys.argv[1]
BASE = "http://127.0.0.1:9333"


def get(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method)
    return json.load(urllib.request.urlopen(req, timeout=10))


class Page:
    def __init__(self):
        target = next(t for t in get("/json") if t["type"] == "page")
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], suppress_origin=True)
        self.id = 0

    def send(self, method, **params):
        self.id += 1
        self.ws.send(json.dumps({"id": self.id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.id:
                return msg

    def go(self, url, wait=3.0):
        self.send("Page.navigate", url=url)
        time.sleep(wait)

    def js(self, expression):
        return self.send("Runtime.evaluate", expression=expression, returnByValue=True)


class NativeUI:
    """Chromium's UI DevTools: the views tree as a DOM, properties as CSS (one client)."""

    def __init__(self, port=9223):
        self.ws = websocket.create_connection(f"ws://127.0.0.1:{port}/0", suppress_origin=True,
                                              timeout=20)
        self.id = 0
        self.send("DOM.enable")
        self.send("CSS.enable")

    def send(self, method, **params):
        self.id += 1
        self.ws.send(json.dumps({"id": self.id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.id:
                return msg.get("result", {})

    def tree(self):
        """Every node: id, type, class, name, depth, screen bounds, properties."""
        out = []

        def walk(node, depth):
            a = node.get("attributes", [])
            attrs = dict(zip(a[::2], a[1::2]))
            row = {"id": node["nodeId"], "type": node.get("nodeName"), "class": attrs.get("class"),
                   "name": attrs.get("name"), "depth": depth}
            if node.get("nodeName") in ("View", "Widget", "Window"):
                b = self.send("DOM.getNodeBoundsInScreen", nodeId=node["nodeId"])
                row["bounds"] = b.get("boundsInScreen") or b
                props = {}
                for m in self.send("CSS.getMatchedStylesForNode", nodeId=node["nodeId"]).get(
                        "matchedCSSRules", []):
                    rule = m.get("rule", {})
                    sel = (rule.get("selectorList", {}).get("selectors") or [{}])[0].get("text", "?")
                    props[sel] = {p["name"]: p.get("value") for p in rule.get("style", {}).get(
                        "cssProperties", [])}
                row["props"] = props
            out.append(row)
            for c in node.get("children", []):
                walk(c, depth + 1)

        walk(self.send("DOM.getDocument")["root"], 0)
        return out

    def dump(self, name):
        rows = self.tree()
        with open(f"{OUT}/views-{name}.json", "w") as f:
            json.dump(rows, f, indent=1)
        print("views", name, len(rows), flush=True)
        return rows

    def find(self, rows, cls=None, prop_contains=None):
        for r in rows:
            if cls and r.get("class") != cls:
                continue
            if prop_contains and prop_contains not in json.dumps(r.get("props", {})):
                continue
            if r.get("bounds"):
                return r
        return None

    def close(self):
        self.ws.close()


def native_dump(name, click=None):
    """Dump the views tree; optionally click the first view matching click=(class, text)."""
    try:
        ui = NativeUI()
    except Exception as e:  # never lose the screenshots over the inspector
        print("ui-devtools unavailable:", e, flush=True)
        return None
    try:
        rows = ui.dump(name)
        if click:
            r = ui.find(rows, *click)
            if not r:
                print("not found:", click, flush=True)
                return None
            b = r["bounds"]
            x, y = int(b["x"] + b["width"] / 2), int(b["y"] + b["height"] / 2)
            subprocess.run(["xdotool", "mousemove", str(x), str(y), "click", "1"])
            print("clicked", r.get("class"), x, y, flush=True)
            return r
        return rows
    except Exception as e:
        print("ui-devtools error:", e, flush=True)
        return None
    finally:
        ui.close()


def window():
    for name in ("Mast", "Chromium", "Chrome"):
        r = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", name],
                           capture_output=True, text=True)
        if r.stdout.strip():
            return r.stdout.split()[0]
    return None


def keys(*seq, text=None):
    w = window()
    if w:
        subprocess.run(["xdotool", "windowfocus", "--sync", w])
    for k in seq:
        subprocess.run(["xdotool", "key", "--clearmodifiers", k])
        time.sleep(0.4)
    if text:
        subprocess.run(["xdotool", "type", "--delay", "60", text])


def shot(name, settle=1.5):
    if not capturing:
        return
    time.sleep(settle)
    subprocess.run(["import", "-window", "root", f"{OUT}/{name}.png"], check=True)
    print("shot", name, flush=True)


for _ in range(80):
    try:
        get("/json/version")
        break
    except Exception:
        time.sleep(0.5)
time.sleep(4)

# Areas, in this order. ./mast shots (and unattended) take a list of them, so a build only
# screenshots what it changed: MAST_SHOTS=newtab,customize (default "basics"; "all" for
# every area). Each one sets up what it needs; the net log covers whichever ran.
AREAS = []


def area(name, about):
    def add(fn):
        AREAS.append((name, about, fn))
        return fn
    return add


wallpaper_set = False


def set_wallpaper():
    """A generated picture as Customize's background (not one of the paintings)."""
    global wallpaper_set
    wallpaper_set = True
    p.js("""(() => {
      const c = document.createElement('canvas'); c.width = 1920; c.height = 1080;
      const g = c.getContext('2d'); const l = g.createLinearGradient(0, 0, 1920, 1080);
      l.addColorStop(0, '#1d3b53'); l.addColorStop(.55, '#7a4b3a'); l.addColorStop(1, '#e0a96d');
      g.fillStyle = l; g.fillRect(0, 0, 1920, 1080);
      g.fillStyle = 'rgba(255,255,255,.12)'; for (let i = 0; i < 9; i++) { g.beginPath(); g.arc(200 + i * 210, 760 - i * 40, 90 + i * 12, 0, 7); g.fill(); }
      const u = c.toDataURL('image/jpeg', .86); chrome.send('setWallpaper', [u.slice(u.indexOf(',') + 1)]);
    })()""")
    time.sleep(1.5)


@area("welcome", "First run: the welcome page and its steps (46-48)")
def welcome():
    # First run: the welcome page (design C25) takes the place of the lone new tab. Its
    # first step, then the import list (an empty builder has no other browsers).
    time.sleep(1)
    shot("46-welcome")
    p.js("document.querySelector('[data-go=\"1\"]').click()")
    time.sleep(3)
    shot("47-welcome-import")
    # The same step as it looks on a computer with other browsers (sample data), and the
    # done step after an import.
    p.js("""mastWelcome({sources: [
      {id: 'chrome/Default', browser: 'Google Chrome', profile: 'Miki', bookmarks: 1204, extensions: 14, history: true},
      {id: 'brave/Default', browser: 'Brave', profile: 'Personal', bookmarks: 312, extensions: 3, history: true},
      {id: 'firefox/0', browser: 'Firefox', profile: 'default-release', bookmarks: -1, extensions: 0, history: true, firefox: true}]})""")
    shot("47b-welcome-import-list", settle=0.8)
    p.js("mastWelcome({result: {browser: 'Google Chrome', bookmarks: 1204, onNewTab: 18, history: 20000, extensions: 14, ok: true}})")
    # The search engine step (Onboarding, step 3): Google to begin with; DuckDuckGo picked
    # for the shot, then Google again for the scenes after.
    shot("47c-welcome-search", settle=0.8)
    p.js("document.querySelector('[data-engine=\"92\"]').click()")
    shot("47d-welcome-search-picked", settle=0.8)
    p.js("document.querySelector('[data-engine=\"1\"]').click()")
    time.sleep(0.5)
    p.js("document.querySelector('[data-go=\"3\"]').click()")
    shot("48-welcome-done", settle=0.8)


@area("tabicon", "A new tab's icon while it opens (00 frames)")
def tabicon():
    p.go("chrome://newtab")
    # What a new tab's icon shows while it opens (a Chromium logo was seen for a moment):
    # the tab row, every 60ms for about a second after Ctrl+T.
    keys("ctrl+t")
    for i in range(16):
        subprocess.run(["import", "-window", "root", "-crop", "720x41+0+0", f"{OUT}/00-newtab-icon-{i:02d}.png"])
        time.sleep(0.06)
    print("shot 00-newtab-icon frames", flush=True)
    keys("ctrl+w")


@area("basics", "Empty new tab, a site, typing in the address bar, the ⋮ menu (01-04)")
def basics():
    # The new tab page: an empty desktop first, as a fresh profile sees it.
    shot("01-desktop-empty")
    native_dump("01-desktop-empty")
    p.go("https://en.wikipedia.org/wiki/Ulysses_pact", 5)
    shot("02-site")
    keys("ctrl+l", text="ulysses")
    shot("03-omnibox-typing")
    keys("Escape", "Escape")
    keys("alt+f")
    shot("04-app-menu")
    keys("Escape")


@area("settings", "Settings, About, chrome://version (05-07)")
def settings():
    p.go("chrome://settings")
    shot("05-settings")
    p.go("chrome://settings/help")
    shot("06-about")
    p.go("chrome://version")
    shot("07-version")


@area("newtab", "The new tab page with saved sites, changing a block (08-09)")
def newtab():
    # The desktop with sites (Wikipedia has a favicon from the visit above; the rest fall back
    # to a monogram until visited).
    p.go("chrome://newtab")
    for url, title in [("https://en.wikipedia.org/", "Wikipedia"), ("https://github.com/", "GitHub"),
                       ("https://svelte.dev/docs", "Svelte"), ("https://developer.mozilla.org/", "MDN"),
                       ("https://www.vitsoe.com/", "Vitsœ"), ("https://rsms.me/inter/", "Inter")]:
        p.js(f"chrome.send('desktopAdd', [{json.dumps(url)}, {json.dumps(title)}])")
        time.sleep(0.3)
    p.go("chrome://newtab")
    shot("08-desktop")
    p.js("document.getElementById('edit').click()")
    shot("09-distractions-change")


@area("blocklist", "The blocklist page (10) and its panel on the new tab: own category, News, Shortcuts (17)")
def blocklist():
    # The full blocklist (chrome://mast, "Edit blocklist").
    p.go("chrome://mast")
    p.js("document.querySelector('[data-toggle=shopping]').click()")
    shot("10-blocklist")
    # "Edit blocklist" opens the list in a panel on the new tab page.
    p.go("chrome://newtab")
    p.js("document.getElementById('editList').click()")
    shot("17-blocklist-panel")
    # A category of your own (design C33): name it, add a site; then a built-in one.
    def field(k, text):
        p.js(f"""(() => {{ const i = document.querySelector('.bl [data-k={k}]'); i.focus(); i.value = {json.dumps(text)};
          i.dispatchEvent(new Event('input', {{bubbles: true}}));
          i.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}})); }})()""")
        time.sleep(0.5)
    p.js("document.querySelector('.bl [data-act=newcat]').click()")
    field("newcat", "Late night")
    field("add", "kottke.org")
    shot("17c-own-category")
    p.js("document.querySelector('.bl [data-view=\"\"]').click()")
    p.js("document.querySelector('.bl [data-view=news]').click()")
    field("add", "semafor.com")
    shot("17d-category-news")
    p.js("document.querySelector('.bl [data-view=\"\"]').click()")
    # Its Shortcuts tab (design C32): add Alt + N for a search, then try Alt + F (taken).
    p.js("document.querySelector('.bl [data-tab=sc]').click()")
    p.js("""(() => { const u = document.querySelector('.bl [data-scurl]'); u.value = 'google.com/search?q=nvidia+stock';
      u.dispatchEvent(new Event('input', {bubbles: true}));
      document.querySelector('.bl [data-act=sckey]').click();
      document.querySelector('.bl [data-act=sckey]').dispatchEvent(new KeyboardEvent('keydown',
        {key: 'n', code: 'KeyN', altKey: true, bubbles: true, cancelable: true}));
      document.querySelector('.bl [data-act=scadd]').click(); })()""")
    time.sleep(0.5)
    p.js("""(() => { document.querySelector('.bl [data-act=sckey]').click();
      document.querySelector('.bl [data-act=sckey]').dispatchEvent(new KeyboardEvent('keydown',
        {key: 'f', code: 'KeyF', altKey: true, bubbles: true, cancelable: true})); })()""")
    shot("17b-shortcuts")
    p.js("document.querySelector('.bl [data-tab=bl]').click()")
    p.js("document.getElementById('blClose').click()")



@area("youtube", "YouTube's modes: Shorts blocked, music only (40-45, 50-53)")
def youtube():
    # YouTube, music only (design C24), before "Block distractions" blocks all of YouTube: the
    # choice on YouTube's row, then YouTube with it on. A search keeps mixes and songs; a Short
    # and a video that isn't music get the blocked page; a song plays.
    # The blocklist is a picked set (design C28a): un-pick every category, then YouTube on
    # its own as Music only, for an hour. (Re-picked after the YouTube scenes.)
    def pick_all(on):
        for _ in range(30):
            sel = '[data-cat][aria-pressed="true"],[data-cat][aria-pressed="mixed"]' if not on else '[data-cat][aria-pressed="false"],[data-cat][aria-pressed="mixed"]'
            r = p.js(f"(() => {{ const b = document.querySelector('.cats {sel}'.replace('.cats ', '') ); if (!b) return false; b.click(); return true; }})()")
            if not r.get("result", {}).get("result", {}).get("value"):
                break
            time.sleep(0.3)
    pick_all(False)
    # Block Shorts first (design C32): YouTube stays, its Shorts go.
    p.js("document.querySelector('[data-yt=shorts]').click()")
    time.sleep(0.3)
    p.js("document.querySelector('[data-dur=\"1h\"]').click()")
    time.sleep(0.3)
    shot("50-yt-shorts-choice")
    p.js("document.querySelector('[data-act=block]').click()")
    time.sleep(1)
    # The builder is in the EU: answer YouTube's cookie question first, as a person would.
    p.go("https://www.youtube.com/", 6)
    p.js("[...document.querySelectorAll('button')].find((b) => /Reject all|Hylkää kaikki/.test(b.textContent))?.click()")
    time.sleep(3)
    p.go("https://www.youtube.com/", 6)
    shot("51-yt-shorts-home", settle=2)
    p.go("https://www.youtube.com/results?search_query=lofi+hip+hop", 6)
    shot("52-yt-shorts-search", settle=2)
    p.go("https://www.youtube.com/shorts/aqz-KE-bpKQ", 4)
    shot("53-yt-shorts-blocked")
    # Then Music only on top of it.
    p.go("chrome://mast")
    p.js("document.querySelector('[data-yt=music]').click()")
    time.sleep(0.3)
    p.js("document.querySelector('[data-dur=\"1h\"]').click()")
    time.sleep(0.3)
    shot("40-yt-choice")
    p.js("document.querySelector('[data-act=block]').click()")
    time.sleep(1)
    p.go("https://www.youtube.com/results?search_query=lofi+hip+hop", 6)
    shot("41-yt-music-search", settle=2)
    p.go("https://www.youtube.com/shorts/aqz-KE-bpKQ", 4)
    shot("42-yt-short-blocked")
    p.go("https://www.youtube.com/watch?v=UYhKDweME3A", 8)
    shot("43-yt-not-music", settle=2)
    print("43 ended at", p.js("location.href"), flush=True)
    # A song opened from the search (remembered as music), then one opened directly.
    p.go("https://www.youtube.com/results?search_query=indila+derniere+danse", 6)
    p.js("document.querySelector('[data-mast=music] a[href*=\"/watch\"]')?.click()")
    time.sleep(8)
    shot("44-yt-song", settle=2)
    print("44 ended at", p.js("location.href"), flush=True)
    p.go("https://www.youtube.com/watch?v=K5KAc5CoCuk", 10)
    shot("45-yt-song-direct", settle=2)
    print("45 ended at", p.js("location.href"), flush=True)
    # Back to every category picked, YouTube blocked whole, for the scenes below.
    p.go("chrome://mast")
    pick_all(True)
    p.js("document.querySelector('[data-yt=site]')?.click()")
    time.sleep(0.5)


@area("blocking", "Blocked pages: timed, forever, the always-on feeds (11-13, 30-31)")
def blocking():
    # Block distractions for today, and one site forever.
    p.go("chrome://newtab")
    p.js("chrome.send('blockDistractions', ['today'])")
    time.sleep(0.5)
    p.js("chrome.send('block', [['x.com', 'twitter.com'], 'forever'])")
    p.go("chrome://newtab")
    shot("11-desktop-blocked")
    p.go("https://www.youtube.com/", 4)
    shot("12-blocked-timed")
    p.go("https://x.com/", 4)
    shot("13-blocked-forever")
    # The always-on lists (mast_feeds.cc): a betting site only the gambling feed lists, and an
    # adult site. Both always blocked, quietly (no end time).
    p.go("https://www.pokerstars.net/", 4)
    shot("30-gambling-feed")
    p.go("https://onlyfans.com/", 4)
    shot("31-quiet-block")


@area("pageinfo", "The page info panel (14)")
def pageinfo():
    # Page info: Chromium's own panel ("Block this site" was removed from it).
    p.go("https://en.wikipedia.org/wiki/Dieter_Rams", 5)
    native_dump("14-site", click=("LocationIconView", None))
    shot("14-page-info")
    keys("Escape")


@area("customize", "Customize, a palette, a picture, mast://, Settings in the palette, a light picture (18-24)")
def customize():
    # Customize: the panel, then a palette (the whole window should follow), then an image.
    p.go("chrome://newtab")
    p.js("document.getElementById('customize').click()")
    shot("18-customize")
    p.js("chrome.send('setPalette', ['ink'])")
    shot("19-palette-ink", settle=2)
    native_dump("19-palette-ink")
    set_wallpaper()
    p.go("chrome://newtab")
    shot("20-wallpaper")
    p.go("https://en.wikipedia.org/wiki/Dieter_Rams", 5)
    shot("21-site-ink")
    # mast:// works in the address bar and chrome:// pages show as mast://.
    keys("ctrl+l", text="mast://version")
    keys("Return")
    shot("22-mast-scheme", settle=2.5)
    # Settings follows the palette too (its page and cards).
    p.go("chrome://settings/appearance", 4)
    shot("23-settings-ink")
    # A light picture: dark text and icons, light pills and tiles.
    p.go("chrome://newtab")
    p.js("chrome.send('setWallpaperTone', ['light'])")
    time.sleep(1)
    p.go("chrome://newtab")
    shot("24-wallpaper-light", settle=2)


@area("tabbar", "Tab bar on websites: Theme and Match site (33-39)")
def tabbar():
    # Tab bar on websites (design C23): the Customize section, then each choice on websites.
    # "Theme": the palette on a site, the picture stays on the new tab. "Match site": a light
    # site (light row, dark text, a light address field and suggestions), a dark one, and one
    # more (not Wikipedia: scene 15 may have blocked it); the colour comes from the top of the page a moment after it paints.
    if not wallpaper_set:
        set_wallpaper()  # "Your picture" needs one (customize sets it when it runs)
    p.js("chrome.send('setWallpaperTone', ['dark'])")
    time.sleep(1)
    p.go("chrome://newtab")
    p.js("document.getElementById('customize').click()")
    shot("33-customize-tab-bar")
    p.js("chrome.send('setTabBar', ['theme'])")
    time.sleep(0.5)
    p.go("https://example.com/", 4)
    shot("34-theme-site", settle=1.5)
    p.go("chrome://newtab")
    p.js("chrome.send('setTabBar', ['site'])")
    time.sleep(0.5)
    p.go("https://example.com/", 4)
    shot("35-site-example", settle=3)
    native_dump("35-site-example")
    keys("ctrl+l", text="example")
    shot("36-site-omnibox", settle=1.5)
    keys("Escape", "Escape")
    p.go("https://github.com/", 6)
    shot("37-site-github", settle=3)
    p.go("https://developer.mozilla.org/en-US/", 6)
    shot("38-site-mdn", settle=3)
    p.go("https://www.python.org/", 6)
    shot("39-site-python", settle=3)
    p.go("chrome://newtab")
    p.js("chrome.send('setTabBar', ['picture'])")
    time.sleep(0.5)


@area("star", "The star: saving a site (25-26)")
def star():
    # The star: Ctrl+D on a site saves it to the new tab (no folder picker), and a starred
    # page shows a filled star.
    p.go("https://www.vitsoe.com/", 5)
    keys("ctrl+d")
    shot("25-star-bubble", settle=1.5)
    keys("Escape")
    shot("26-starred", settle=1)


@area("extensions", "The extensions page and the Web Store (27-28)")
def extensions():
    # The Web Store: the extensions page's link, and a listing that should offer to install.
    p.go("chrome://extensions", 3)
    shot("27-extensions")
    p.go("https://chromewebstore.google.com/detail/ddkjiahejlhfcafbddmgiahcphecmpfh", 7)
    shot("28-webstore-item", settle=2)


@area("tabgroups", "Tab groups (29)")
def tabgroups():
    # Tab groups (design C22 A): the first tab grouped from its context menu and named "hi";
    # its header pill and lane, next to an ungrouped tab.
    p.go("chrome://newtab")
    keys("ctrl+t")
    time.sleep(1)
    subprocess.run(["xdotool", "mousemove", "120", "20", "click", "3"])
    time.sleep(1)
    keys("Down", "Down", "Down", "Return")  # 156: new tab to the right, split view, then "Add tab to new group"
    time.sleep(1.5)
    keys(text="hi")
    keys("Return")
    shot("29-tab-group", settle=1.5)


@area("widevine", "Widevine on request: the question, then the download (54-55)")
def widevine():
    # Back to the default look first: Onyx, no picture.
    p.go("chrome://newtab")
    p.js("chrome.send('setPalette', ['onyx']); chrome.send('clearWallpaper')")
    time.sleep(1)
    # Widevine on request (mast_widevine.h): a page that uses protected media (Shaka Player's demo
    # checks Widevine as it loads) asks; Download fetches it from Google, then the tab offers a
    # restart. Not restarted here: the session ends with these scenes.
    p.go("https://shaka-player-demo.appspot.com/demo/", 8)
    keys("ctrl+1")  # the scene's page is the first tab; the tab-group scene left another one active
    time.sleep(1)
    shot("54-widevine-ask", settle=1)
    native_dump("54-widevine-ask", click=("MdTextButton", "Download"))
    time.sleep(25)
    shot("55-widevine-ready", settle=1)
    hint = "/tmp/mast-shot-profile/WidevineCdm/latest-component-updated-widevine-cdm"
    print("widevine hint:", open(hint).read() if os.path.exists(hint) else "missing", flush=True)
    print("widevine dir:", sorted(os.listdir("/tmp/mast-shot-profile/WidevineCdm"))
          if os.path.isdir("/tmp/mast-shot-profile/WidevineCdm") else "missing", flush=True)


capturing = True
wanted = os.environ.get("MAST_SHOTS", "basics").strip() or "basics"
names = [name for name, _, _ in AREAS]
picked = names if wanted == "all" else [w.strip() for w in wanted.split(",") if w.strip()]
unknown = [w for w in picked if w not in names]
if unknown:
    print(f"unknown screenshot area(s): {', '.join(unknown)}; areas: {', '.join(names)}", flush=True)
    sys.exit(2)
print("areas:", ", ".join(n for n in names if n in picked), flush=True)
p = Page()
for name, about, fn in AREAS:
    # A fresh profile always opens on the welcome page: go through it either way, so every
    # area starts from a set-up browser; it's only photographed when picked.
    if name not in picked and name != "welcome":
        continue
    capturing = name in picked
    print(f"== {name}: {about}", flush=True)
    fn()
