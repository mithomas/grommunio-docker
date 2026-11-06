#!/usr/bin/env python3
"""
Verification Test: Multi-Domain Mail Routing (DOMAINS)
Starts the shipped image with DOMAINS set and checks that mail for the primary domain, a primary-domain alias,
and both secondary domains is delivered into the one mailbox via Exim -> LMTP, using the Exim configuration
the entrypoint renders (no hand-edited config).
"""
import json
import sys
import uuid

from container_harness import Container, ScratchDatabase, require_harness
from env_helper import mailbox_contains, wait_until

DOMAINS = "alias1.example.org:alias2.example.net"
MAILBOX = "testuser@example.com"
RECIPIENTS = [
    "testuser@example.com",
    "alias.testuser@example.com",
    "testuser@alias1.example.org",
    "alias.testuser@alias2.example.net",
]

# Sent from inside the container: Exim skips spam scanning for localhost, so only routing is exercised here.
SEND_SCRIPT = r'''
import json, smtplib, sys
from email.mime.text import MIMEText
for rcpt, subject in json.loads(sys.argv[1]):
    msg = MIMEText("multi-domain routing check")
    msg["Subject"], msg["From"], msg["To"] = subject, "sender@external.example", rcpt
    with smtplib.SMTP("127.0.0.1", 2525, timeout=10) as client:
        client.sendmail("sender@external.example", [rcpt], msg.as_string())
'''


def check_multidomain_delivery():
    print("==> Checking multi-domain routing with DOMAINS...")
    with ScratchDatabase() as db, Container(env={"DOMAINS": DOMAINS}, scratch=db) as c:
        if not c.wait_healthy(240):
            print(f"    [-] Container did not become healthy:\n{c.logs()[-1200:]}", file=sys.stderr)
            return False

        setup = [
            ("grommunio-admin", "domain", "create", "example.com", "-u", "50"),
            ("grommunio-admin", "user", "create", "--alias", "alias.testuser@example.com", MAILBOX),
        ]
        for cmd in setup:
            res = c.exec(*cmd)
            if res.returncode != 0:
                print(f"    [-] {' '.join(cmd)} failed: {res.stderr or res.stdout}", file=sys.stderr)
                return False

        token = uuid.uuid4().hex[:10]
        subjects = {rcpt: f"MultiDomain-{index}-{token}" for index, rcpt in enumerate(RECIPIENTS)}
        res = c.exec("python3", "-c", SEND_SCRIPT, json.dumps(list(subjects.items())))
        if res.returncode != 0:
            print(f"    [-] SMTP submission failed: {res.stderr}", file=sys.stderr)
            return False

        for rcpt, subject in subjects.items():
            if not wait_until(lambda: mailbox_contains(MAILBOX, subject, runner=c.exec), 60):
                print(f"    [-] Mail for {rcpt} never reached {MAILBOX}.\n{c.logs()[-600:]}", file=sys.stderr)
                return False
            print(f"    [+] {rcpt} delivered to {MAILBOX}.")
    return True


def main():
    require_harness()
    if check_multidomain_delivery():
        print("[+] SUCCESS: Multi-domain routing verified.\n")
        return 0
    print("[-] FAILED: Multi-domain routing verification failed.\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
