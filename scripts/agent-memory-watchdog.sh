#!/bin/bash
# Memory watchdog for coding agents on macOS (no cgroups there).
# Kills any descendant of a WATCH process whose resident memory exceeds MEM_GB: protects against tests that leak memory while the
# agent waits on them (one such runaway wrote 2.64 TB of swap to my SSD in 4 hours). The agent process itself is never killed.
# Usage: agent-memory-watchdog.sh [--once]   (without --once: loops every INTERVAL seconds)
#   WATCH=crush MEM_GB=1 INTERVAL=2 LOG=~/Library/Logs/agent-memory-watchdog.log agent-memory-watchdog.sh
# A threshold of 1 GB fits a 24 GB Mac with ~400 MB free while a 13 GB local model is loaded; raise it if your builds need more.
# To run it at login, a launchd agent (~/Library/LaunchAgents/local.agent-memory-watchdog.plist) with ProgramArguments
# [/bin/bash, /path/to/agent-memory-watchdog.sh], RunAtLoad and KeepAlive true, and EnvironmentVariables for WATCH and MEM_GB.
MEM_GB=${MEM_GB:-1}; INTERVAL=${INTERVAL:-2}; WATCH=${WATCH:-crush}
LOG=${LOG:-$HOME/Library/Logs/agent-memory-watchdog.log}
desc() { local c; for c in $(pgrep -P "$1"); do echo "$c"; desc "$c"; done; }
check() {
  for root in $(pgrep -x "$WATCH"); do
    for p in $(desc "$root"); do
      rss=$(ps -o rss= -p "$p" 2>/dev/null | tr -d ' ')
      [ -n "$rss" ] && [ "$rss" -ge $((MEM_GB * 1048576)) ] || continue
      echo "$(date '+%F %T') kill pid=$p rss=$((rss / 1024))MB parent=$WATCH($root) cmd=$(ps -o command= -p "$p" | cut -c1-120)" >> "$LOG"
      kill -TERM $p $(desc "$p") 2>/dev/null; sleep 2; kill -KILL $p $(desc "$p") 2>/dev/null
    done
  done
}
if [ "${1:-}" = --once ]; then check
else while true; do check; sleep "$INTERVAL"; done; fi
