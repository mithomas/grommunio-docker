#!/usr/bin/env python3
"""
Verification Test: ClamAV Antivirus Pipeline, Quarantine to Junk & Outbound Malware Protection
1. Supervisor daemons 'clamav-daemon' and 'clamav-freshclam' report RUNNING, and
   '/run/clamav/clamd.ctl' responds PONG to PING.
2. Inbound EICAR test message sent to Exim (:2525) is:
   - Accepted at SMTP (250 OK) without generating backscatter,
   - Tagged with '[VIRUS] ' Subject prefix and 'X-Virus-Status: Infected',
   - Delivered by Gromox LDA into the recipient's Junk E-mail folder (parent_fid == 23, not Inbox 13),
   - Recorded in the Admin UI Spam History (/system/antispam/history),
   - Ignored by grommunio-bayes-sync.py (recorded as 'virus', not fed to rspamc learn_spam).
3. Outbound EICAR test message submitted from container localhost (127.0.0.1:2525) to an
   external recipient is rejected with SMTP 550 so malware never leaves the server.
4. Clean inbound messages land in Inbox (parent_fid == 13) without '[VIRUS] ' prefix.
"""
import sys
import uuid

from env_helper import (admin_session, api, container_exec, ensure_testuser,
                        mailbox_dir, send_mail, wait_until)

# Constructed in two halves so host file scanners do not quarantine this test script.
EICAR_PART1 = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-"
EICAR_PART2 = r"STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
EICAR = EICAR_PART1 + EICAR_PART2

FOLDER_INBOX = 13
FOLDER_JUNK = 23


def check_clamd_health() -> bool:
    print("    [>] 1. Verifying clamav-daemon and clamav-freshclam services and socket...")
    if not wait_until(
        lambda: container_exec("supervisorctl", "status", "clamav-daemon", "clamav-freshclam").stdout.count("RUNNING") == 2,
        30,
        1.0,
    ):
        res = container_exec("supervisorctl", "status", "clamav-daemon", "clamav-freshclam")
        print(f"    [-] ClamAV services are not RUNNING:\n{res.stdout}{res.stderr}", file=sys.stderr)
        return False

    ping_script = """
import socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(5.0)
s.connect("/run/clamav/clamd.ctl")
s.sendall(b"PING\\n")
print(s.recv(32).decode().strip())
"""
    if not wait_until(lambda: container_exec("python3", "-c", ping_script).stdout.strip() == "PONG", 30, 1.0):
        print("    [-] clamd Unix socket /run/clamav/clamd.ctl did not respond PONG to PING.", file=sys.stderr)
        return False
    print("    [+] clamav-daemon and clamav-freshclam are RUNNING; clamd socket answered PONG.")
    return True


def message_details(rcpt: str, subject_token: str):
    """Returns (message_id, parent_fid, normalized_subject, mid_string) or None."""
    mdir = mailbox_dir(rcpt)
    if not mdir:
        return None
    query = """
import json, sqlite3, sys
db, token = sys.argv[1:3]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
row = c.execute(
    "SELECT m.message_id, m.parent_fid, p.propval, m.mid_string "
    "FROM message_properties p JOIN messages m USING (message_id) "
    "WHERE p.proptag = 236781599 AND m.is_deleted = 0 AND p.propval LIKE ?",
    ("%" + token + "%",),
).fetchone()
if row:
    print(json.dumps(list(row)))
"""
    res = container_exec("python3", "-c", query, f"{mdir}/exmdb/exchange.sqlite3", subject_token)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    import json
    return json.loads(res.stdout.strip())


def history_row(session, subject):
    _, body = api("GET", "/system/antispam/history", session=session)
    rows = (body or {}).get("rows", [])
    return next((row for row in rows if subject in (row.get("subject") or "")), None)


def check_clamav_pipeline() -> bool:
    print("==> Checking ClamAV Antivirus Pipeline, Junk Quarantine & Outbound Protection...")
    if not check_clamd_health():
        return False

    rcpt = ensure_testuser()
    try:
        session = admin_session()
    except RuntimeError as ex:
        print(f"    [-] {ex}", file=sys.stderr)
        return False

    # 2. Clean inbound message must land in Inbox (parent_fid == 13) without [VIRUS] prefix
    print("    [>] 2. Sending clean inbound message and verifying Inbox delivery...")
    clean_subj = send_mail(rcpt, "ClamClean", body="Clean message body without any malware.")
    clean_info = wait_until(lambda: message_details(rcpt, clean_subj), 30, 0.5)
    if not clean_info:
        print(f"    [-] Clean message '{clean_subj}' was not delivered.", file=sys.stderr)
        return False
    _, clean_fid, clean_stored_subj, _ = clean_info
    if clean_fid != FOLDER_INBOX:
        print(f"    [-] Clean message landed in folder {clean_fid}, expected Inbox ({FOLDER_INBOX}).", file=sys.stderr)
        return False
    if "[VIRUS]" in clean_stored_subj:
        print(f"    [-] Clean message unexpectedly received [VIRUS] prefix: {clean_stored_subj!r}", file=sys.stderr)
        return False
    print(f"    [+] Clean message delivered to Inbox (fid={clean_fid}) with unmodified subject '{clean_stored_subj}'.")

    # 3. Inbound EICAR virus message -> quarantined to Junk (fid 23) with '[VIRUS] ' prefix
    print("    [>] 3. Sending inbound EICAR test message and verifying Junk quarantine & '[VIRUS] ' prefix...")
    virus_subj = f"EicarIn-{uuid.uuid4().hex[:12]}"
    send_mail(rcpt, subject=virus_subj, body=f"Attached test file:\n{EICAR}\n")

    virus_info = wait_until(lambda: message_details(rcpt, virus_subj), 30, 0.5)
    if not virus_info:
        print(f"    [-] Quarantined EICAR message '{virus_subj}' did not arrive in mailbox.", file=sys.stderr)
        return False
    virus_mid, virus_fid, virus_stored_subj, virus_mid_str = virus_info
    if virus_fid != FOLDER_JUNK:
        print(f"    [-] EICAR message landed in folder {virus_fid}, expected Junk ({FOLDER_JUNK}).", file=sys.stderr)
        return False
    if not virus_stored_subj.startswith("[VIRUS] ") or virus_subj not in virus_stored_subj:
        print(f"    [-] Expected stored subject '[VIRUS] {virus_subj}', got {virus_stored_subj!r}.", file=sys.stderr)
        return False

    mdir = mailbox_dir(rcpt)
    eml_check = container_exec("grep", "-F", "X-Virus-Status: Infected", f"{mdir}/eml/{virus_mid_str}")
    if eml_check.returncode != 0:
        print("    [-] Quarantined message is missing 'X-Virus-Status: Infected' header.", file=sys.stderr)
        return False
    print(f"    [+] Inbound EICAR quarantined to Junk (fid={virus_fid}) with subject '{virus_stored_subj}'.")

    # Verify Admin UI Spam History records the virus detection
    row = wait_until(lambda: history_row(session, virus_subj), 15, 0.5)
    if not row:
        print(f"    [-] No Admin UI Spam History entry found for '{virus_subj}'.", file=sys.stderr)
        return False
    symbols = row.get("symbols") or {}
    if "CLAM_VIRUS" not in symbols:
        print(f"    [-] Expected CLAM_VIRUS in Spam History symbols, got: {symbols}", file=sys.stderr)
        return False
    print(f"    [+] Admin UI Spam History recorded CLAM_VIRUS (score={row.get('score')}, action={row.get('action')}).")

    # Verify Bayes sync records the message as 'virus' without learning it as text spam
    sync_res = container_exec("python3", "/usr/local/bin/grommunio-bayes-sync.py")
    if sync_res.returncode != 0:
        print(f"    [-] grommunio-bayes-sync.py failed:\n{sync_res.stderr}", file=sys.stderr)
        return False
    bayes_check = container_exec(
        "python3", "-c",
        f"import sqlite3; c=sqlite3.connect('/var/lib/rspamd/bayes_learned.sqlite3'); "
        f"r=c.execute('SELECT learned_type FROM learned_messages WHERE message_id={virus_mid}').fetchone(); "
        f"print(r[0] if r else 'NONE')"
    )
    if bayes_check.stdout.strip() != "virus":
        print(f"    [-] Expected Bayes sync to mark EICAR msg {virus_mid} as 'virus', got {bayes_check.stdout.strip()!r}.",
              file=sys.stderr)
        return False
    print("    [+] grommunio-bayes-sync.py recorded quarantined message as 'virus' (skipped Bayes spam training).")

    # 4. Outbound EICAR message submitted from 127.0.0.1:2525 must be rejected with SMTP 550
    print("    [>] 4. Submitting outbound EICAR message from localhost and verifying SMTP 550 rejection...")
    outbound_script = f"""
import smtplib, sys
from email.mime.text import MIMEText
msg = MIMEText({EICAR!r})
msg["Subject"] = "EicarOut-{uuid.uuid4().hex[:8]}"
msg["From"] = "{rcpt}"
msg["To"] = "external-recipient@external.example"
try:
    with smtplib.SMTP("127.0.0.1", 2525, timeout=15) as s:
        s.sendmail(msg["From"], [msg["To"]], msg.as_string())
    print("ACCEPTED_UNEXPECTEDLY")
    sys.exit(1)
except smtplib.SMTPDataError as err:
    print(f"REJECTED:{{err.smtp_code}}:{{err.smtp_error.decode(errors='replace')}}")
    sys.exit(0 if err.smtp_code == 550 else 2)
"""
    out_res = container_exec("python3", "-c", outbound_script)
    if out_res.returncode != 0 or not out_res.stdout.strip().startswith("REJECTED:550:"):
        print(f"    [-] Outbound EICAR was not rejected with 550:\n{out_res.stdout}{out_res.stderr}", file=sys.stderr)
        return False
    print(f"    [+] Outbound EICAR rejected at SMTP DATA ({out_res.stdout.strip()}).")
    return True


if __name__ == "__main__":
    if check_clamav_pipeline():
        print("[+] SUCCESS: ClamAV daemon, Junk quarantine, '[VIRUS]' tagging, and outbound protection verified.\n")
        sys.exit(0)
    print("[-] FAILED: ClamAV verification failed.\n", file=sys.stderr)
    sys.exit(1)
