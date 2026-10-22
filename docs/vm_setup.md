# Running Finnbot on a VM

Everything to move Finnbot from the Windows PC to an always-on Linux VM, step by step. Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-23, at Andres's request. Monitoring and backups are covered in more depth in [operations.md](operations.md); the commands are in [admin_handbook.md](admin_handbook.md).

Until the VM runs, nothing in Finnbot happens on a schedule: no hourly digest runs (so no digests, earnings reminders, sign-up sync, or settings sync), no watchdog, and no backups.

---

## 0. Do you need a paid VM? No

**Recommendation: Oracle Cloud's Always Free tier, an Ampere A1 (Arm) VM.** The free allowance (checked 2026-10-23 on [Oracle's Always Free page](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm)) is up to 2 CPUs (OCPUs) and 12 GB of memory, 200 GB of disk, and 10 TB of outbound traffic a month. Finnbot needs a fraction of that: one Python process at a time, mostly waiting on APIs, about 1.4 MB of state. Your `.env` already has an Oracle login, so you have an account.

**One catch, and the fix.** Oracle may reclaim an Always Free VM it considers idle: over 7 days, CPU under 20% at the 95th percentile, network under 20%, and (on A1) memory under 20%. Finnbot works in short hourly bursts, so it would look idle.
- **The fix:** upgrade the account to **Pay As You Go** (step 1.2). Oracle still charges nothing for Always Free resources after the upgrade, only for use beyond them. It also lifts the "out of host capacity" errors free accounts often hit when creating A1 VMs.
- **Guard against surprises:** set a budget alert of $1 a month.
- Oracle's 2023 notice said a Pay As You Go account isn't reclaimed; its current page doesn't say either way. If the VM is ever stopped anyway, the healthchecks.io alerts (part 7) tell you within the hour, and the Drive backup (part 8) restores the state.

**Not recommended:**
- Oracle's free AMD "Micro" VM: 1 GB of memory and 1/8 of a CPU, too tight for the idea screen.
- Google Cloud's free e2-micro: also 1 GB, US regions only, and its public IPv4 address may be billed.

**If Oracle is a hassle:** a small paid VPS works the same way (2 GB of memory or more, Ubuntu 24.04), e.g. Hetzner's CX22 at about €4 a month or a $6 DigitalOcean droplet. Follow the same steps from part 2 on; the user is `root` or the one the provider gives you instead of `ubuntu`.

---

## 1. Create the VM (Oracle console, about 15 minutes)

### 1.1 An SSH key on your PC

In PowerShell on the PC:

```powershell
ssh-keygen -t ed25519 -f $HOME\.ssh\finnbot_vm -C finnbot-vm
```

Press Enter twice for no passphrase (or set one; Windows' ssh-agent can remember it). This makes `finnbot_vm` (private, stays on the PC) and `finnbot_vm.pub` (public, goes to Oracle).

### 1.2 Upgrade to Pay As You Go, and a budget alert

1. Sign in at [cloud.oracle.com](https://cloud.oracle.com). Note your **home region** (top bar): free VMs can only be created there.
2. Open the menu → **Billing & Cost Management** → **Upgrade and Manage Payment** → **Upgrade to Pay As You Go**. It uses the card from sign-up and can take a few minutes to a day to complete.
3. **Billing & Cost Management** → **Budgets** → **Create Budget**: target your tenancy (root compartment), $1 a month, an alert at 100% of actual spend, sent to your email.

### 1.3 The instance

**Compute** → **Instances** → **Create instance**:

| Field | Choose |
|---|---|
| Name | `finnbot` |
| Placement | Any availability domain (try another if one says "Out of host capacity") |
| Image | **Canonical Ubuntu 24.04** (the Arm build is picked automatically for A1) |
| Shape | **Ampere** → **VM.Standard.A1.Flex**, **2 OCPUs, 12 GB memory** (the whole free allowance; 1 OCPU / 6 GB also works) |
| Networking | Create a new virtual cloud network and a **public subnet**; **Assign a public IPv4 address**: yes |
| SSH keys | **Paste public key**: the contents of `$HOME\.ssh\finnbot_vm.pub` (`Get-Content $HOME\.ssh\finnbot_vm.pub`) |
| Boot volume | The default (about 47-50 GB) |

Create it, wait for **Running**, and copy its **Public IP address**.

The default security list only lets SSH (port 22) in, which is all Finnbot needs: it only makes outgoing connections (APIs, Gmail on port 587, Telegram). Don't open any other port.

### 1.4 Connect

```powershell
ssh -i $HOME\.ssh\finnbot_vm ubuntu@<public IP>
```

Answer `yes` to the fingerprint question the first time. Everything in parts 2-8 runs in this session unless it says "on the PC".

---

## 2. Prepare Ubuntu (about 5 minutes)

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install git build-essential curl unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades   # Yes: security updates install themselves
timedatectl                                       # "Time zone: Etc/UTC" (keep UTC; the crontab times assume it)

# A 2 GB swap file, a safety net if a run ever needs more memory
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h                                           # Shows the memory and the swap
```

If `apt upgrade` asks to restart services or mentions a new kernel, accept the defaults, then `sudo reboot` and reconnect.

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

## 4. Python (about 5 minutes)

Finnbot is developed on Python 3.14, so the VM uses the same version, installed with `uv` (Ubuntu 24.04's own Python is 3.12):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
cd ~/ai_financial_advisor
uv python install 3.14
uv venv --python 3.14 venv                                  # The scripts expect venv/bin/python
uv pip install --python venv/bin/python -r requirements-dev.txt   # requirements.txt plus pytest
venv/bin/python --version                                   # Python 3.14.x
```

On an Arm VM almost every package installs ready-made; any that has to be built uses `build-essential` from part 2. If one still fails, copy the error into a Claude session.

---

## 5. Secrets and state (about 10 minutes)

### 5.1 Copy from the PC

Stop anything Finnbot is doing on the PC first (no digest or watchdog running), so the state you copy is final. Then, **on the PC**, in PowerShell:

```powershell
$ip = "<public IP>"; $key = "$HOME\.ssh\finnbot_vm"; $repo = "C:\Users\Andre\Documents\git\ai_financial_advisor"
# The service account's JSON file: its path is GOOGLE_APPLICATION_CREDENTIALS in the PC's .env
$json = ((Get-Content "$repo\.env" | Select-String '^GOOGLE_APPLICATION_CREDENTIALS=').Line -split '=', 2)[1].Trim('"', ' ')
scp -i $key "$repo\.env" "ubuntu@${ip}:~/ai_financial_advisor/.env"
scp -i $key "$json" "ubuntu@${ip}:~/finnbot-service.json"
scp -i $key -r "$repo\data" "ubuntu@${ip}:~/ai_financial_advisor/"
```

`scp` sends them encrypted over SSH. The `data/` folder (about 1.4 MB) carries on the PC's state: who already received today's digest, the watchdog's last reviews, the track record, the API usage ledger, the holdings bought from ideas, and the accepted settings.

### 5.2 Adjust them on the VM

```bash
cd ~/ai_financial_advisor
chmod 600 .env ~/finnbot-service.json
sed -i 's/\r$//' .env                                       # Windows line endings to Linux (harmless either way)
sed -i "s|^GOOGLE_APPLICATION_CREDENTIALS=.*|GOOGLE_APPLICATION_CREDENTIALS=$HOME/finnbot-service.json|" .env
sed -i '/^UNIVERSAL_LLM_MODEL=/d;/^UNIVERSAL_LLM_EFFORT=/d' .env   # So the Parameters tab decides the ideas' model
grep -E '^(GOOGLE_APPLICATION_CREDENTIALS|UNIVERSAL_LLM)' .env     # Shows the new path, and no UNIVERSAL_LLM lines
```

`.env` holds every key: never print it whole, and keep it out of git (it already is). The PC's copy can stay, for manual test runs; keep both in step when a key changes.

---

## 6. Test before scheduling (about 10 minutes)

```bash
cd ~/ai_financial_advisor
venv/bin/python -m src.check_config                      # Every required setting OK; fix anything MISSING or INVALID
venv/bin/python -m pytest                                # About 380 tests, a minute, offline
venv/bin/python -m src.remote_settings --sync            # Reads the Parameters tab: proves the service account works
venv/bin/python src/digest/digest_payload.py --check     # Says whether a digest is due and for whom; sends nothing
venv/bin/python src/telegram_alert/hourly_watchdog.py --dry-run   # Which users and stocks it would check; sends nothing
```

Then one full test, which costs about $0.20 and proves the market data, the LLMs, Gmail, and the email layout all work from the VM:

```bash
venv/bin/python src/digest/digest_payload.py --payload-only --period Post-Market
ls -t test_output/digest_payloads | head -1              # The payload it just saved
venv/bin/python src/digest/digest_payload.py --from-payload test_output/digest_payloads/<that file>
```

A `[Test]` digest should reach your inbox within a minute of the last command.

---

## 7. Monitoring: healthchecks.io (about 10 minutes)

`HEALTHCHECKS_PING_KEY` is already in `.env`, so each cron job creates its check on its first ping. Full walkthrough: [operations.md](operations.md), "Your steps: healthchecks.io".

1. If not done yet: sign up at [healthchecks.io](https://healthchecks.io) (free plan), add the **Telegram** integration, and create the project's **Ping key** (Settings). It must match `HEALTHCHECKS_PING_KEY`.
2. After part 9 has run each job once, set each check's schedule (click it → **Change Schedule**):

   | Check | Period | Grace |
   |---|---|---|
   | `finnbot-watchdog` | 1 hour | 45 minutes |
   | `finnbot-digest` | 1 hour | 45 minutes |
   | `finnbot-backup` | 1 day | 2 hours |

3. Test an alert: `curl -fsS https://hc-ping.com/<ping key>/finnbot-watchdog/fail` should reach Telegram; the next real run turns it green.

---

## 8. Backups to Google Drive (about 15 minutes)

Nightly, encrypted, 90 days of previous versions, with rclone. Full walkthrough: [operations.md](operations.md), "Your steps: rclone backups to Google Drive". In short:

1. On the VM: `sudo -v ; curl https://rclone.org/install.sh | sudo bash`
2. On the PC: `winget install Rclone.Rclone` (for the one-time Google sign-in).
3. On the VM: `rclone config` → new remote `gdrive`, storage `drive`, scope **`drive.file`**, no web browser; run the `rclone authorize ...` command it prints **on the PC**, sign in, paste the token back.
4. `rclone config` again → new remote `finnbot-backup`, storage `crypt`, remote `gdrive:finnbot-backup`, standard filename encryption, encrypted directory names, generated passwords. **Save both passwords in your password manager**: without them the backups can't be decrypted.
5. Test: `bash scripts/backup_to_drive.sh`, then `rclone ls finnbot-backup:current`.

The run logs are also copied to a `finnbot-logs` folder in your Drive each night, readable on your phone (not encrypted unless you set `FINNBOT_LOGS_REMOTE=finnbot-backup:logs`).

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

Save and exit (nano: Ctrl+O, Enter, Ctrl+X). `crontab -l` shows them.

- **The digest line runs every hour.** Finnbot decides from the exchange calendars when a Pre-Market (from 07:00 ET) or Post-Market (16:10 ET) digest is due, so daylight saving needs nothing. Every run also does the settings and sign-up syncs, earnings reminders, the holiday notice, and the monthly scorecards.
- **The backup at 02:30 UTC** is after the Post-Market window has closed.
- **The shared lock:** all three jobs take one lock, `/tmp/finnbot.lock`, so they never overlap. Each waits up to 15 minutes for it.

**From now on the VM is the only scheduled runner.** Make sure the PC isn't also scheduled:

```powershell
schtasks /query /fo LIST | Select-String -Pattern "finnbot|digest_payload|hourly_watchdog"
```

It should print nothing. Manual test runs on the PC stay fine. Each `--payload-only` run also syncs the Parameters tab from the PC's `parameters.py`, so keep the PC's copy up to date (`git pull`).

---

## 10. The first day

- **Within the hour:** `cat ~/ai_financial_advisor/logs/digest.status` and `logs/watchdog.status` show one line per run; the healthchecks.io checks turn green.
- **At 07:10 ET and 16:20 ET** (allow up to 30 minutes): the digests arrive.
- **Telegram:** earnings reminders and any alerts as before.
- **After the first Pre-Market and Post-Market digests:** check the news cost. Ask Claude to "confirm the news cost"; it will read the usage ledger.

---

## 11. Day to day

| Task | Command (on the VM) |
|---|---|
| Update after a change is merged on GitHub | `cd ~/ai_financial_advisor && git pull` (plus `uv pip install --python venv/bin/python -r requirements-dev.txt` when `requirements.txt` changed). Takes effect at the next cron run; nothing needs restarting. A change in `parameters.py` reaches the Parameters tab at the next digest run. |
| Follow a run | `tail -f logs/digest.log` (or `logs/watchdog.log`); Ctrl+C to stop |
| Last run's result | `cat logs/digest.status logs/watchdog.status logs/backup.status` |
| Memory and disk | `free -h`, `df -h /` |
| Settings in force | `venv/bin/python -m src.remote_settings --show` |
| Add a new user's keys | `venv/bin/python -m src.import_signup <name> --enter-keys` |
| Any command from the handbook | the same as on the PC, with `venv/bin/python` |
| Reboot | `sudo reboot`; cron and swap come back by themselves |

Security updates install themselves (part 2). A monthly `sudo apt update && sudo apt -y upgrade` catches the rest.

---

## 12. When something goes wrong

| Symptom | What to do |
|---|---|
| healthchecks.io says a check is late | `ssh` in: is the VM up? Then `crontab -l`, and `tail -50 logs/digest.log`. Exit code 75 means a job waited 15 minutes for the lock: another run may be stuck (`ps aux | grep python`). |
| The VM is stopped or gone (Oracle reclaimed it, or it broke) | Start it in the console if it exists. Otherwise create a new one (parts 1-4), restore the state with `rclone copy finnbot-backup:current/data data` after recreating both rclone remotes with the **same** passwords (part 8), copy `.env` and the JSON again (part 5), and add the crontab (part 9). |
| `Permission denied (publickey)` on `git pull` | The deploy key or `~/.ssh/config` (part 3). |
| Emails stop, the log says "Username and Password not accepted" | The Gmail app password (`GMAIL_APP_PASSWORD`) was revoked or changed: create a new one for the digest account and update `.env`. |
| The settings sync fails with 403 | The sign-up spreadsheet must stay shared with the service account as an **Editor**. |
| Memory warnings, or runs killed | `free -h`; with 6 GB or more plus swap this shouldn't happen. Check `dmesg | tail` for "Out of memory". |
| `out of host capacity` when creating the VM | Another availability domain, retry later, or finish the Pay As You Go upgrade (part 1.2). |
