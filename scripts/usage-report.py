#!/usr/bin/env python3
"""Usage report for a hybrid AI stack (read-only, aggregates only: no session content is read).

Measure usage before optimizing: sessions per client, MCP bridge calls, watchdog interventions, quota readings. SQLite databases are
opened with `immutable=1` (no write, no lock). The paths below are those of my setup; adapt them to yours.

Usage: usage-report.py [DAYS]   (default 30)
"""
import collections, json, os, sqlite3, subprocess, sys, time

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
SINCE = time.time() - DAYS * 86400
HOME = os.path.expanduser("~")
BRIDGE_TRACE = os.environ.get("BRIDGE_TRACE", f"{HOME}/Library/Logs/m5-bridge-trace.jsonl")  # one JSON object per MCP message
WATCHDOG_LOG = os.environ.get("WATCHDOG_LOG", f"{HOME}/Library/Logs/agent-memory-watchdog.log")
QUOTA_LOG = os.environ.get("QUOTA_LOG", f"{HOME}/.claude/quota-log.jsonl")  # written by a status line from `rate_limits`


def db(path):
    return sqlite3.connect(f"file:{path}?immutable=1", uri=True)


def jsonl(path):
    try:
        return [json.loads(line) for line in open(path) if line.strip()]
    except FileNotFoundError:
        return []


rows = []
trace = [d for d in jsonl(BRIDGE_TRACE) if d.get("ts", 0) >= SINCE]
methods = collections.Counter(d.get("method") for d in trace)
rows.append(("MCP bridge", f"{methods['initialize']} starts, {methods['tools/call']} tool calls"))

def crush_dbs(depth=6, skip=("Library", ".Trash", "node_modules", ".git")):
    """`.crush/crush.db` databases under ~, hidden directories included, ~/Library skipped."""
    found = []
    for d, dirs, files in os.walk(HOME):
        dirs[:] = [x for x in dirs if x not in skip] if d[len(HOME):].count(os.sep) < depth else []
        if os.path.basename(d) == ".crush" and "crush.db" in files:
            found.append(os.path.join(d, "crush.db"))
    return found


crush = crush_dbs()
n = sum(db(p).execute("select count(*) from sessions where created_at >= ?", (SINCE,)).fetchone()[0] for p in crush)
rows.append(("Crush", f"{n} sessions ({len(crush)} databases found under ~, ~/Library skipped)"))

oc = f"{HOME}/.local/share/opencode/opencode.db"
if os.path.exists(oc):
    per = collections.Counter(json.loads(m).get("id") if m else "?" for (m,) in db(oc).execute(
        "select model from session_v2 where time_created >= ?", (SINCE * 1000,)))
    models = ", ".join(f"{m} {c}" for m, c in per.most_common()) or "none"
    rows.append(("OpenCode", f"{sum(per.values())} sessions ({models})"))

lines = [l for l in open(WATCHDOG_LOG) if l[:19] >= time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(SINCE))] \
    if os.path.exists(WATCHDOG_LOG) else []
rows.append(("Memory watchdog", f"{sum(' kill ' in l for l in lines)} kills (tests included)"))

quota = [d for d in jsonl(QUOTA_LOG) if d.get("ts", 0) >= SINCE]
last = (quota[-1].get("seven_day") or {}).get("used_percentage") if quota else None
rows.append(("Claude quota", f"{len(quota)} readings, last 7-day usage: {last if last is not None else '—'} %"))

up = subprocess.run(["pgrep", "-x", "llama-server"], capture_output=True).returncode == 0
rows.append(("llama-server", "loaded now" if up else "not loaded now"))

print(f"Usage over {DAYS} days (as of {time.strftime('%Y-%m-%d')})\n\n| Component | Measure |\n|---|---|")
for name, value in rows:
    print(f"| {name} | {value} |")
