#!/usr/bin/env bash
# Runs one Finnbot cron job under the shared lock and reports how it went to healthchecks.io.
#
# Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-16, at Andres's request.
#
# Why: Finnbot's own Telegram alerts only work while it runs. If cron stops, the VM goes down, the disk fills up, or a
# run hangs, nothing is sent. healthchecks.io expects a ping after every run and alerts when one is late, failed, or
# never finished. Setup steps are in docs/operations.md.
#
# Usage (crontab):  bash scripts/cron_job.sh watchdog | digest | backup
#
# For each run it pings https://hc-ping.com/<HEALTHCHECKS_PING_KEY>/finnbot-<job>:
#   /start     before the job (healthchecks.io then also catches a run that never finishes);
#   /<code>    after it, with the job's exit code (0 = success, anything else = failure) and logs/<job>.status
#              (one line with counts only, written by the job) as the message.
# `?create=1` creates the check on its first ping. Without HEALTHCHECKS_PING_KEY the job still runs, just unmonitored.
# A failed ping never fails the job.
set -u

JOB="${1:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

case "$JOB" in
  watchdog) CMD=(venv/bin/python src/telegram_alert/hourly_watchdog.py); OUT=watchdog.log ;;
  digest)   CMD=(venv/bin/python src/digest/digest_payload.py);          OUT=digest.log ;;
  backup)   CMD=(bash scripts/backup_to_drive.sh);                       OUT=logs/backup.log ;;
  *) echo "usage: $0 watchdog|digest|backup" >&2; exit 2 ;;
esac

source scripts/env_value.sh  # env_value KEY: reads .env without running it as shell code

PING_KEY="${HEALTHCHECKS_PING_KEY:-$(env_value HEALTHCHECKS_PING_KEY)}"
PING_BASE="${HEALTHCHECKS_PING_URL:-$(env_value HEALTHCHECKS_PING_URL)}"
PING_BASE="${PING_BASE:-https://hc-ping.com}"  # Only change it for a self-hosted Healthchecks
RUN_ID="$(cat /proc/sys/kernel/random/uuid 2> /dev/null || uuidgen 2> /dev/null || true)"
RID_PARAM="${RUN_ID:+&rid=$RUN_ID}"  # Pairs /start with its result, so healthchecks.io shows the run's duration

mkdir -p logs
STATUS_FILE="logs/$JOB.status"
rm -f "$STATUS_FILE"  # So a crash before the job writes its status can't report the previous run's

ping_hc() {  # $1: "/start" or "/<exit code>"; $2: message
  [ -n "$PING_KEY" ] || return 0
  curl -fsS -m 10 --retry 3 -o /dev/null --data-binary "${2:-}" \
    "${PING_BASE%/}/$PING_KEY/finnbot-$JOB$1?create=1$RID_PARAM" 2>> logs/heartbeat.log \
    || echo "$(date -u +%FT%TZ) could not ping healthchecks.io for $JOB$1" >> logs/heartbeat.log
}

ping_hc /start ""
# -w 900: wait up to 15 minutes for another job to finish; -E 75: a distinct exit code when that wait times out
flock -w 900 -E 75 /tmp/finnbot.lock "${CMD[@]}" >> "$OUT" 2>&1
CODE=$?

if [ -s "$STATUS_FILE" ]; then
  MESSAGE="$(head -c 2000 "$STATUS_FILE")"
elif [ "$CODE" -eq 75 ]; then
  MESSAGE="Didn't run: another Finnbot job still held the lock after 15 minutes (possibly stuck)."
else
  LOGS="logs/$JOB.log"
  [ "$OUT" = "$LOGS" ] || LOGS="$LOGS and $OUT"
  MESSAGE="Exited with code $CODE without a status line; see $LOGS on the server."
fi
ping_hc "/$CODE" "$MESSAGE"
exit "$CODE"
