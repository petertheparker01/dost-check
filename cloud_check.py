"""
Cloud checker (GitHub Actions par chalti hai).

Kaam:
- PC on hai (PC ne haal hi me ping bheji) -> cloud kuch nahi karta.
- PC band hai -> cloud killarnation se dost ka Last Login padhta hai.
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
TZ_HOURS = {"BST": 6, "UTC": 0, "GMT": 0, "IST": 5.5}


def parse_dt(s):
    """Last Login ke text se UTC datetime banata hai. Dono site ke format samajhta hai. Na samjhe to None."""
    try:
        if not s:
            return None
        # killarnation: "14 Nov 2026 at 10:08:37 AM (BST)"
        m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+at\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)\s*\(?([A-Za-z]+)?\)?", s, re.I)
        if m:
            d, mon, y, hh, mm, ss, ap, tz = m.groups()
        else:
            # freefirejornal: "November 20, 2026, 10:37:59 UTC"
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
    last = None
    for line in r.text.splitlines():
        try:
            m = json.loads(line)
        except Exception:
            continue
        if m.get("event") == "message":
            last = m
    return last


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def pc_is_alive(ping):
    return bool(ping) and ping != "error" and (time.time() - ping["time"]) < PC_ALIVE_MIN * 60


def precheck():
    need_env()
    ping = last_pc_ping()
    if ping == "error":
        run = "no"            # ntfy se baat nahi ho paayi, galat alert se bachne ke liye ruk jao
    else:
        run = "no" if pc_is_alive(ping) else "yes"
    if run == "no":
        st = load_state()
        if st.get("pc_off_notified"):
            st["pc_off_notified"] = False      # PC wapas on hua, agli baar band hone par phir se message aayega
            save_state(st)
    print("PC on hai, cloud ruka." if run == "no" else "PC band lagta hai, cloud check karega.")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"run={run}\n")



def get_last_login_jornal():
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, timeout=30000)
        page = browser.new_page()
        try:
            page.set_default_timeout(20000)
            page.goto(URL_JORNAL, wait_until="domcontentloaded", timeout=30000)
            deadline = time.time() + 30
            value = None
            while time.time() < deadline:
                try:
                    body = page.inner_text("body")
                except Exception:
                    body = ""
                refreshing = "Showing the latest available copy" in body
                value = page.evaluate(JS_LAST)
                if value and not refreshing:
                    break
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass
                time.sleep(2)
            if not value:
                raise PWTimeout("Last login nahi mila")
            return re.sub(r"\s+", " ", value).strip()
        except Exception as e:
            print(f"freefirejornal fail: {type(e).__name__}")
            return None
        finally:
            browser.close()


def get_last_login_killar():
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, timeout=30000)
        page = browser.new_page()
        try:
            page.set_default_timeout(20000)
            page.goto(URL, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeout:
                pass
            box = page.locator(
                'input[type="text"], input[type="number"], input[type="tel"], input:not([type])'
            ).first
            box.wait_for(timeout=15000)
            if box.input_value().strip() != UID:
                for _ in range(4):
                    box.click()
                    box.fill("")
                    box.press_sequentially(UID, delay=60)
                    time.sleep(1)
                    if box.input_value().strip() == UID:
                        break
                else:
                    raise RuntimeError("UID box me nahi bhari")
            try:
                page.locator('button:has-text("Check ID")').first.click(timeout=8000)
            except Exception:
                box.press("Enter")

            deadline = time.time() + 25
            value = None
            while time.time() < deadline:
                value = page.evaluate(JS_LAST)
                if value:
                    break
                time.sleep(1)
            if not value:
                raise PWTimeout("Last Login box nahi mila")
            return re.sub(r"\s+", " ", value).strip()
        except Exception as e:
            captcha = False
            try:
                captcha = page.evaluate(
                    "() => !!document.querySelector("
                    "'iframe[src*=\"challenges.cloudflare\"], iframe[src*=\"turnstile\"], "
                    "iframe[src*=\"recaptcha\"], iframe[src*=\"hcaptcha\"], .cf-turnstile')"
                )
            except Exception:
                pass
            print(f"Check fail: {type(e).__name__} | captcha jaisa kuch dikha: {captcha}")
            return None
        finally:
            browser.close()


def get_last_login_api(ctx=None):
    """Seedha API se Last Login (bina browser, bina captcha). Unix timestamp ko padhne layak text me badalta hai."""
    try:
        r = requests.get(API_URL, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            print(f"API ne HTTP {r.status_code} diya")
            return None
        data = r.json()
        ts = (data.get("basicInfo") or {}).get("lastLoginAt")
        if not ts:
            print("API ke jawab me lastLoginAt nahi mila")
            return None
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return dt.strftime("%B %d, %Y, %H:%M:%S UTC")
    except Exception as e:
        print(f"API fail: {type(e).__name__}")
        return None


def get_last_login_mobileverso():
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, timeout=30000)
        ctx = browser.new_context(timezone_id="Asia/Kolkata", locale="en-US")   # site time browser ke timezone me dikhati hai
        page = ctx.new_page()
        try:
            page.set_default_timeout(20000)
            page.goto(URL_MOBILEVERSO, wait_until="domcontentloaded", timeout=40000)
            deadline = time.time() + 40
            value = None
            while time.time() < deadline:
                try:
                    body = page.inner_text("body")
                except Exception:
                    body = ""
                m = re.search(r"Last login\s+([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4},?\s*\d{1,2}:\d{2}\s*(?:AM|PM))", body, re.I)
                if m:
                    value = re.sub(r"\s+", " ", m.group(1)).strip() + " IST"
                    break
                time.sleep(2)
            if not value:
                raise PWTimeout("Last login nahi mila")
            return value
        except Exception as e:
            print(f"mobileverso fail: {type(e).__name__}")
            return None
        finally:
            browser.close()


def get_last_login():
    for name, fn in (("mobileverso", get_last_login_mobileverso), ("api", get_last_login_api)):   # pehle mobileverso, phir api
        value = fn()
        if value:
            print(f"Last Login {name} se mila.")
            return value
    return None


def run():
    need_env()
    ping = last_pc_ping()
    state = load_state()

    # Kya ye PC band hone ke baad cloud ka PEHLA check hai? (tab ek baar Last Login ka message jayega)
    first_off = isinstance(ping, dict) and not state.get("pc_off_notified")

    value = get_last_login()

    if not value:
        if first_off:
            notify("PC band lag raha hai",
                   "PC se ping nahi aa rahi. Cloud ne check karna shuru kiya, par abhi Last Login padh nahi paya.", "high")
            state["pc_off_notified"] = True
        save_state(state)
        return

    ping_known = None
    if isinstance(ping, dict):
        msg = (ping.get("message") or "").strip()
        ping_known = msg if msg and msg != "-" else None

    # "pichla" Last Login: jo naya hai wahi (cloud ka apna ya PC ka bataya hua)
    if state.get("value") and (not isinstance(ping, dict) or state.get("ts", 0) > ping["time"]):
        known = state["value"]
    else:
        known = ping_known or state.get("value")

    # PC band hote hi ek baar: dost ka abhi ka Last Login
    if first_off:
        notify("PC band lag raha hai",
               f"Dost ka abhi ka Last Login: {to_ist(value)}. Ab cloud check karega, naya login aate hi message aayega.", "high")
        state["pc_off_notified"] = True

    # Naya login mila (PC band hone ke dauran bhi) to alert
    if known is None:
        print("Pehli reading save hui.")
    elif is_newer(value, known):
        print("NAYA LOGIN mila, alert bheja.")
        notify("Dost online aaya!", f"Naya Last Login: {to_ist(value)} (cloud se)", "urgent")
    else:
        print("Koi naya login nahi.")

    # Sirf tab save karo jab kuch badla ho (ya 12 ghante se save nahi hua), taaki commit baar-baar na bane
    best = value if (known is None or is_newer(value, known)) else known
    if state.get("value") != best or time.time() - state.get("ts", 0) > 43200:
        state["value"] = best
        state["ts"] = time.time()
    save_state(state)


def _hard_stop():
    print("Time limit khatam, run band kiya.")
    os._exit(0)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    t = threading.Timer(210, _hard_stop)
    t.daemon = True
    t.start()
    if mode == "precheck":
        precheck()
    else:
        run()
