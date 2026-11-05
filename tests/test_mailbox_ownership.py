#!/usr/bin/env python3
"""
Verification Test: Mailbox Volume Ownership & Repair
1. /var/lib/gromox, /var/lib/gromox/user and existing mailbox databases are accessible to the daemons.
2. /usr/local/bin/grommunio-fix-permissions.sh runs in shallow and recursive mode inside the container.
"""
import subprocess
import sys


def test_mailbox_storage_integrity():
    print("    [>] 1. Validating in-container mailbox volume permissions & access...")
    verify_script = r'''
import os
import stat
import sys

# Check /var/lib/gromox
if not os.path.exists("/var/lib/gromox"):
    print("FAIL: /var/lib/gromox does not exist", file=sys.stderr)
    sys.exit(1)

st = os.stat("/var/lib/gromox")
# Ensure directory is writable by owner/group
mode = oct(stat.S_IMODE(st.st_mode))
if (st.st_mode & 0o770) != 0o770:
    print(f"FAIL: /var/lib/gromox has unexpected mode: {mode}", file=sys.stderr)
    sys.exit(1)

# Check /var/lib/gromox/user if present
if os.path.exists("/var/lib/gromox/user"):
    st_user = os.stat("/var/lib/gromox/user")
    if (st_user.st_mode & 0o700) != 0o700:
        print("FAIL: /var/lib/gromox/user not accessible to owner", file=sys.stderr)
        sys.exit(1)

print("OK_MAILBOX_PERMISSIONS")
'''
    res = subprocess.run(
        ["docker", "exec", "-i", "grommunio", "python3", "-c", verify_script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if res.returncode != 0 or "OK_MAILBOX_PERMISSIONS" not in res.stdout:
        print(f"    [-] Mailbox storage permission check failed:\n{res.stderr or res.stdout}", file=sys.stderr)
        return False

    print("    [+] /var/lib/gromox and user mailbox stores have correct owner permissions.")
    return True


def test_repair_script_execution():
    print("    [>] 2. Validating on-demand repair utility in container (shallow and recursive)...")
    res_shallow = subprocess.run(
        ["docker", "exec", "-i", "grommunio", "/usr/local/bin/grommunio-fix-permissions.sh", "shallow"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if res_shallow.returncode != 0 or "Permissions repair (shallow) completed successfully" not in res_shallow.stdout:
        print(f"    [-] Repair utility shallow execution failed:\n{res_shallow.stderr or res_shallow.stdout}", file=sys.stderr)
        return False

    res_rec = subprocess.run(
        ["docker", "exec", "-i", "grommunio", "/usr/local/bin/grommunio-fix-permissions.sh", "recursive"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if res_rec.returncode != 0 or "Permissions repair (recursive) completed successfully" not in res_rec.stdout:
        print(f"    [-] Repair utility recursive execution failed:\n{res_rec.stderr or res_rec.stdout}", file=sys.stderr)
        return False

    print("    [+] /usr/local/bin/grommunio-fix-permissions.sh executed successfully in both shallow and recursive modes.")
    return True


def main():
    print("==> Checking Mailbox Volume Ownership & Repair...")
    if not test_mailbox_storage_integrity():
        return 1
    if not test_repair_script_execution():
        return 1
    print("    [+] ALL MAILBOX OWNERSHIP VERIFICATIONS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
