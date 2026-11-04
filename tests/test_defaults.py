#!/usr/bin/env python3
"""
Verification Test 11: Mailbox Creation Defaults & Privileges
Validates that Grommunio Admin API creation defaults (/defaults/createParams)
are properly configured with the specified parameters:
- Language: de_DE
- Protocols: privWeb=True, privEas=True, privDav=False
- Services & Privileges: smtp=False, pop3_imap=False, changePassword=False
- Integrations: privChat=False, privVideo=False, privFiles=False, privArchive=False, chatUser=False
- Quotas: storagequotalimit, prohibitsendquota, prohibitreceivequota (supporting KiB / human units)
- Dynamic nonzero quota configuration & idempotency verification
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

from env_helper import get_admin_port, load_env_secret


def parse_quota(val):
    if val is None:
        return 0
    s = str(val).strip().lower()
    if not s or s in ("0", "unlimited", "none", "off", "false"):
        return 0
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([a-z]*)$", s)
    if not m:
        try:
            return int(float(s))
        except ValueError:
            return 0
    num_str, unit = m.groups()
    num = float(num_str)
    if unit in ("g", "gb", "gib"):
        return int(num * 1024 * 1024)
    elif unit in ("m", "mb", "mib"):
        return int(num * 1024)
    elif unit in ("k", "kb", "kib", ""):
        return int(num)
    elif unit in ("t", "tb", "tib"):
        return int(num * 1024 * 1024 * 1024)
    else:
        return int(num)


def check_defaults():
    print("==> Checking Mailbox Creation Defaults & Privileges...")
    admin_pw = load_env_secret("ADMIN_PASSWORD", "admin_secret")
    admin_port = get_admin_port()
    login_url = f"http://localhost:{admin_port}/api/v1/login"
    defaults_url = f"http://localhost:{admin_port}/api/v1/defaults/createParams"

    # Step 1: Authenticate to Admin API
    login_payload = urllib.parse.urlencode({"user": "admin", "pass": admin_pw}).encode()
    try:
        req_login = urllib.request.Request(
            login_url,
            data=login_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(req_login, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            token = data.get("grommunioAuthJwt")
            csrf = data.get("csrf")
            if not token:
                print("    [-] Failed to obtain JWT token from login response.", file=sys.stderr)
                return False
            print("    [+] Successfully authenticated to Admin API.")
    except Exception as ex:
        print(f"    [-] Admin login failed: {ex}", file=sys.stderr)
        return False

    # Step 2: Fetch current defaults
    try:
        req_def = urllib.request.Request(
            defaults_url,
            headers={"Cookie": f"grommunioAuthJwt={token}"}
        )
        with urllib.request.urlopen(req_def, timeout=5) as resp:
            resp_data = json.loads(resp.read().decode())
    except Exception as ex:
        print(f"    [-] Failed to fetch defaults from {defaults_url}: {ex}", file=sys.stderr)
        return False

    initial_payload = resp_data.get("data", {})
    user_defaults = initial_payload.get("user", {})
    print(f"    [+] Retrieved user creation defaults: {json.dumps(user_defaults, indent=2)}")

    expected_storage = parse_quota(load_env_secret("DEFAULT_USER_STORAGE_QUOTA", "0"))
    expected_send = parse_quota(load_env_secret("DEFAULT_USER_SEND_QUOTA", "0"))
    expected_receive = parse_quota(load_env_secret("DEFAULT_USER_RECEIVE_QUOTA", "0"))

    expected_checks = [
        ("lang", "de_DE"),
        ("privWeb", True),
        ("privEas", True),
        ("privDav", False),
        ("smtp", False),
        ("pop3_imap", False),
        ("changePassword", False),
        ("privChat", False),
        ("privVideo", False),
        ("privFiles", False),
        ("privArchive", False),
        ("chat", False),
        ("storagequotalimit", expected_storage),
        ("prohibitsendquota", expected_send),
        ("prohibitreceivequota", expected_receive),
    ]

    failed = False
    for key, expected in expected_checks:
        actual = user_defaults.get(key)
        # Note: missing key with expected 0 is treated as 0/unlimited
        if actual is None and expected == 0:
            actual = 0
        if actual != expected:
            print(f"    [-] Mismatch for '{key}': expected {expected!r}, got {actual!r}", file=sys.stderr)
            failed = True
        else:
            print(f"    [+] Checked '{key}': {actual!r} matches expected.")

    if failed:
        return False

    # Step 3: Test dynamic nonzero quota update and idempotency
    print("    [>] Testing dynamic nonzero quota configuration & idempotency...")
    test_storage = 10485760  # 10 GiB in KiB
    test_send = 8388608      # 8 GiB in KiB
    test_receive = 9437184   # 9 GiB in KiB

    updated_user = dict(user_defaults)
    updated_user.update({
        "storagequotalimit": test_storage,
        "prohibitsendquota": test_send,
        "prohibitreceivequota": test_receive,
    })

    test_payload = {
        "user": updated_user,
        "domain": initial_payload.get("domain", {})
    }

    try:
        # PUT nonzero quotas
        req_put = urllib.request.Request(
            defaults_url,
            data=json.dumps(test_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Cookie": f"grommunioAuthJwt={token}",
                "X-Csrf-Token": csrf
            },
            method="PUT"
        )
        with urllib.request.urlopen(req_put, timeout=5) as resp:
            pass

        # Verify nonzero quotas reflected in GET
        req_verify = urllib.request.Request(
            defaults_url,
            headers={"Cookie": f"grommunioAuthJwt={token}"}
        )
        with urllib.request.urlopen(req_verify, timeout=5) as resp:
            verified_data = json.loads(resp.read().decode()).get("data", {}).get("user", {})

        assert verified_data.get("storagequotalimit") == test_storage, "storagequotalimit mismatch"
        assert verified_data.get("prohibitsendquota") == test_send, "prohibitsendquota mismatch"
        assert verified_data.get("prohibitreceivequota") == test_receive, "prohibitreceivequota mismatch"
        print(f"    [+] Successfully updated nonzero quotas: storage={test_storage}KB, send={test_send}KB, receive={test_receive}KB")

        # Test idempotency check logic
        desired_nonzero = dict(updated_user)
        is_idempotent = all(verified_data.get(k) == v for k, v in desired_nonzero.items())
        assert is_idempotent, "Idempotency check failed: verified_data does not match desired_nonzero"
        print("    [+] Idempotency check verified: unchanged nonzero quotas require no update.")

    finally:
        # Step 4: Restore original initial defaults cleanly
        print("    [>] Restoring initial defaults...")
        req_restore = urllib.request.Request(
            defaults_url,
            data=json.dumps(initial_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Cookie": f"grommunioAuthJwt={token}",
                "X-Csrf-Token": csrf
            },
            method="PUT"
        )
        try:
            with urllib.request.urlopen(req_restore, timeout=5):
                print("    [+] Initial defaults restored successfully.")
        except Exception as ex:
            print(f"    [-] Notice: restore request returned: {ex}", file=sys.stderr)

    return True


def main():
    if check_defaults():
        print("[+] SUCCESS: Mailbox creation defaults verified.\n")
        return 0
    else:
        print("[-] FAILED: Mailbox creation defaults verification failed.\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
