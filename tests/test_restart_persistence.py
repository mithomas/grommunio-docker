#!/usr/bin/env python3
"""
Verification Test: Container Restart Data & Volume Persistence
Verifies operational and data persistence across container lifecycles:
1. Container 1 starts against ScratchDatabase with a persistent named volume for /var/lib/gromox.
2. Domain and user mailbox are created, and an email is delivered into the mailbox.
3. Container 1 terminates cleanly.
4. Container 2 starts against the same ScratchDatabase and volume:
   - Skips schema creation ("already initialized").
   - Retains the previously created domain and user.
   - Retains the previously delivered message in SQLite.
   - Successfully receives and delivers a second message alongside the existing one.
5. All scratch containers, scratch databases, and test volumes are cleanly removed.
"""
import secrets
import sys

from container_harness import Container, ScratchDatabase, _docker, require_harness
from env_helper import mailbox_contains, wait_until

SUBMIT_SCRIPT = r'''
import smtplib
import sys
from email.mime.text import MIMEText

sender = sys.argv[1]
rcpt = sys.argv[2]
subject = sys.argv[3]
body = sys.argv[4]

msg = MIMEText(body)
msg["Subject"] = subject
msg["From"] = sender
msg["To"] = rcpt

try:
    with smtplib.SMTP("127.0.0.1", 2525, timeout=15) as s:
        s.sendmail(sender, [rcpt], msg.as_string())
    print("SUBMIT_OK")
except Exception as ex:
    print(f"SUBMIT_ERROR: {ex}", file=sys.stderr)
    sys.exit(1)
'''


def check_restart_persistence():
    print("==> Checking Container Restart Data & Volume Persistence...")
    require_harness()

    vol_gromox = f"gtest-gromox-{secrets.token_hex(4)}"
    test_user = "persistuser@example.com"
    test_pass = "Secret123!"

    print(f"    [>] 1. Creating dedicated test volume '{vol_gromox}'...")
    _docker("volume", "create", vol_gromox)

    try:
        with ScratchDatabase() as db:
            # First container run: initialize schema, user, and deliver message 1
            print("    [>] 2. Starting first container against scratch database and persistent volume...")
            with Container(volumes=[f"{vol_gromox}:/var/lib/gromox"], scratch=db) as first:
                if not first.wait_healthy(180):
                    print(f"[-] FAIL: First container failed to become healthy.\nLogs:\n{first.logs()[-1200:]}", file=sys.stderr)
                    return False
                print("    [+] First container healthy; provisioning domain and user...")

                first.exec("grommunio-admin", "domain", "create", "example.com", "-u", "50")
                res_user = first.exec("grommunio-admin", "user", "create",
                                      "--privWeb", "true", "--privEas", "true",
                                      "--altname", "persistuser", test_user)
                if res_user.returncode != 0:
                    print(f"[-] FAIL: Failed to create user in first container:\n{res_user.stderr}", file=sys.stderr)
                    return False
                first.exec("grommunio-admin", "passwd", test_user, "--password", test_pass)

                # Deliver message 1
                subject_1 = f"PersistMsg1-{secrets.token_hex(4)}"
                print(f"    [>] 3. Delivering message 1 ('{subject_1}') into mailbox...")
                res_sub1 = first.exec("python3", "-", "sender@example.com", test_user, subject_1,
                                      "Content of message 1", input=SUBMIT_SCRIPT, timeout=30)
                if res_sub1.returncode != 0 or "SUBMIT_OK" not in res_sub1.stdout:
                    print(f"[-] FAIL: Submission of message 1 failed:\n{res_sub1.stderr or res_sub1.stdout}", file=sys.stderr)
                    return False

                # Assert message 1 in mailbox
                if not wait_until(lambda: mailbox_contains(test_user, subject_1, runner=first.exec), 30, 1.0):
                    print("[-] FAIL: Message 1 was not stored in user's mailbox.", file=sys.stderr)
                    return False
                print("    [+] Message 1 stored and verified in mailbox.")

                print("    [>] 4. Stopping first container...")
                first.stop()

            # Second container run: attach to same DB and volume
            print("    [>] 5. Starting second container attached to same database and volume...")
            with Container(volumes=[f"{vol_gromox}:/var/lib/gromox"], scratch=db) as second:
                if not second.wait_healthy(180):
                    print(f"[-] FAIL: Second container failed to become healthy.\nLogs:\n{second.logs()[-1200:]}", file=sys.stderr)
                    return False

                logs_2 = second.logs()
                if "already initialized" not in logs_2:
                    print("[-] FAIL: Second container did not detect already initialized database.", file=sys.stderr)
                    return False
                if "Initial database schema created successfully" in logs_2:
                    print("[-] FAIL: Second container re-created database schema.", file=sys.stderr)
                    return False
                print("    [+] Second container skipped schema re-creation and reached healthy state.")

                # Verify user still exists
                res_q = second.exec("grommunio-admin", "user", "query", "-f", f"username={test_user}")
                if test_user not in res_q.stdout:
                    print(f"[-] FAIL: User '{test_user}' not found in second container:\n{res_q.stdout}", file=sys.stderr)
                    return False
                print("    [+] User and domain preserved across restart.")

                # Verify message 1 is still in mailbox
                if not mailbox_contains(test_user, subject_1, runner=second.exec):
                    print("[-] FAIL: Message 1 lost after container restart.", file=sys.stderr)
                    return False
                print("    [+] Pre-restart message 1 confirmed intact in mailbox.")

                # Deliver message 2
                subject_2 = f"PersistMsg2-{secrets.token_hex(4)}"
                print(f"    [>] 6. Delivering message 2 ('{subject_2}') into restarted container...")
                res_sub2 = second.exec("python3", "-", "sender@example.com", test_user, subject_2,
                                       "Content of message 2", input=SUBMIT_SCRIPT, timeout=30)
                if res_sub2.returncode != 0 or "SUBMIT_OK" not in res_sub2.stdout:
                    print(f"[-] FAIL: Submission of message 2 failed:\n{res_sub2.stderr or res_sub2.stdout}", file=sys.stderr)
                    return False

                # Assert message 2 in mailbox
                if not wait_until(lambda: mailbox_contains(test_user, subject_2, runner=second.exec), 30, 1.0):
                    print("[-] FAIL: Message 2 was not stored in user's mailbox.", file=sys.stderr)
                    return False

                # Assert BOTH messages exist
                has_both = (mailbox_contains(test_user, subject_1, runner=second.exec) and
                            mailbox_contains(test_user, subject_2, runner=second.exec))
                if not has_both:
                    print("[-] FAIL: Both messages not present in mailbox concurrently.", file=sys.stderr)
                    return False
                print("    [+] Both pre-restart and post-restart messages verified concurrently in mailbox.")

    finally:
        print(f"    [>] 7. Cleaning up test volume '{vol_gromox}'...")
        _docker("volume", "rm", "-f", vol_gromox)

    return True


def main():
    if not check_restart_persistence():
        print("[-] FAILED: Container restart persistence verification failed.\n", file=sys.stderr)
        return 1
    print("[+] SUCCESS: Container restart data & volume persistence verified.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
