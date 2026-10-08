#!/usr/bin/env python3
"""
Attendance automation for attendance.collsol.com (direct REST API).

Usage:
    python attend.py checkin      # log in + check in (optionally sleeps a random delay first)
    python attend.py checkout     # log in + submit report + check out
    python attend.py status       # print today's attendance state (dry run, no writes)

Config comes from a .env file next to this script (see .env.example).
Times are randomized inside a window so check-in/out never lands on the same minute.

NOTE: the three request bodies in the `API` section below are my best inference
from the app's endpoints. Confirm them against the HAR capture (see README) and
tweak if a field name differs — that is the only part Option B depends on.
"""

import os
import sys
import json
import time
import random
import subprocess
import datetime as dt
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
BASE = "https://attendance.collsol.com/api"


# ----------------------------------------------------------------------------
# config
# ----------------------------------------------------------------------------
def load_env():
    """Minimal .env loader (no dependency on python-dotenv)."""
    env = {}
    path = HERE / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    # environment variables win over the file (useful for GitHub Actions secrets)
    for k in ("ATT_EMAIL", "ATT_PASSWORD",
              "CHECKIN_START", "CHECKIN_END",
              "CHECKOUT_START", "CHECKOUT_END",
              "SKIP_WEEKENDS", "REPORT_TEXT", "REPORT_FROM_GIT", "GIT_REPO"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


CFG = load_env()


def cfg(key, default=None):
    return CFG.get(key, default)


# ----------------------------------------------------------------------------
# API  --  endpoints confirmed from the HAR capture (2026-10-08)
#
# This account is a REMOTE / TIMER employee. Attendance is a real-time timer,
# not a date-stamped record:
#   - POST /attendance/remote-timer-sync  (empty body) -> checks in at the
#     SERVER's current time. Returns action "checked_in" or "report_required".
#   - POST /attendance/remote-timer-logout-checkout (empty body) -> checks out
#     at the server's current time.
#   - PUT  /attendance/{id}/report  {"report": "..."} -> the "what was done".
# The server stamps its own clock; there is NO timestamp field to pass. So the
# only way to land a check-in at ~23:00 is to actually call sync at ~23:00.
# ----------------------------------------------------------------------------
class API:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://attendance.collsol.com",
            "Referer": "https://attendance.collsol.com/",
            "User-Agent": "Mozilla/5.0 (attendance-automation)",
        })
        self.token = None
        self.employee_id = None

    def login(self):
        email = cfg("ATT_EMAIL")
        password = cfg("ATT_PASSWORD")
        if not email or not password:
            raise SystemExit("ATT_EMAIL / ATT_PASSWORD missing from .env")
        r = self.s.post(f"{BASE}/auth/login",
                        json={"email": email, "password": password})
        r.raise_for_status()
        data = r.json()
        nested = data.get("data") or {}
        self.token = data.get("token") or nested.get("token")
        if not self.token:
            raise SystemExit(f"Login OK but no token in response: {data}")
        user = data.get("user") or nested.get("user") or {}
        self.employee_id = user.get("employee_id")
        self.s.headers["Authorization"] = f"Bearer {self.token}"
        return data

    def today_status(self):
        r = self.s.get(f"{BASE}/attendance/today-status/{self.employee_id}")
        r.raise_for_status()
        return r.json()

    def timer_sync(self):
        """Check in (or report what the server wants). Empty body is correct."""
        r = self.s.post(f"{BASE}/attendance/remote-timer-sync")
        r.raise_for_status()
        return r.json()

    def submit_report(self, attendance_id, text):
        r = self.s.put(f"{BASE}/attendance/{attendance_id}/report",
                       json={"report": text})
        r.raise_for_status()
        return r.json()

    def logout_checkout(self):
        r = self.s.post(f"{BASE}/attendance/remote-timer-logout-checkout")
        r.raise_for_status()
        return r.json()


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def is_weekend():
    return dt.date.today().weekday() >= 5  # 5=Sat, 6=Sun


def random_delay(start_hhmm, end_hhmm):
    """Sleep until a random moment inside [start, end] of today, if we're before it."""
    if not start_hhmm or not end_hhmm:
        return
    if cfg("NO_SLEEP", "").lower() in ("1", "true", "yes"):
        print("NO_SLEEP set -> acting immediately"); return
    now = dt.datetime.now()
    sh, sm = map(int, start_hhmm.split(":"))
    eh, em = map(int, end_hhmm.split(":"))
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    if end < start:                      # window crosses midnight
        end += dt.timedelta(days=1)
    target = start + dt.timedelta(seconds=random.randint(0, int((end - start).total_seconds())))
    wait = (target - dt.datetime.now()).total_seconds()
    if wait > 0:
        print(f"[{now:%H:%M}] sleeping {int(wait//60)} min -> acting at {target:%H:%M}")
        time.sleep(wait)


def git_report(repo, since_hours=24):
    """Draft the 'what was done' text from the last day's commits."""
    if not repo or not Path(repo).exists():
        return None
    try:
        out = subprocess.check_output(
            ["git", "-C", repo, "log", f"--since={since_hours} hours ago",
             "--pretty=- %s"], text=True).strip()
        return out or None
    except Exception:
        return None


def report_text():
    if cfg("REPORT_FROM_GIT", "").lower() in ("1", "true", "yes"):
        g = git_report(cfg("GIT_REPO"))
        if g:
            return g
    return cfg("REPORT_TEXT", "Worked on assigned development tasks.")


def extract_id(obj):
    for k in ("attendance_id", "id", "_id", "attendanceId"):
        if isinstance(obj, dict) and obj.get(k):
            return obj[k]
    if isinstance(obj, dict) and isinstance(obj.get("data"), dict):
        return extract_id(obj["data"])
    return None


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------
def do_checkin():
    if cfg("SKIP_WEEKENDS", "").lower() in ("1", "true", "yes") and is_weekend():
        print("weekend -> skip"); return
    random_delay(cfg("CHECKIN_START"), cfg("CHECKIN_END"))
    api = API(); api.login()
    res = api.timer_sync()
    data = res.get("data") or {}
    # The server refuses check-in while a previous day's report is pending.
    if data.get("action") == "report_required":
        for p in data.get("pending_reports", []):
            api.submit_report(p["id"], report_text())
            print(f"submitted pending report for {p.get('date')} (id {p['id']})")
        res = api.timer_sync()          # retry now that reports are in
        data = res.get("data") or {}
    print("check-in:", res.get("message"), "| id", data.get("attendance_id"))


def do_checkout():
    if cfg("SKIP_WEEKENDS", "").lower() in ("1", "true", "yes") and is_weekend():
        print("weekend -> skip"); return
    random_delay(cfg("CHECKOUT_START"), cfg("CHECKOUT_END"))
    api = API(); api.login()
    status = (api.today_status().get("data") or {})
    att_id = extract_id(status)
    if att_id:
        try:
            api.submit_report(att_id, report_text())
            print("report submitted for id", att_id)
        except Exception as e:
            print("report failed (non-fatal):", e)
    res = api.logout_checkout()
    print("check-out:", res.get("message") or json.dumps(res)[:200])


def do_status():
    api = API(); api.login()
    print("employee_id:", api.employee_id)
    print(json.dumps(api.today_status(), indent=2)[:1500])


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"checkin": do_checkin,
     "checkout": do_checkout,
     "status": do_status}.get(cmd, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
