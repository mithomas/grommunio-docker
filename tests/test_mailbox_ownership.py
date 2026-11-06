#!/usr/bin/env python3
"""
Verification Test: Mailbox Volume Ownership & Repair
1. /var/lib/gromox, its user store and existing mailbox directories are owned by gromox:gromox.
2. grommunio-fix-permissions.sh repairs a root-owned tree: 'shallow' fixes only the top level,
   'recursive' fixes everything below it.
"""
import sys

from env_helper import container_exec

PROBE = "/var/lib/gromox/ownership-probe"
PROBE_FILE = f"{PROBE}/nested/deep/file"


def owner(path):
    res = container_exec("stat", "-c", "%U:%G", path)
    return res.stdout.strip() if res.returncode == 0 else None


def test_mailbox_storage_ownership():
    print("    [>] 1. Validating gromox ownership of the mailbox volume...")
    for path in ("/var/lib/gromox", "/var/lib/gromox/user"):
        if owner(path) != "gromox:gromox":
            print(f"    [-] {path} is owned by {owner(path)}, expected gromox:gromox", file=sys.stderr)
            return False

    res = container_exec("find", "/var/lib/gromox/user", "-mindepth", "2", "-maxdepth", "2",
                         "(", "!", "-group", "gromox", "-o", "(", "!", "-user", "gromox", "!", "-user", "grommunio", ")", ")")
    if res.returncode != 0 or res.stdout.strip():
        print(f"    [-] Mailbox directories not owned by grommunio/gromox:gromox:\n{res.stdout or res.stderr}", file=sys.stderr)
        return False
    print("    [+] /var/lib/gromox, the user store and mailbox directories have valid gromox ownership.")
    return True


def test_repair_script():
    print("    [>] 2. Validating the repair utility on a root-owned tree...")
    try:
        setup = container_exec("sh", "-c", f"mkdir -p {PROBE}/nested/deep && touch {PROBE_FILE}")
        if setup.returncode != 0 or owner(PROBE_FILE) != "root:root":
            print(f"    [-] Could not create the root-owned probe tree: {setup.stderr}", file=sys.stderr)
            return False

        res = container_exec("/usr/local/bin/grommunio-fix-permissions.sh", "shallow")
        if res.returncode != 0:
            print(f"    [-] Shallow repair failed:\n{res.stderr or res.stdout}", file=sys.stderr)
            return False
        if owner(PROBE) != "gromox:gromox":
            print(f"    [-] Shallow repair did not fix the top level ({owner(PROBE)})", file=sys.stderr)
            return False
        if owner(PROBE_FILE) != "root:root":
            print(f"    [-] Shallow repair unexpectedly descended into mailbox data ({owner(PROBE_FILE)})", file=sys.stderr)
            return False
        print("    [+] Shallow repair fixed the top level only.")

        res = container_exec("/usr/local/bin/grommunio-fix-permissions.sh", "recursive")
        if res.returncode != 0:
            print(f"    [-] Recursive repair failed:\n{res.stderr or res.stdout}", file=sys.stderr)
            return False
        if owner(PROBE_FILE) != "gromox:gromox":
            print(f"    [-] Recursive repair left {PROBE_FILE} owned by {owner(PROBE_FILE)}", file=sys.stderr)
            return False
        print("    [+] Recursive repair restored ownership of nested files.")
        return True
    finally:
        container_exec("rm", "-rf", PROBE)


def main():
    print("==> Checking Mailbox Volume Ownership & Repair...")
    if not test_mailbox_storage_ownership():
        return 1
    if not test_repair_script():
        return 1
    print("    [+] ALL MAILBOX OWNERSHIP VERIFICATIONS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
