"""Summarise where Mast talked to, from a net log, and flag anything that isn't a page we opened.

  netcheck.py <netlog.json> [chrome.log]

With <netlog dir>/audit-end (written by the `audit` screenshot area: when its idle phase
ended, in ms since the epoch), it first runs the network audit: every URL request made
before then must match remote/audit-allow.txt (docs/BASE-PLAN.md).
"""
import collections
import json
import os
import re
import sys


def load_netlog(path):
    """The net log's constants and events. Chromium writes the constants first, then one
    event per line, so a log cut short (Chromium killed mid-write) still reads: every
    complete line is an event."""
    constants, events = {}, []
    with open(path, errors="replace") as f:
        head = []
        for line in f:
            if line.lstrip().startswith('"events"'):
                break
            head.append(line)
        text = "".join(head).strip().rstrip(",")
        try:
            constants = json.loads(text + "}").get("constants", {})
        except ValueError:
            pass
        for line in f:
            line = line.strip().rstrip(",")
            if line.startswith("{") and line.endswith("}"):
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
    return {"constants": constants, "events": events} if constants else None


def audit(netlog_path):
    end_file = os.path.join(os.path.dirname(netlog_path), "audit-end")
    if not os.path.exists(end_file):
        return
    end = int(open(end_file).read().strip())
    allow_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit-allow.txt")
    allow = [l.split("#")[0].strip() for l in open(allow_file)]
    allow = [a for a in allow if a]
    log = load_netlog(netlog_path)
    if not log:
        print("AUDIT: could not read the net log\n")
        return
    consts = log.get("constants", {})
    offset = int(consts.get("timeTickOffset", 0))
    start_job = consts.get("logEventTypes", {}).get("URL_REQUEST_START_JOB")
    seen = collections.Counter()
    for ev in log.get("events", []):
        if ev.get("type") != start_job:
            continue
        url = (ev.get("params") or {}).get("url", "")
        if not url or int(ev.get("time", 0)) + offset > end:
            continue
        if url.split(":", 1)[0] in ("chrome", "chrome-extension", "mast", "data", "blob", "file", "devtools"):
            continue
        seen[url.split("?")[0]] += 1
    bad = {u: n for u, n in seen.items() if not any(u.startswith(a) for a in allow)}
    print(f"AUDIT: {'PASS' if not bad else 'FAIL'}: {sum(seen.values())} request(s) from start-up to the end "
          f"of the idle phase, {len(bad)} not on remote/audit-allow.txt")
    for u, n in sorted(bad.items(), key=lambda x: -x[1]):
        print(f"  !! {n:4d}  {u}")
    for u, n in sorted(seen.items(), key=lambda x: -x[1]):
        if u not in bad:
            print(f"     {n:4d}  {u}")
    print()


audit(sys.argv[1])

# Pages the screenshot scenes open on purpose (and what they load).
OPENED = ("wikipedia.org", "wikimedia.org", "youtube.com", "x.com")

text = open(sys.argv[1], errors="replace").read()
hosts = collections.Counter(re.findall(r'"(?:https?|wss?)://([^/":?]+)', text))
print("Hosts contacted (requests):")
for host, n in hosts.most_common():
    mark = "  " if host.endswith(OPENED) else "!!"
    print(f"  {mark} {n:5d}  {host}")
unexpected = [h for h in hosts if not h.endswith(OPENED)]
print(f"\n{len(unexpected)} host(s) not opened by the scenes" + (": " + ", ".join(sorted(unexpected)) if unexpected else ""))
if len(sys.argv) > 2:
    blocked = [l.strip() for l in open(sys.argv[2], errors="replace") if "qjz9zk" in l or "TrkProtocolHandler" in l]
    print(f"{len(blocked)} request(s) stopped by domain substitution (each is a feature that tried to reach Google)")
    for line in blocked[:30]:
        print("   ", line[:200])
