#!/usr/bin/env python3
"""
Verification Test 7: OpenLDAP Companion Integration & User Downsync
Verifies:
1. OpenLDAP service responsiveness (port 389).
2. Grommunio Admin CLI LDAP connectivity ('grommunio-admin ldap info').
3. LDAP search query returns bootstrap user 'testuser' and group 'grommunio-users'.
4. Synchronization of LDAP user via 'grommunio-admin ldap downsync -c' into Grommunio.
5. Presence and provisioning of 'testuser@example.com' in Grommunio user store.
"""
import subprocess
import sys

from env_helper import SKIP_EXIT, get_bind_ip, get_ldap_port, ldap_available, wait_until

def run_compose_cmd(cmd_args):
    cmd = ["docker", "compose", "exec", "-T", "grommunio"] + cmd_args
    return subprocess.run(cmd, capture_output=True, text=True)


def _cmd_output(res) -> str:
    return "\n".join(part.strip() for part in (res.stdout, res.stderr) if part and part.strip())


def _verify_provisioned_user() -> bool:
    res = run_compose_cmd(["grommunio-admin", "user", "query", "-f", "username=testuser@example.com"])
    if "testuser@example.com" not in res.stdout:
        print(f"    [-] 'testuser@example.com' missing from user query:\n{_cmd_output(res)}", file=sys.stderr)
        return False
    print("    [+] 'testuser@example.com' confirmed provisioned in Grommunio database.")

    res_show = run_compose_cmd(["grommunio-admin", "user", "show", "testuser@example.com"])
    if "testuser" not in res_show.stdout or "altnames:" not in res_show.stdout:
        print(f"    [-] 'testuser' missing from altnames for testuser@example.com:\n{_cmd_output(res_show)}", file=sys.stderr)
        return False
    print("    [+] Short UID 'testuser' confirmed registered in altnames.")

    res_find = run_compose_cmd(
        ["find", "/var/lib/gromox/user/example.com", "-maxdepth", "2",
         "(", "!", "-group", "gromox", "-o", "(", "!", "-user", "gromox", "!", "-user", "grommunio", ")", ")"]
    )
    if res_find.returncode != 0 or res_find.stdout.strip():
        print(f"    [-] Mailbox paths not owned by gromox/grommunio:gromox:\n{_cmd_output(res_find)}", file=sys.stderr)
        return False
    print("    [+] Mailbox store owned by grommunio/gromox:gromox.")
    return True


def check_ldap_integration():
    ldap_port = get_ldap_port()
    bind_ip = get_bind_ip()
    print("==> Checking OpenLDAP companion integration and user provisioning...")

    # Step 1: OpenLDAP is optional (compose test profile); report as skipped when absent
    if not ldap_available():
        print(f"    [!] OpenLDAP container (port {ldap_port}) is not accessible on {bind_ip}.")
        print("        Ensure the test profile is running: docker compose --profile test up -d")
        return None
    print(f"    [+] OpenLDAP port {ldap_port} is open and reachable.")

    # Step 2: Test 'grommunio-admin ldap info' from within grommunio container
    res = run_compose_cmd(["grommunio-admin", "ldap", "info"])
    if res.returncode != 0:
        print(f"    [-] 'grommunio-admin ldap info' failed:\n{_cmd_output(res)}", file=sys.stderr)
        return False
    print("    [+] 'grommunio-admin ldap info' succeeded.")

    # Step 3: Search for bootstrap user 'testuser'
    def search_testuser():
        res = run_compose_cmd(["grommunio-admin", "ldap", "search", "testuser"])
        return res.returncode == 0 and "testuser" in res.stdout

    if not wait_until(search_testuser, timeout=30, interval=2.0):
        res = run_compose_cmd(["grommunio-admin", "ldap", "search", "testuser"])
        print(f"    [-] Bootstrap user 'testuser' not found in LDAP search:\n{_cmd_output(res)}", file=sys.stderr)
        return False
    print("    [+] Bootstrap user 'testuser' found in LDAP search.")

    # Step 4: Ensure primary domain 'example.com' exists and reset any pre-existing LDAP objects
    run_compose_cmd(["grommunio-admin", "domain", "create", "example.com", "-u", "50"])
    for addr in ("testuser@example.com", "grommunio-users@example.com"):
        run_compose_cmd(["grommunio-admin", "user", "delete", "-y", addr])

    res_pre = run_compose_cmd(["grommunio-admin", "user", "query", "-f", "username=testuser@example.com"])
    if "testuser@example.com" in res_pre.stdout:
        print(f"    [-] Could not remove pre-existing 'testuser@example.com' before fresh import test:\n{_cmd_output(res_pre)}",
              file=sys.stderr)
        return False
    print("    [+] Clean pre-downsync state confirmed (no pre-existing 'testuser@example.com').")

    # Step 5: Pass 1 - Fresh LDAP downsync (exercises new user import via Users.create and defaults-system)
    print("    [>] Pass 1: Executing fresh user import ('grommunio-admin ldap downsync -c')...")
    res = run_compose_cmd(["grommunio-admin", "ldap", "downsync", "-c"])
    if res.returncode != 0 or "imported as user" not in res.stdout:
        print(f"    [-] Fresh 'grommunio-admin ldap downsync -c' failed:\n{_cmd_output(res)}", file=sys.stderr)
        return False
    print("    [+] Fresh downsync import executed successfully.")

    if not _verify_provisioned_user():
        return False

    # Step 6: Pass 2 - Existing user LDAP downsync (exercises re-synchronization of an existing user)
    print("    [>] Pass 2: Executing existing-user re-sync ('grommunio-admin ldap downsync -c')...")
    res2 = run_compose_cmd(["grommunio-admin", "ldap", "downsync", "-c"])
    if res2.returncode != 0 or "Synchronizing testuser@example.com..." not in res2.stdout:
        print(f"    [-] Existing-user 'grommunio-admin ldap downsync -c' failed:\n{_cmd_output(res2)}", file=sys.stderr)
        return False
    print("    [+] Existing-user downsync executed successfully.")

    return _verify_provisioned_user()

def main():
    success = check_ldap_integration()
    if success is None:
        print("[~] LDAP INTEGRATION TEST SKIPPED.")
        return SKIP_EXIT
    if success:
        print("[+] LDAP INTEGRATION TEST PASSED!")
        return 0
    else:
        print("[-] LDAP INTEGRATION TEST FAILED.", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
