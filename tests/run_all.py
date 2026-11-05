#!/usr/bin/env python3
"""
Test Suite Runner
Executes all verification tests in sequence and reports a summary table.
A test exits with SKIP_EXIT (77) when it cannot run; skips fail the run when CI is set
unless the script is listed in ALLOW_SKIP (comma-separated).
"""
import os
import subprocess
import sys

from env_helper import SKIP_EXIT

TESTS = [
    ("test_lint.py", "Static Lint Gate & Fail-Safe Verifications"),
    ("test_endpoints.py", "HTTP Ingress Endpoints & Content-Types"),
    ("test_admin_auth.py", "Grommunio Admin API & JWT Auth"),
    ("test_rspamd_auth.py", "Rspamd Dashboard Dedicated Auth"),
    ("test_ldap_config.py", "Admin API LDAP Config & Disk Persistence"),
    ("test_ldap.py", "OpenLDAP Integration & User Downsync"),
    ("test_webmail_security.py", "Webmail Root Ingress & Cookie Security"),
    ("test_mail_pipeline.py", "Exim -> Rspamd -> Gromox Pipeline & Spam History"),
    ("test_live_status.py", "Admin UI Live Status & Nginx VTS Telemetry"),
    ("test_monitoring_logs.py", "Admin UI Monitoring Logs (Exim & Daemons)"),
    ("test_defaults.py", "Mailbox Creation Defaults & Privileges"),
    ("test_db_socket.py", "MariaDB Unix Socket (DB_SOCKET) Connectivity"),
    ("test_multidomain_delivery.py", "Multi-Domain Routing (DOMAINS) via Exim -> LMTP"),
    ("test_password_escaping.py", "Special-Character Passwords End-to-End"),
    ("test_db_init_safety.py", "Database Schema & Migration Idempotence"),
    ("test_startup_safety.py", "Startup Safety (Credentials, DB Failure, SIGTERM, Restart)"),
    ("test_log_retention.py", "Persistent Log Growth Bounding & 1-Year Retention"),
    ("test_mailbox_ownership.py", "Mailbox Volume Ownership Optimization & Repair"),
    ("test_bayes_sync.py", "Bayes Synchronization Engine & Scan Cost Reduction"),
    ("test_healthcheck.py", "Container Healthcheck Diagnostics & Thresholds"),
]


def main():
    test_dir = os.path.dirname(os.path.abspath(__file__))
    ci = os.environ.get("CI", "").lower() in ("1", "true", "yes")
    allow_skip = {s.strip() for s in os.environ.get("ALLOW_SKIP", "").split(",") if s.strip()}
    results = []

    print("==================================================================")
    print("      Grommunio Debian 13 Container Verification Test Suite       ")
    print("==================================================================\n")

    for script, description in TESTS:
        proc = subprocess.run([sys.executable, os.path.join(test_dir, script)])
        if proc.returncode == 0:
            status = "PASSED"
        elif proc.returncode == SKIP_EXIT:
            status = "SKIPPED" if (not ci or script in allow_skip) else "FAILED"
            if status == "FAILED":
                print(f"[-] {script} was skipped, which is not allowed under CI (see ALLOW_SKIP).\n", file=sys.stderr)
        else:
            status = "FAILED"
        results.append((script, description, status))

    print("\n==================================================================")
    print("                         TEST SUMMARY                             ")
    print("==================================================================")
    all_passed = True
    for script, description, status in results:
        if status == "PASSED":
            status_str = "[ PASS ]"
        elif status == "SKIPPED":
            status_str = "[ SKIP ]"
        else:
            status_str = "[ FAIL ]"
            all_passed = False
        print(f"{status_str} {script:<24} - {description}")

    print("==================================================================")
    if all_passed:
        print("[+] ALL VERIFICATION TESTS PASSED SUCCESSFULLY!")
        return 0
    else:
        print("[-] ONE OR MORE VERIFICATION TESTS FAILED.", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
