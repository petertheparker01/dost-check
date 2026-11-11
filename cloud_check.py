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
import time

import requests

UID = os.environ.get("FF_UID", "")
TOPIC = os.environ.get("NTFY_TOPIC", "")
PC_TOPIC = TOPIC + "-pc"
STATE_FILE = "state.json"
PC_ALIVE_MIN = 25          # itne minute se zyada ping na aaye to PC band maana jayega
URL = "https://killarnation.com/tools/free-fire-id-checker/?uid=" + UID

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
    print("PC on hai, cloud ruka." if run == "no" else "PC band lagta hai, cloud check karega.")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"run={run}\n")


def get_last_login():
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                pass
            box = page.locator(
                'input[type="text"], input[type="number"], input[type="tel"], input:not([type])'
            ).first
            box.wait_for(timeout=20000)
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

            deadline = time.time() + 40
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


def run():
    need_env()
    ping = last_pc_ping()
    state = load_state()

    # PC band hone ki pehli khabar (sirf tab jab PC pehle kabhi ping bhej chuka ho)
    if isinstance(ping, dict) and not state.get("pc_off_notified"):
        notify("PC band lag raha hai",
               "PC se ping nahi aa rahi. Ab cloud dost ka login check karega.", "high")
        state["pc_off_notified"] = True

    value = get_last_login()
    if not value:
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

    if known is None:
        print("Pehli reading save hui.")
    elif value != known:
        print("NAYA LOGIN mila, alert bheja.")
        notify("Dost online aaya!", f"Naya Last Login: {value} (cloud se)", "urgent")
    else:
        print("Koi naya login nahi.")

    state["value"] = value
    state["ts"] = time.time()
    save_state(state)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "precheck":
        precheck()
    else:
        run()
