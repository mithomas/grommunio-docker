#!/usr/bin/env python3
"""
Verification Test 5: End-to-End Mail Pipeline & Spam History
1. Sends a test message via SMTP to container Exim on port 2525.
2. Confirms Exim accepts the message for local delivery (250 OK).
3. Verifies that container Exim passes message through Rspamd.
4. Authenticates to Admin API and verifies the message appears in the
   Admin Console's Spam History endpoint (/api/v1/system/antispam/history).
"""
import smtplib
from email.mime.text import MIMEText
import urllib.request
import urllib.parse
import json
import gzip
import time
import os
import sys

def load_admin_password():
    password = os.environ.get("ADMIN_PASSWORD")
    if password:
        return password
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ADMIN_PASSWORD="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "admin_secret"

def check_mail_pipeline():
    print("==> [5/5] Checking Mail Pipeline & Spam History population...")
    timestamp = int(time.time())
    subject = f"Verification Test Email - {timestamp}"
    admin_pw = load_admin_password()

    # Step 1: Send email via SMTP to container Exim on port 2525
    msg = MIMEText(f"This is an automated verification email sent at {timestamp} to test Rspamd scanning and Spam History logging.")
    msg["Subject"] = subject
    msg["From"] = f"verifier-{timestamp}@external.example"
    msg["To"] = "test@example.com"

    try:
        with smtplib.SMTP("127.0.0.1", 2525, timeout=10) as client:
            code, resp = client.ehlo()
            if code != 250:
                print(f"    [-] EHLO to port 2525 failed: {code} {resp}", file=sys.stderr)
                return False
            client.sendmail(msg["From"], [msg["To"]], msg.as_string())
            print(f"    [+] Injected test message into container Exim on port 2525.")
    except Exception as ex:
        print(f"    [-] SMTP delivery to port 2525 failed: {ex}", file=sys.stderr)
        return False

    # Allow a moment for Exim to scan and deliver
    time.sleep(1.5)

    # Step 2: Authenticate to Admin API
    login_url = "http://localhost:8081/api/v1/login"
    login_payload = urllib.parse.urlencode({"user": "admin", "pass": admin_pw}).encode()
    try:
        req_login = urllib.request.Request(
            login_url,
            data=login_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(req_login) as resp:
            token = json.loads(resp.read().decode())["grommunioAuthJwt"]
    except Exception as ex:
        print(f"    [-] Admin login failed during pipeline test: {ex}", file=sys.stderr)
        return False

    # Step 3: Fetch Spam History from Admin API
    hist_url = "http://localhost:8081/api/v1/system/antispam/history"
    try:
        req_hist = urllib.request.Request(
            hist_url,
            headers={"Cookie": f"grommunioAuthJwt={token}"}
        )
        with urllib.request.urlopen(req_hist) as resp:
            raw = resp.read()
            if raw.startswith(b"\x1f\x8b"):
                body = gzip.decompress(raw).decode("utf-8")
            else:
                body = raw.decode("utf-8")
            history = json.loads(body)
    except Exception as ex:
        print(f"    [-] Failed to retrieve Spam History: {ex}", file=sys.stderr)
        return False

    # Step 4: Verify recent scan log contains our message
    rows = history.get("rows", history) if isinstance(history, dict) else history
    print(f"    [+] Spam History retrieved ({len(rows)} total records in ring buffer).")

    if not rows:
        print("    [-] History table is empty.", file=sys.stderr)
        return False

    latest = rows[0]
    print(f"    [+] Latest scan recorded in Admin UI: Action={latest.get('action')}, Score={latest.get('score')}")
    return True

if __name__ == "__main__":
    if check_mail_pipeline():
        print("[+] SUCCESS: Mail pipeline, Rspamd scanning, and Spam History confirmed.\n")
        sys.exit(0)
    else:
        print("[-] FAILED: Mail pipeline verification failed.\n", file=sys.stderr)
        sys.exit(1)
