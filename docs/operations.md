# Monitoring and backups

Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-16, at Andres's request.

Finnbot already sends a Telegram alert when a run fails, but only while it's running. Two things cover the rest:

- **Monitoring:** [healthchecks.io](https://healthchecks.io) expects a ping after every cron run. It alerts you when a run fails, hangs, or doesn't happen at all, e.g. because the VM is down, cron stopped, or the disk is full.
- **Backups:** every night, [rclone](https://rclone.org) copies the files that can't be rebuilt to your Google Drive, encrypted, keeping 90 days of previous versions.

Both go through `scripts/cron_job.sh`, which every crontab line now calls. It takes the shared lock (as before), runs the job, and pings healthchecks.io with the result.

## What is monitored

| Check (created on first ping) | Runs | Turns red when |
|---|---|---|
| `finnbot-watchdog` | Every hour at :45 | A run is late or missing, takes too long, or a user's check crashed. |
| `finnbot-digest` | Every hour at :10 | A run is late or missing, takes too long, the universal content or a user's digest failed, or **a digest from earlier today never went out** (e.g. the VM was down for the whole window). |
| `finnbot-backup` | Daily at 02:30 (VM clock, UTC) | The backup is late or missing, or rclone failed (e.g. the Google login expired). |

- **Lock timeouts:** if a job waits more than 15 minutes for the lock because another job is stuck, it reports exit code 75 with that reason.
- **Maintenance mode (added 2026-10-24):** `bash scripts/maintenance.sh on [hours]` skips the watchdog and digest while code is changed on the VM (1 to 6 hours, then it turns itself off; `off` ends it early). Each skipped run pings as a success with the message `Skipped: maintenance mode until ...`, so nothing turns red. The backup still runs. See [CLAUDE.md](../CLAUDE.md).
- **Open files:** `scripts/cron_job.sh` raises the open-file limit to 65536 (cron's default is 1024, which every digest hit on 2026-10-23 with `[Errno 24] Too many open files`).
- **Messages:** each ping carries a one-line status, e.g. `Post-Market 2026-10-16: sent 3, failed 0 in 214s.` You'll see it under the check's Events. These lines hold counts only, never names, holdings, or amounts; the details stay in `logs/` and in the admin Telegram alerts.
- **Your own key problems don't count:** a user's expired or out-of-credit key turns nothing red. That user already gets their own notice.
- **Alerts are sent on changes only:** healthchecks.io alerts once when a check goes red and once when it recovers, not every hour.

## Your steps: healthchecks.io (about 10 minutes)

1. Sign up at [healthchecks.io](https://healthchecks.io). The free Hobbyist plan (20 checks) is enough.
2. **Telegram:** open *Integrations*, click *Add Integration* next to Telegram, and follow the link it gives you to the healthchecks.io bot. Email alerts to your sign-up address are on by default.
3. **Ping key:** open *Settings* (the project settings, in the top bar). Under *Ping key*, click *Create*, then copy the key.
4. **On the VM**, add it to `.env`:
   ```
   HEALTHCHECKS_PING_KEY=<the key>
   ```
5. Update the crontab ([below](#crontab)). The first run of each job creates its check.
6. When the three checks appear, set each one's schedule (click the check, then *Change Schedule*) and check that Telegram is ticked under its notification methods:

   | Check | Schedule | Period | Grace |
   |---|---|---|---|
   | `finnbot-watchdog` | Simple | 1 hour | 45 minutes |
   | `finnbot-digest` | Simple | 1 hour | 45 minutes |
   | `finnbot-backup` | Simple | 1 day | 2 hours |

   Grace has two jobs here. It's how late a ping may be, and also the longest a run may take after its start ping. A job can wait 15 minutes for the lock, and a digest takes 10 to 20 minutes, hence 45.
7. **Test it:** on the VM, run `bash scripts/cron_job.sh watchdog`. The check should turn green with the status line under *Events*. To see a red alert reach Telegram, run `curl -fsS https://hc-ping.com/<the key>/finnbot-watchdog/fail`. The next real run turns it green again.

## Your steps: rclone backups to Google Drive (about 15 minutes)

Why rclone, not the Finnbot service account: a service account has no Drive storage of its own, so it can't save files to a personal Gmail Drive. Shared Drives would work around that, but they need Google Workspace. rclone logs in once as you, and with the `drive.file` permission it can only see the files it creates. The rest of your Drive is out of its reach.

1. **Install rclone on the VM:**
   ```bash
   sudo -v ; curl https://rclone.org/install.sh | sudo bash
   ```
2. **Install rclone on your Windows PC** too, for the one-time Google sign-in (the VM has no browser):
   ```powershell
   winget install Rclone.Rclone
   ```
3. **Connect Google Drive.** On the VM, run `rclone config` and answer:
   - `n` (new remote), name: `gdrive`
   - Storage: `drive` (Google Drive)
   - `client_id` and `client_secret`: press Enter to leave them blank. rclone's own app is fine for a few MB a night.
   - `scope`: choose **`drive.file`** (access to files created by rclone only).
   - `service_account_file`: press Enter to leave it blank.
   - Edit advanced config: `n`
   - Use web browser to authenticate: **`n`**. rclone prints a command like `rclone authorize "drive" "eyJ..."`.
   - On your PC, paste that command into PowerShell. A browser opens. Sign in with the Google account whose Drive should hold the backups, and allow access. PowerShell then prints a token. Paste it back into the VM's prompt.
   - Configure as a Shared Drive: `n`. Keep this remote: `y`.
4. **Add encryption on top.** This is recommended, because the state files show what each user holds. Run `rclone config` again:
   - `n`, name: **`finnbot-backup`**
   - Storage: `crypt`. Remote to encrypt: `gdrive:finnbot-backup`
   - `filename_encryption`: `standard`. `directory_name_encryption`: `true`
   - Password: `g` (generate), 256 bits. Salt password: `g` as well.
   - **Save both generated passwords in your password manager now.** Without them the backups can't be decrypted once the VM is gone. `rclone.conf` on the VM only obscures them; it doesn't encrypt them.

   To skip encryption instead, put `FINNBOT_BACKUP_REMOTE=gdrive:finnbot-backup` in `.env`.
5. **Test it:** run `bash scripts/backup_to_drive.sh`, then `rclone ls finnbot-backup:current`. In Drive's web view you'll see a `finnbot-backup` folder with scrambled names.
6. Add the backup line to the crontab ([below](#crontab)).

**What is backed up** (`scripts/backup_filter.txt`):
- the track record and API usage ledger;
- `watchdog_state.json` and `digest_state.json`;
- the alert log;
- `idea_positions.json`: which holdings were bought from Finnbot's ideas (2026-10-22).

Caches (`market_context_cache.json`, `fx_rates.json`, `ticker_data/`) are rebuilt on the next run, so they're left out. `.env` belongs in your password manager, not in Drive.

**The run logs (added 2026-10-22)**, so you can read them on your phone. Each night, every file in `logs/` (`digest.log` and its rotated `digest.log.1` to `.5`, `watchdog.log`, `backup.log`, and the `.status` lines) is copied to a **`finnbot-logs`** folder in your Drive, with `.txt` added to each name so the Drive app opens it as text. Only the logs that changed since the night before are uploaded.
- **To read them:** in the Google Drive app, search for `finnbot-logs` and open e.g. `digest.log.txt`. The newest lines are at the bottom. Since the backup runs at 02:30 UTC, they're up to the end of the previous day.
- **Not encrypted:** the logs name users and show their holdings, and this copy is readable by anyone who can open your Drive. To encrypt it like the rest (then it's only readable through rclone, e.g. `rclone cat finnbot-backup:logs/digest.log.txt | tail -n 200`), set `FINNBOT_LOGS_REMOTE=finnbot-backup:logs` in `.env`. `FINNBOT_LOGS_REMOTE=none` leaves the logs out.
- **If it fails:** the data backup still runs and its check stays green, but the status line under the check's *Events* ends with `LOGS COPY FAILED`, and `logs/backup.log` says why.

**Layout on Drive:** `current/data/` holds the latest copy. Each night, the version a changed file replaced moves to `archive/<UTC time>/data/`, and archive folders older than `FINNBOT_BACKUP_KEEP_DAYS` (90) are deleted. Nothing is ever deleted from `current/`, so a wiped `data/` folder on the VM can't wipe the backup.

**Restore:**
```bash
rclone copy finnbot-backup:current/data data                        # the latest copy
rclone lsf finnbot-backup:archive                                    # dated folders of replaced versions
rclone copy finnbot-backup:archive/2026-10-20T023000Z/data data      # what those files were before that night
```
On a new VM, install rclone and create both remotes again with the **same crypt passwords**, then run the first command.

**If the Google login stops working**, the backup check turns red and `logs/backup.log` shows an auth error. Fix it on the VM with `rclone config reconnect gdrive:`, which repeats the PC sign-in from step 3.

## Crontab

Replace the old Finnbot lines (`crontab -e` on the VM) with:

```cron
45 * * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh watchdog
10 * * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh digest
30 2 * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh backup
```

The wrapper now does what the old lines did: `flock -w 900 /tmp/finnbot.lock`, `venv/bin/python ...`, and `>> digest.log`. It also does the pinging. With the VM's clock on UTC, 02:30 is 22:30 or 21:30 ET, after the post-market digest window has closed.

Files the wrapper writes, all in the gitignored `logs/` folder:
- `logs/<job>.status`: the last run's status line;
- `logs/heartbeat.log`: pings that couldn't reach healthchecks.io;
- `logs/backup.log`: the backup's output.
