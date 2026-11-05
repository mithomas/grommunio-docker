#!/usr/bin/env python3
"""
Verification Test: End-to-End Mail Pipeline & Spam History
1. A clean message sent to Exim (:2525) is scanned by Rspamd, delivered via LMTP into the mailbox,
   and recorded in the Admin Console Spam History with the right sender and recipient.
2. A GTUBE spam message is rejected during SMTP DATA, never reaches the mailbox,
   and increments Rspamd's 'reject' action counter (hard rejects are not stored in Spam History).
"""
import json
import smtplib
import sys
import urllib.request
import uuid

from env_helper import (admin_session, api, ensure_testuser, get_rspamd_port, load_env_secret,
                        mailbox_contains, send_mail, wait_until)

GTUBE = "XJS*C4JDBQADN1.NSBN3*2IDNEN*GTUBE-STANDARD-ANTI-UBE-TEST-EMAIL*C.34X"


def rspamd_rejects():
    req = urllib.request.Request(
        f"http://localhost:{get_rspamd_port()}/stat",
        headers={"Password": load_env_secret("RSPAMD_PASSWORD", "rspamd_secret")},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)["actions"]["reject"]


def history_row(session, subject):
    _, body = api("GET", "/system/antispam/history", session=session)
    rows = (body or {}).get("rows", [])
    return next((row for row in rows if row.get("subject") == subject), None)


def check_mail_pipeline():
    print("==> Checking Mail Pipeline & Spam History...")
    rcpt = ensure_testuser()
    try:
        session = admin_session()
    except RuntimeError as ex:
        print(f"    [-] {ex}", file=sys.stderr)
        return False

    # Clean message: accepted, delivered, scanned
    subject = send_mail(rcpt, "Pipeline")
    if not wait_until(lambda: mailbox_contains(rcpt, subject), 30):
        print(f"    [-] Clean message '{subject}' was not delivered to {rcpt}.", file=sys.stderr)
        return False
    print("    [+] Clean message delivered through Exim -> Rspamd -> LMTP into the mailbox.")

    row = wait_until(lambda: history_row(session, subject), 15)
    if not row:
        print(f"    [-] No Spam History entry for '{subject}'.", file=sys.stderr)
        return False
    if row.get("action") == "reject" or row.get("rcpt_smtp") != [rcpt] or not row.get("sender_smtp", "").startswith("verifier-"):
        print(f"    [-] Unexpected Spam History entry for the clean message: {row}", file=sys.stderr)
        return False
    print(f"    [+] Spam History records the clean message (action={row['action']}, score={row['score']}).")

    # GTUBE: rejected at DATA, never delivered
    spam_subject = f"Gtube-{uuid.uuid4().hex[:12]}"
    rejects_before = rspamd_rejects()
    try:
        send_mail(rcpt, subject=spam_subject, body=f"GTUBE test message\n{GTUBE}\n")
    except smtplib.SMTPDataError as err:
        if err.smtp_code != 550:
            print(f"    [-] Spam rejected with SMTP {err.smtp_code}, expected 550: {err.smtp_error!r}", file=sys.stderr)
            return False
        print(f"    [+] GTUBE message rejected during DATA ({err.smtp_code} {err.smtp_error.decode(errors='replace')}).")
    else:
        print("    [-] GTUBE message was accepted by Exim.", file=sys.stderr)
        return False

    if not wait_until(lambda: rspamd_rejects() == rejects_before + 1, 10):
        print(f"    [-] Rspamd reject counter did not increase by one (was {rejects_before}).", file=sys.stderr)
        return False
    if mailbox_contains(rcpt, spam_subject):
        print("    [-] Rejected spam message reached the mailbox.", file=sys.stderr)
        return False
    print("    [+] Rspamd counted exactly one reject and the message was not delivered.")
    return True


if __name__ == "__main__":
    if check_mail_pipeline():
        print("[+] SUCCESS: Mail pipeline, Rspamd scanning, and Spam History confirmed.\n")
        sys.exit(0)
    print("[-] FAILED: Mail pipeline verification failed.\n", file=sys.stderr)
    sys.exit(1)
