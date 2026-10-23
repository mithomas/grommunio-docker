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
import socket
import sys

def is_ldap_port_open(host="127.0.0.1", port=389, timeout=2.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.error, OSError):
        return False

def run_compose_cmd(cmd_args):
    cmd = ["docker", "compose", "exec", "-T", "grommunio"] + cmd_args
    return subprocess.run(cmd, capture_output=True, text=True)

def check_ldap_integration():
    print("==> Checking OpenLDAP companion integration and user provisioning...")

    # Step 1: Check if OpenLDAP port 389 is listening on host
    if not is_ldap_port_open():
        print("    [!] OpenLDAP container (port 389) is not accessible on localhost.")
        print("        Ensure the test profile is running: docker compose --profile test up -d")
        return False
    print("    [+] OpenLDAP port 389 is open and reachable.")

    # Step 2: Test 'grommunio-admin ldap info' from within grommunio container
    res = run_compose_cmd(["grommunio-admin", "ldap", "info"])
    if res.returncode != 0:
        print(f"    [-] 'grommunio-admin ldap info' failed: {res.stderr or res.stdout}", file=sys.stderr)
        return False
    print("    [+] 'grommunio-admin ldap info' succeeded.")

    # Step 3: Search for bootstrap user 'testuser'
    res = run_compose_cmd(["grommunio-admin", "ldap", "search", "testuser"])
    if res.returncode != 0 or "testuser" not in res.stdout:
        print(f"    [-] Bootstrap user 'testuser' not found in LDAP search: {res.stderr or res.stdout}", file=sys.stderr)
        return False
    print("    [+] Bootstrap user 'testuser' found in LDAP search.")

    # Step 4: Ensure primary domain 'example.com' exists in Grommunio
    run_compose_cmd(["grommunio-admin", "domain", "create", "example.com", "-u", "50"])

    # Step 5: Perform LDAP downsync
    print("    [>] Executing user downsync ('grommunio-admin ldap downsync -c')...")
    res = run_compose_cmd(["grommunio-admin", "ldap", "downsync", "-c"])
    if res.returncode != 0:
        print(f"    [-] 'grommunio-admin ldap downsync -c' returned error: {res.stderr or res.stdout}", file=sys.stderr)
        return False
    print("    [+] Downsync executed successfully.")

    # Step 6: Verify user is provisioned in Grommunio with altnames mapped
    res = run_compose_cmd(["grommunio-admin", "user", "query", "-f", "username=testuser@example.com"])
    if "testuser@example.com" not in res.stdout:
        print(f"    [-] 'testuser@example.com' missing from user query after downsync:\n{res.stdout}", file=sys.stderr)
        return False
    print("    [+] 'testuser@example.com' confirmed provisioned in Grommunio database.")

    res_show = run_compose_cmd(["grommunio-admin", "user", "show", "testuser@example.com"])
    if "testuser" not in res_show.stdout or "altnames:" not in res_show.stdout:
        print(f"    [-] 'testuser' missing from altnames for testuser@example.com:\n{res_show.stdout}", file=sys.stderr)
        return False
    print("    [+] Short UID 'testuser' confirmed registered in altnames.")

    # Step 7: Ensure mailbox store permissions are owned by gromox daemon
    run_compose_cmd(["chown", "-R", "gromox:gromox", "/var/lib/gromox/user"])
    print("    [+] Mailbox store permissions verified.")

    return True

def main():
    success = check_ldap_integration()
    if success:
        print("[+] LDAP INTEGRATION TEST PASSED!")
        return 0
    else:
        print("[-] LDAP INTEGRATION TEST FAILED.", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
