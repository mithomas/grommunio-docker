#!/usr/bin/env python3
"""
Test Suite Runner
Executes all verification tests in sequence and reports a summary table.
"""
import subprocess
import os
import sys

TESTS = [
    ("test_services.py", "Supervisor Services (All 14 Running)", False),
    ("test_endpoints.py", "HTTP Ingress Endpoints & Content-Types", False),
    ("test_admin_auth.py", "Grommunio Admin API & JWT Auth", False),
    ("test_rspamd_auth.py", "Rspamd Dashboard Dedicated Auth", False),
    ("test_ldap_config.py", "Admin API LDAP Config & Disk Persistence", False),
    ("test_ldap.py", "OpenLDAP Integration & User Downsync", True),
    ("test_webmail_security.py", "Webmail Root Ingress & Cookie Security", False),
    ("test_mail_pipeline.py", "Exim -> Rspamd -> Gromox Pipeline & Spam History", False),
    ("test_live_status.py", "Admin UI Live Status & Nginx VTS Telemetry", False),
    ("test_monitoring_logs.py", "Admin UI Monitoring Logs (Exim & Daemons)", False),
    ("test_defaults.py", "Mailbox Creation Defaults & Privileges", False),
]

def is_port_open(port=389, host="127.0.0.1"):
    import socket
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except (socket.error, OSError):
        return False

def main():
    test_dir = os.path.dirname(os.path.abspath(__file__))
    results = []

    print("==================================================================")
    print("      Grommunio Debian 13 Container Verification Test Suite       ")
    print("==================================================================\n")

    for item in TESTS:
        script = item[0]
        description = item[1]
        is_optional = item[2] if len(item) > 2 else False

        if script == "test_ldap.py" and not is_port_open(389):
            print(f"[-] Skipping {script} (OpenLDAP companion service port 389 not listening)\n")
            results.append((script, description, "SKIPPED"))
            continue

        script_path = os.path.join(test_dir, script)
        cmd = [sys.executable, script_path]
        proc = subprocess.run(cmd)
        passed = (proc.returncode == 0)
        results.append((script, description, "PASSED" if passed else "FAILED"))

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
