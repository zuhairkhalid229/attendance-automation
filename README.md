# Attendance automation

Clocks in and out automatically every day via GitHub Actions. Check-in and
check-out each fire at a random moment inside a configurable window, so the
times vary day to day.

## Commands

- `python attend.py checkin`  — waits a random time in the check-in window, then checks in.
- `python attend.py checkout` — waits a random time in the checkout window, submits the daily note, then checks out.
- `python attend.py status`   — prints today's state. Reads only; writes nothing.

## Setup on GitHub Actions

1. Push this folder to a repo.
2. Repo → **Settings → Secrets and variables → Actions** → add two secrets:
   - `ATT_EMAIL`
   - `ATT_PASSWORD`
3. The workflow in [.github/workflows/attendance.yml](.github/workflows/attendance.yml)
   runs on schedule. Edit the windows and times in its `env:` block.

### Test without waiting

Repo → **Actions → Attendance → Run workflow** → choose `status` with
`no_sleep = true`. It only reads, so it's safe to run anytime.

## Timing

GitHub cron is **UTC**; the workflow sets `TZ=Asia/Karachi` so the windows are
read in local time. Hosted runners can start 10–30 minutes late, which is fine
for a loose window.

## Local use (optional)

```bash
pip install -r requirements.txt
cp .env.example .env     # fill in your values
python attend.py status
```

`.env` is gitignored — keep credentials out of commits.
