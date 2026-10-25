# Finnbot: notes for Claude

Coding conventions are in [CONVENTIONS.md](CONVENTIONS.md). The admin's guide is [docs/admin_handbook.md](docs/admin_handbook.md).

## Working on the VM (live cron jobs)

On the VM (`crontab -l` lists `scripts/cron_job.sh`), this folder is the live deployment. Cron runs whatever is checked
out here, through `scripts/cron_job.sh`:

| Job | When (VM clock, UTC; CRON_TZ in the crontab) | Takes |
|---|---|---|
| watchdog | every hour at :45 | seconds; minutes when it evaluates holdings |
| digest | every hour at :10 | about 25 minutes when one is due; seconds otherwise |
| backup | 02:30 daily | a minute |

The jobs share `/tmp/finnbot.lock`, so they never overlap. A digest is due on weekdays from 07:00 ET until the open, and
from 16:10 to 20:10 ET.

Before changing code, templates, `parameters.py`, or `.env` on the VM:

1. `bash scripts/maintenance.sh on` (1 hour; `on 2` for longer, up to 6). The watchdog and digest are skipped until it
   ends, and it waits for a running job to finish first. It turns itself off when the time is up.
2. Make the change, and run the tests: `venv/bin/python -m pytest` (about a minute; they write only to temp folders).
3. `bash scripts/maintenance.sh off`.

Always pause first (Andres, 2026-10-25): any change to files in this folder on the VM, docs and tests included, and any
git pull, checkout, or merge. During a digest window (above), keep the pause under an hour: a skipped digest is retried
at the next hour's run, but not after its window closes.

Also on the VM:
- Don't switch branches, stash, rebase, or reset unless maintenance mode is on. Cron runs whatever is checked out.
- Run a job by hand only under the shared lock, so it can't overlap a cron run and write the same state files:
  `flock -w 900 /tmp/finnbot.lock venv/bin/python src/digest/digest_payload.py --payload-only` (no email), or
  `flock -w 900 /tmp/finnbot.lock venv/bin/python src/telegram_alert/hourly_watchdog.py --dry-run`. A real run
  emails users and spends their API credits, so ask Andres first.
- Replace `scripts/*.sh` by writing a new file and `mv`-ing it over the old one, not by editing in place. A running bash
  reads its script as it goes.
- Never print `.env` values; list key names only (`cut -d= -f1`).
- Cron starts jobs with a soft limit of 1024 open files; `scripts/cron_job.sh` and the crontab raise it to 65536 (the
  digest ran out on 2026-10-23). Keep both.
