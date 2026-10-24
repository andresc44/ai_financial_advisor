#!/usr/bin/env bash
# Pauses Finnbot's cron jobs while code is being changed on the VM, so no run starts on half-edited files.
#
# Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-24, at Andres's request.
#
# Why: the VM runs whatever is in this folder. A watchdog (:45) or digest (:10) that starts while files are half
# edited can crash, or import a mix of old and new code. While maintenance mode is on, scripts/cron_job.sh skips the
# watchdog and the digest (the backup still runs) and reports the skip to healthchecks.io as a success. It turns
# itself off after the hours given, so a forgotten `off` can't stop Finnbot for long. A digest skipped inside its
# window is retried at the next hour's run (src/digest/digest_schedule.py). See CLAUDE.md.
#
# Usage (from the project root):
#   bash scripts/maintenance.sh on [hours]   # pause for 1-6 hours (default 1); waits for a running job to finish
#   bash scripts/maintenance.sh off          # resume now
#   bash scripts/maintenance.sh status       # paused or not, and whether a job is running now
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FILE="$ROOT/data/maintenance_until"  # When maintenance mode ends (epoch seconds); read by scripts/cron_job.sh
LOCK=/tmp/finnbot.lock  # The lock every cron job holds while it runs

job_running() { ! flock -n "$LOCK" true; }

case "${1:-status}" in
  on)
    HOURS="${2:-1}"
    [[ "$HOURS" =~ ^[1-6]$ ]] || { echo "Hours must be a whole number from 1 to 6." >&2; exit 2; }
    mkdir -p "$(dirname "$FILE")"
    echo $(( $(date +%s) + HOURS * 3600 )) > "$FILE"
    echo "Maintenance mode on until $(date -d "@$(cat "$FILE")" '+%H:%M %Z'): the watchdog and digest are skipped."
    if job_running; then
      echo "A job is running now; waiting for it to finish (up to 45 minutes)..."
      if ! flock -w 2700 "$LOCK" true; then
        echo "Still running after 45 minutes. Check logs/ before editing anything." >&2
        exit 1
      fi
    fi
    echo "Nothing is running: safe to edit. Run 'bash scripts/maintenance.sh off' when the tests pass."
    ;;
  off)
    rm -f "$FILE"
    echo "Maintenance mode off: the next watchdog (:45) and digest (:10) run as scheduled."
    ;;
  status)
    END="$(cat "$FILE" 2> /dev/null || true)"
    if [[ "$END" =~ ^[0-9]+$ ]] && [ "$(date +%s)" -lt "$END" ]; then
      echo "Maintenance mode: on until $(date -d "@$END" '+%H:%M %Z')."
    else
      echo "Maintenance mode: off."
    fi
    if job_running; then echo "A job is running now."; else echo "No job is running now."; fi
    echo "Schedule ($(date '+%H:%M %Z') now): watchdog at :45, digest at :10 every hour; backup at 02:30 UTC."
    ;;
  *)
    echo "usage: $0 on [hours] | off | status" >&2
    exit 2
    ;;
esac
