# Running Finnbot on a VM (Google Cloud)

Everything to move Finnbot from the Windows PC to an always-on VM, step by step. Written for Google Cloud; Finnbot actually runs on an OVHcloud VPS-1 (see the status below). Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-23, at Andres's request (Google Cloud only). Monitoring and backups are covered in more depth in [operations.md](operations.md); every command is in [admin_handbook.md](admin_handbook.md).

**Status (2026-10-26): Finnbot has run on a VM since 2026-10-23**, with the hourly digest and watchdog under cron, the nightly Drive backup, and the healthchecks.io heartbeat all set up. The machine in use measures 2 vCPUs, about 3.8 GB of memory, 4 GB of swap, a 38 GB disk, Ubuntu, and Python 3.14, so it is bigger than the free e2-micro this guide starts with. **It is an OVHcloud VPS-1 (about A$85 a year), not Google Cloud**: the Ubuntu, Python, swap, cron, healthchecks and backup steps below apply to any Ubuntu VM, but the Google Cloud console and `gcloud` steps (parts 1 and 6.3) don't; use OVH's control panel for the machine itself. Measured on it: a full digest for three users takes about 25 minutes (1,501 s on 2026-10-26) and peaks at about 790-800 MB (788 MB on 2026-10-25, 798 MB on 2026-10-26).

Without a running VM, nothing in Finnbot happens on a schedule: no hourly digest runs (so no digests, earnings reminders, sign-up sync, or settings sync), no watchdog, and no backups. Code changes on the VM are made in maintenance mode (`bash scripts/maintenance.sh on`; [CLAUDE.md](../CLAUDE.md)).

---

## 0. Which VM, and what it costs

**Start on Google Cloud's free e2-micro, and measure.** Google's [free tier](https://docs.cloud.google.com/free/docs/free-cloud-features) (checked 2026-10-23) includes, per billing account and month:
- one **e2-micro** VM (2 shared vCPUs, 1 GB of memory), in **Oregon (`us-west1`), Iowa (`us-central1`), or South Carolina (`us-east1`)** only;
- **30 GB of standard persistent disk**;
- **1 GB of outbound traffic** from North America (traffic to China and Australia isn't included).

**What Finnbot needs.** One Python process at a time, mostly waiting on APIs. Its outbound traffic (prompts to the LLMs, emails, Telegram) is a few tens of MB a month, and its state is about 1.4 MB. The open question is memory: 1 GB is tight for the idea screen, so the setup adds 2 GB of swap and measures a full run (part 6). If the run is too slow or uses more than about 800 MB, switch to a bigger machine type (part 6.3). That takes two minutes and changes nothing else.

| Machine type | Memory | Cost (on-demand, approximate) | When |
|---|---|---|---|
| **e2-micro** | 1 GB | **Free** (free tier) | Start here |
| e2-small | 2 GB | about $12 a month | If the e2-micro is too slow or short of memory |
| e2-medium | 4 GB | about $24 a month | Not expected to be needed |

**Charges that may still apply:**
- **The public IP address.** Google lists in-use external IPv4 addresses at $0.005 an hour (about $3.65 a month), and its free-tier page doesn't say whether the free VM's address is included. Check the first month's bill. The VM needs a public address (or a paid Cloud NAT) to reach the APIs.
- **The disk type.** The console's default boot disk is a **balanced** persistent disk, which is not free; choose **standard** (part 1.3).
- **Backups and snapshots.** Turn off the console's VM backup options. Finnbot backs itself up to Google Drive (part 8).
- **SSH sessions from Australia.** They count toward outbound traffic to Australia, which isn't free, but they're a few cents at most.

Set a budget alert first (part 1.1), so any surprise shows within days.

---

## 1. Create the VM (Google Cloud console, about 15 minutes)

Use the project Finnbot's service account already lives in (`finnbot-504921`), so everything stays in one place.

### 1.1 Billing and a budget alert

1. Open [console.cloud.google.com](https://console.cloud.google.com) and select the project `finnbot-504921` (top bar).
2. **Billing**: make sure the project is linked to a billing account; the free tier needs one, even though the free VM costs nothing.
3. **Billing** → **Budgets & alerts** → **Create budget**:
   - scope: this project;
   - amount: $5 a month;
   - alerts at 50%, 90%, and 100% of actual spend, emailed to you.

### 1.2 Turn on Compute Engine

**Compute Engine** → **VM instances**. If it asks, click **Enable** for the Compute Engine API. It takes a minute.

### 1.3 The instance

**Create instance**, then:

| Section | Field | Choose |
|---|---|---|
| Machine configuration | Name | `finnbot` |
| | Region / zone | `us-east1` (South Carolina) / `us-east1-b`. `us-central1` or `us-west1` are also free. |
| | Series / machine type | **E2** → **e2-micro** (2 vCPU, 1 GB memory) |
| | VM provisioning model | **Standard** (not Spot: a Spot VM can be stopped at any time) |
| OS and storage | Image | **Change** → **Ubuntu** → **Ubuntu 24.04 LTS** (x86/64, `amd64`) |
| | Boot disk type | **Standard persistent disk** (the free one) |
| | Size | **30 GB** |
| Data protection | Backups | **No backups** |
| Networking | Firewall | Leave **Allow HTTP/HTTPS traffic** unticked |
| | External IPv4 | **Ephemeral** (the default) |
| Observability | Ops Agent | **Untick** "Install Ops Agent for Monitoring and Logging" (it needs memory the e2-micro doesn't have to spare) |

The summary on the right should say "e2-micro" and a free-tier note. **Create** it and wait for the green tick.

Google's default firewall only lets SSH in, which is all Finnbot needs: it only makes outgoing connections (the APIs, Gmail on port 587, Telegram). Google blocks outgoing port 25, which Finnbot doesn't use. Don't open any other port.

### 1.4 The Google Cloud CLI on the PC, and connecting

The `gcloud` command handles the SSH keys and copies files. In PowerShell on the PC:

```powershell
winget install Google.CloudSDK
# Open a new PowerShell window, then:
gcloud init                                         # Sign in with your Google account; pick project finnbot-504921
gcloud config set compute/zone us-east1-b           # The zone from 1.3, so the commands below don't need it
gcloud compute ssh finnbot                          # First time: creates an SSH key (Enter for no passphrase)
```

You're now on the VM, as a Linux user with your Windows user name (e.g. `andre`), or one derived from your Google account if the project uses OS Login. Everything in parts 2-9 runs in this session unless it says "on the PC". The console's **SSH** button next to the VM opens the same session in a browser.

---

## 2. Prepare Ubuntu (about 5 minutes)

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install git build-essential curl time unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades   # Yes: security updates install themselves
timedatectl                                       # "Time zone: Etc/UTC" (keep UTC; the crontab times assume it)

# 2 GB of swap: on the e2-micro's 1 GB this is what keeps a large run from running out of memory
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h                                           # About 1 GB of memory and 2 GB of swap
```

If `apt upgrade` mentions a new kernel, run `sudo reboot`, wait a minute, and reconnect with `gcloud compute ssh finnbot`.

---

## 3. Get the code (about 5 minutes)

The repository is private, so the VM gets its own read-only **deploy key**:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_finnbot -N "" -C finnbot-vm
cat ~/.ssh/github_finnbot.pub
```

Copy the printed line. On GitHub: the `ai_financial_advisor` repository → **Settings** → **Deploy keys** → **Add deploy key**, title `finnbot-vm`, paste it, leave **Allow write access** unticked, **Add key**. Then on the VM:

```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/github_finnbot\n  IdentitiesOnly yes\n' >> ~/.ssh/config
chmod 600 ~/.ssh/config
git clone git@github.com:andresc44/ai_financial_advisor.git ~/ai_financial_advisor
cd ~/ai_financial_advisor && git checkout main && git log --oneline -1
```

---

## 4. Python (about 10 minutes on the e2-micro)

Finnbot is developed on Python 3.14 (`CONVENTIONS.md`), so the VM uses the same version, installed with `uv` (Ubuntu 24.04's own Python is 3.12):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
cd ~/ai_financial_advisor
uv python install 3.14
uv venv --python 3.14 venv                                        # The scripts expect venv/bin/python
uv pip install --python venv/bin/python -r requirements-dev.txt   # requirements.txt plus pytest
venv/bin/python --version                                         # Python 3.14.x
```

If a package fails to install, copy the error into a Claude session.

---

## 5. Secrets and state (about 10 minutes)

### 5.1 Copy from the PC

Stop anything Finnbot is doing on the PC first (no digest or watchdog running), so the state you copy is final. Then, **on the PC**, in PowerShell:

```powershell
$repo = "C:\Users\Andre\Documents\git\ai_financial_advisor"
# The service account's JSON file: its path is GOOGLE_APPLICATION_CREDENTIALS in the PC's .env
$json = ((Get-Content "$repo\.env" | Select-String '^GOOGLE_APPLICATION_CREDENTIALS=').Line -split '=', 2)[1].Trim('"', ' ')
gcloud compute scp "$repo\.env" finnbot:~/ai_financial_advisor/.env
gcloud compute scp "$json" finnbot:~/finnbot-service.json
gcloud compute scp --recurse "$repo\data" finnbot:~/ai_financial_advisor/
```

They travel encrypted over SSH. The `data/` folder (about 1.4 MB) carries on the PC's state:
- who already received today's digest;
- the watchdog's last reviews;
- the track record and the API usage ledger;
- the holdings bought from ideas;
- the settings accepted from the Parameters tab.

### 5.2 Adjust them on the VM

```bash
cd ~/ai_financial_advisor
chmod 600 .env ~/finnbot-service.json
sed -i 's/\r$//' .env                                              # Windows line endings to Linux (harmless either way)
sed -i "s|^GOOGLE_APPLICATION_CREDENTIALS=.*|GOOGLE_APPLICATION_CREDENTIALS=$HOME/finnbot-service.json|" .env
sed -i '/^UNIVERSAL_LLM_MODEL=/d;/^UNIVERSAL_LLM_EFFORT=/d' .env    # So the Parameters tab decides the ideas' model
grep -E '^(GOOGLE_APPLICATION_CREDENTIALS|UNIVERSAL_LLM)' .env      # The new path, and no UNIVERSAL_LLM lines
```

`.env` holds every key: never print it whole, and keep it out of git (it already is). The PC's copy can stay, for manual test runs; when a key changes, change it in both.

---

## 6. Test, and measure the machine (about 45 minutes, mostly waiting)

### 6.1 The quick checks

```bash
cd ~/ai_financial_advisor
venv/bin/python -m src.config.check_config                      # Every required setting OK; fix anything MISSING or INVALID
venv/bin/python -m pytest                                # About 440 tests, offline (a few minutes on the e2-micro)
venv/bin/python -m src.config.remote_settings --sync            # Reads the Parameters tab: proves the service account works
venv/bin/python src/digest/digest_payload.py --check     # Says whether a digest is due and for whom; sends nothing
venv/bin/python src/telegram_alert/hourly_watchdog.py --dry-run   # Which users and stocks it would check; sends nothing
```

### 6.2 One full run, measured (costs about $0.20)

This proves the market data, the LLMs, Gmail, and the email all work from the VM, and shows whether the e2-micro is big enough:

```bash
/usr/bin/time -v venv/bin/python src/digest/digest_payload.py --payload-only --period Post-Market 2> ~/run_measure.txt
grep -E "Maximum resident set size|Elapsed \(wall clock\)" ~/run_measure.txt
ls -t test_output/digest_payloads | head -1              # The payload it just saved
venv/bin/python src/digest/digest_payload.py --from-payload test_output/digest_payloads/<that file>
```

A `[Test]` digest should reach your inbox within a minute of the last command.

### 6.3 Is the e2-micro enough?

| Result | Then |
|---|---|
| Maximum resident set size under about 800,000 kB (800 MB), and the run under about 45 minutes | Keep the e2-micro (free). Note that the measured peak on the VM in use was already 790-800 MB, so an e2-micro would be at its edge, and a larger digest (more users or holdings) would tip it over. |
| More memory than that, a run killed ("Killed", or "Out of memory" in `sudo dmesg \| tail`), or well over 45 minutes | Move to e2-small, below. |

To change the machine type, **on the PC** (about two minutes; the disk, the files, and the crontab stay as they are):

```powershell
gcloud compute instances stop finnbot
gcloud compute instances set-machine-type finnbot --machine-type e2-small
gcloud compute instances start finnbot
```

---

## 7. Monitoring: healthchecks.io (about 10 minutes)

`HEALTHCHECKS_PING_KEY` is already in `.env`, so each cron job creates its check on its first ping. Full walkthrough: [operations.md](operations.md), "Your steps: healthchecks.io".

1. If not done yet:
   - sign up at [healthchecks.io](https://healthchecks.io) (the free plan);
   - add the **Telegram** integration;
   - create the project's **Ping key** (Settings). It must match `HEALTHCHECKS_PING_KEY`.
2. Once part 9 has run each job, set each check's schedule (click it, then **Change Schedule**):

   | Check | Period | Grace |
   |---|---|---|
   | `finnbot-watchdog` | 1 hour | 45 minutes |
   | `finnbot-digest` | 1 hour | 45 minutes (60-90 if the run in 6.2 took over 40 minutes) |
   | `finnbot-backup` | 1 day | 2 hours |

   The grace is also the longest a run may take after its start ping, so on a slower machine give the digest more.
3. Test an alert: `curl -fsS https://hc-ping.com/<ping key>/finnbot-watchdog/fail` should reach Telegram. The next real run turns the check green again.

---

## 8. Backups to Google Drive (about 15 minutes)

Each night, encrypted, keeping 90 days of previous versions, with rclone. Full walkthrough: [operations.md](operations.md), "Your steps: rclone backups to Google Drive". In short:

1. On the VM: `sudo -v ; curl https://rclone.org/install.sh | sudo bash`
2. On the PC: `winget install Rclone.Rclone` (for the one-time Google sign-in).
3. On the VM, run `rclone config` and create a new remote:
   - name `gdrive`, storage `drive`, scope **`drive.file`**;
   - answer no to using a web browser;
   - run the `rclone authorize ...` command it prints **on the PC**, sign in, and paste the token back.
4. Run `rclone config` again and create a second remote:
   - name `finnbot-backup`, storage `crypt`, remote `gdrive:finnbot-backup`;
   - standard filename encryption, encrypted directory names, generated passwords;
   - **save both passwords in your password manager**: without them the backups can't be decrypted.
5. Test: `bash scripts/backup_to_drive.sh`, then `rclone ls finnbot-backup:current`.

The run logs are also copied each night to a `finnbot-logs` folder in your Drive, so you can read them on your phone. They aren't encrypted unless you set `FINNBOT_LOGS_REMOTE=finnbot-backup:logs`.

---

## 9. Schedule it (2 minutes)

```bash
crontab -e
```

Pick an editor (nano is easiest) and add:

```cron
45 * * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh watchdog
10 * * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh digest
30 2 * * * cd ~/ai_financial_advisor && bash scripts/cron_job.sh backup
```

Save and exit (nano: Ctrl+O, Enter, Ctrl+X); `crontab -l` shows them.

- **The digest line runs every hour.** Finnbot decides from the exchange calendars when a Pre-Market (from 07:00 ET) or Post-Market (16:10 ET) digest is due, so daylight saving needs nothing. Every run also does the settings and sign-up syncs, the earnings reminders, the holiday notice, and the monthly scorecards.
- **The backup at 02:30 UTC** is after the Post-Market window has closed.
- **The shared lock:** the three jobs share one lock (`/tmp/finnbot.lock`), so they never overlap; each waits up to 15 minutes for it.

**From now on the VM is the only scheduled runner.** Make sure the PC isn't scheduled too:

```powershell
schtasks /query /fo LIST | Select-String -Pattern "finnbot|digest_payload|hourly_watchdog"
```

It should print nothing.

Manual test runs on the PC are still fine. Each `--payload-only` run also syncs the Parameters tab from the PC's `parameters.py`, so keep the PC's copy up to date (`git pull`).

---

## 10. The first day

- **Within the hour:** `cat ~/ai_financial_advisor/logs/digest.status` and `logs/watchdog.status` show one line per run, and the healthchecks.io checks turn green.
- **About 07:10 and 16:20 ET** (allow up to 45 minutes on the e2-micro): the digests arrive.
- **Telegram:** earnings reminders and any alerts, as before.
- **After the first Pre-Market and Post-Market digests:** ask Claude to "confirm the news cost". It reads the usage ledger.
- **After the first month:** open **Billing** → **Reports** and check the line items. You should see nothing beyond the possible external IP charge (part 0).

---

## 11. Day to day

| Task | How |
|---|---|
| Connect | On the PC: `gcloud compute ssh finnbot` (or the console's **SSH** button) |
| Update after a change is merged on GitHub | `cd ~/ai_financial_advisor && git pull`. If `requirements.txt` changed, also run `uv pip install --python venv/bin/python -r requirements-dev.txt`. Takes effect at the next cron run, with nothing to restart. A change in `parameters.py` reaches the Parameters tab at the next digest run. |
| Follow a run | `tail -f logs/digest.log` (or `logs/watchdog.log`); Ctrl+C to stop |
| Last run's result | `cat logs/digest.status logs/watchdog.status logs/backup.status` |
| Memory and disk | `free -h`, `df -h /` |
| Settings in force | `venv/bin/python -m src.config.remote_settings --show` |
| Add a new user's keys | `venv/bin/python -m src.users.import_signup <name> --enter-keys` |
| Copy a file to or from the VM | On the PC: `gcloud compute scp <file> finnbot:~/` or `gcloud compute scp finnbot:~/<file> .` |
| Any command from the handbook | The same as on the PC, with `venv/bin/python` |
| Reboot | `sudo reboot`; cron and swap come back by themselves |
| Stop paying for a bigger machine | Part 6.3 in reverse: `--machine-type e2-micro` |

Security updates install themselves (part 2). A monthly `sudo apt update && sudo apt -y upgrade` catches the rest.

---

## 12. When something goes wrong

| Symptom | What to do |
|---|---|
| healthchecks.io says a check is late | `gcloud compute ssh finnbot`: is the VM up (console: VM instances)? Then `crontab -l` and `tail -50 logs/digest.log`. Exit code 75 means a job waited 15 minutes for the lock and another run may be stuck (`ps aux \| grep python`). On a slow machine, a long digest can also make the 10:45 watchdog wait: see 6.3. |
| A run is killed, or `sudo dmesg \| tail` says "Out of memory" | The e2-micro is too small that day: move to e2-small (6.3). |
| The VM is gone, or its disk is broken | Create a new one (parts 1-4). Restore the state with `rclone copy finnbot-backup:current/data data`, after recreating both rclone remotes with the **same** passwords (part 8). Copy `.env` and the JSON again (part 5), and add the crontab (part 9). |
| `Permission denied (publickey)` on `git pull` | The deploy key or `~/.ssh/config` (part 3). |
| Emails stop, and the log says "Username and Password not accepted" | The Gmail app password (`GMAIL_APP_PASSWORD`) was revoked or changed. Create a new one for the digest account and update `.env`. |
| The settings sync fails with 403 | The sign-up spreadsheet must stay shared with the service account as an **Editor**. |
| An unexpected charge | **Billing** → **Reports**, grouped by SKU. The usual suspects are a balanced (not standard) disk, a snapshot schedule, a second VM, or the external IP. |
