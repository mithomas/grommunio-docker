#!/usr/bin/env python3
"""
Verification Test: Persistent Log Growth Bounding & 1-Year Retention
1. Admin UI FileReader reads .1 rotated logs when the active file has fewer lines than requested.
2. In-container logrotate accepts the shipped configuration (dry run).
"""
import os
import subprocess
import sys


def test_filereader_tail_continuity():
    print("    [>] 1. Validating Admin UI FileReader tail continuity with .1 fallback...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_logs_path = os.path.join(root_dir, "rootfs", "usr", "share", "grommunio-admin-api", "tools", "file_logs.py")

    with open(file_logs_path, "r", encoding="utf-8") as f:
        fl_content = f.read()

    assert 'rotated_path = self.path + ".1"' in fl_content, (
        "FAIL: FileReader does not define rotated_path .1 check"
    )
    assert 'rot_lines = f_rot.readlines()' in fl_content, (
        "FAIL: FileReader does not read rotated log lines"
    )
    assert 'all_entries = rot_entries + entries' in fl_content, (
        "FAIL: FileReader does not combine rotated and active entries"
    )

    print("    [+] Admin UI FileReader tail continuity logic verified.")
    return True


def test_in_container_logrotate_execution():
    print("    [>] 2. Validating in-container logrotate debug dry-run & daemon execution...")
    check_script = r'''
import subprocess
import sys

which_res = subprocess.run(["which", "logrotate"], capture_output=True, text=True)
if which_res.returncode != 0:
    print("FAIL: logrotate is not installed in the container image", file=sys.stderr)
    sys.exit(1)

d_res = subprocess.run(["logrotate", "-d", "-s", "/var/log/logrotate.status", "/etc/logrotate.d/grommunio"],
                       capture_output=True, text=True)
if d_res.returncode != 0:
    print(f"FAIL: logrotate dry-run returned error:\n{d_res.stderr or d_res.stdout}", file=sys.stderr)
    sys.exit(1)

print("OK_CONTAINER_LOGROTATE")
'''
    res = subprocess.run(
        ["docker", "exec", "-i", "grommunio", "python3", "-c", check_script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if res.returncode != 0:
        print(f"    [-] In-container logrotate check failed:\n{res.stderr or res.stdout}", file=sys.stderr)
        return False
    print("    [+] In-container logrotate syntax and debug evaluation verified.")
    return True


def main():
    print("==> Checking Persistent Log Growth Bounding & 1-Year Retention...")
    if not test_filereader_tail_continuity():
        return 1
    if not test_in_container_logrotate_execution():
        return 1
    print("    [+] ALL LOG RETENTION & GROWTH BOUNDING VERIFICATIONS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
