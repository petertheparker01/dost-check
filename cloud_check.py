"""
Cloud checker (GitHub Actions par chalti hai).

Kaam:
- PC on hai (PC ne haal hi me ping bheji) -> cloud kuch nahi karta.
- PC band hai -> cloud HL Gaming, Mobileverso, phir API se Last Login padhta hai.
  Naya login mile to phone me ntfy se "Dost online aaya!" (urgent) bhejta hai.

Do mode:
    python cloud_check.py precheck   -> PC on/off dekhta hai, GITHUB_OUTPUT me run=yes/no likhta hai
    python cloud_check.py run        -> asli check
"""

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

UID = os.environ.get("FF_UID", "")
TOPIC = os.environ.get("NTFY_TOPIC", "")
PC_TOPIC = TOPIC + "-pc"
STATE_FILE = "state.json"
PC_ALIVE_MIN = 25          # itne minute se zyada ping na aaye to PC band maana jayega
URL = "https://killarnation.com/tools/free-fire-id-checker/?uid=" + UID
URL_JORNAL = "https://freefirejornal.com/en/profile/" + UID + "/"
API_URL = "https://glob-info2.vercel.app/info?uid=" + UID
URL_MOBILEVERSO = "https://mobileverso.com.br/en/freefire/player/" + UID
URL_HL = "https://www.hlgamingofficial.com/p/free-fire-account-last-seenlogin.html"

JS_LAST = (
    "() => {"
    " const els = [...document.querySelectorAll('*')].filter("
    "  e => e.children.length === 0 && /^\\s*last\\s*login\\s*$/i.test(e.textContent));"
    " for (const e of els) {"
    "  const p = e.parentElement; if (!p) continue;"
    "  const t = p.innerText.replace(/last\\s*login/i, '').trim();"
    "  if (/\\d/.test(t)) return t;"
    " }"
    " return null; }"
)


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
TZ_HOURS = {"BST": 1, "UTC": 0, "GMT": 0, "IST": 5.5}


def parse_dt(s):
    """Last Login ke text se UTC datetime banata hai. Sab sites ke format samajhta hai. Na samjhe to None."""
    try:
        if not s:
            return None
        # hlgaming: "11/22/2026, 11:27:26 AM IST"  (mahina/tareekh/saal)
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?\s*([A-Za-z]+)?", s, re.I)
        if m:
            mo, d, y, hh, mm, ss, ap, tz = m.groups()
            month = int(mo)
        else:
            # killarnation: "14 Nov 2026 at 10:08:37 AM (BST)"
            m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+at\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)\s*\(?([A-Za-z]+)?\)?", s, re.I)
            if m:
                d, mon, y, hh, mm, ss, ap, tz = m.groups()
            else:
                # freefirejornal / mobileverso: "November 20, 2026, 10:37:59 UTC" ya "Nov 21, 2026, 8:09 PM IST"
                m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?\s*([A-Za-z]+)?", s, re.I)
                if not m:
                    return None
                mon, d, y, hh, mm, ss, ap, tz = m.groups()
            month = MONTHS.get(mon[:3].lower())
            if not month:
                return None
        hour = int(hh)
        if ap:
            hour = hour % 12 + (12 if ap.upper() == "PM" else 0)
        dt = datetime(int(y), month, int(d), hour, int(mm), int(ss or 0))
        offset = TZ_HOURS.get((tz or "").upper(), 0)
        return (dt - timedelta(hours=offset)).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def to_ist(s):
    """Last Login ko India time (IST) me badalta hai, taaki message me sahi time dikhe."""
    dt = parse_dt(s)
    if dt is None:
        return s
    ist = dt + timedelta(hours=5, minutes=30)
    return ist.strftime("%d %b %Y, %I:%M %p").lstrip("0") + " (India time)"


def is_newer(new, old):
    """True agar new Last Login old se naya hai. Purana (cached) time aaye to False (jhootha alert nahi)."""
    a, b = parse_dt(new), parse_dt(old)
    if a and b:
        return a > b
    return new != old


def need_env():
    if not UID or not TOPIC:
        print("FF_UID ya NTFY_TOPIC secret nahi mila.")
        sys.exit(1)


def notify(title, msg, priority="high"):
    try:
        requests.post(
            f"https://ntfy.sh/{TOPIC}",
            data=msg.encode("utf-8"),
            headers={"Title": title, "Priority": priority},
            timeout=20,
        )
    except Exception as e:
        print("ntfy error:", type(e).__name__)


def last_pc_ping():
    """PC ki sabse nayi ping (dict: time, message) ya None."""
    try:
        r = requests.get(f"https://ntfy.sh/{PC_TOPIC}/json?poll=1&since=12h", timeout=25)
    except Exception as e:
        print("ping padhne me error:", type(e).__name__)
        return "error"
