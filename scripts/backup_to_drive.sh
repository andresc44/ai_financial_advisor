#!/usr/bin/env bash
# Copies Finnbot's irreplaceable files to Google Drive with rclone, keeping dated copies of anything that changed.
#
# Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-16, at Andres's request.
#
# Why: the track record, the API usage ledger, and the watchdog / digest state exist only on the VM. If the VM's
# disk is lost, so is the history of every recommendation and what it cost. Setup steps are in docs/operations.md.
#
# Run nightly by cron through scripts/cron_job.sh (shared lock + healthchecks.io), or by hand:
#   bash scripts/backup_to_drive.sh
#
# Settings (.env, all optional):
#   FINNBOT_BACKUP_REMOTE     rclone destination (default "finnbot-backup:", the encrypted remote from the setup)
#   FINNBOT_BACKUP_KEEP_DAYS  days to keep the previous versions of changed files (default 90)
#
# Layout on Drive:
#   current/data/...                     the latest copy of each file in scripts/backup_filter.txt
#   archive/<UTC timestamp>/data/...     the version each run replaced (rclone --backup-dir), deleted after KEEP_DAYS
# Files are only ever added or replaced, never deleted from current/, so a wiped data/ folder can't wipe the backup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
STATUS_FILE="logs/backup.status"
mkdir -p logs
trap 'echo "Backup failed at line $LINENO (see logs/backup.log)." > "$STATUS_FILE"' ERR

source scripts/env_value.sh  # env_value KEY: reads .env without running it as shell code

REMOTE="${FINNBOT_BACKUP_REMOTE:-$(env_value FINNBOT_BACKUP_REMOTE)}"
REMOTE="${REMOTE:-finnbot-backup:}"
KEEP_DAYS="${FINNBOT_BACKUP_KEEP_DAYS:-$(env_value FINNBOT_BACKUP_KEEP_DAYS)}"
KEEP_DAYS="${KEEP_DAYS:-90}"
remote_path() {  # "finnbot-backup:" + "current" -> "finnbot-backup:current"; "gdrive:finnbot" -> "gdrive:finnbot/current"
  case "$REMOTE" in *:) echo "$REMOTE$1" ;; *) echo "${REMOTE%/}/$1" ;; esac
}

if ! command -v rclone > /dev/null; then
  echo "rclone is not installed; see docs/operations.md." | tee "$STATUS_FILE" >&2
  exit 1
fi

STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
echo "$(date -u +%FT%TZ) Backing up data/ to $(remote_path current/data)"
rclone copy data "$(remote_path current/data)" \
  --filter-from scripts/backup_filter.txt \
  --backup-dir "$(remote_path "archive/$STAMP/data")" \
  --stats-one-line --stats-log-level NOTICE

# Retention: archive folders are named by UTC timestamp, so compare their date part with the cutoff date
CUTOFF="$(date -u -d "-$KEEP_DAYS days" +%Y-%m-%d)"
{ rclone lsf --dirs-only "$(remote_path archive)" 2> /dev/null || true; } | while IFS= read -r dir; do
  dir="${dir%/}"
  if [[ "${dir:0:10}" < "$CUTOFF" ]]; then
    echo "Removing archive/$dir (older than $KEEP_DAYS days)"
    rclone purge "$(remote_path "archive/$dir")"
  fi
done

SIZE="$(rclone size data --filter-from scripts/backup_filter.txt --json)"
FILES="$(echo "$SIZE" | sed -E 's/.*"count":([0-9]+).*/\1/')"
BYTES="$(echo "$SIZE" | sed -E 's/.*"bytes":([0-9]+).*/\1/')"
echo "Backup finished: $FILES files, $((BYTES / 1024)) KiB; previous versions kept $KEEP_DAYS days." | tee "$STATUS_FILE"
