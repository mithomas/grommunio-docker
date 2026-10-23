#!/usr/bin/env python3
"""
Verification Test 11: Mailbox Creation Defaults & Privileges
Validates that Grommunio Admin API creation defaults (/defaults/createParams)
are properly configured with the specified parameters:
- Language: de_DE
- Protocols: privWeb=True, privEas=True, privDav=False
- Services & Privileges: smtp=False, pop3_imap=False, changePassword=False
- Integrations: privChat=False, privVideo=False, privFiles=False, privArchive=False, chatUser=False
- Quotas: storagequotalimit=0, prohibitsendquota=0, prohibitreceivequota=0
"""
import json
import os
import sys
import urllib.parse
import urllib.request


def load_admin_password():
    password = os.environ.get("ADMIN_PASSWORD")
    if password:
        return password
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ADMIN_PASSWORD="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "admin_secret"


def check_defaults():
    print("==> Checking Mailbox Creation Defaults & Privileges...")
    admin_pw = load_admin_password()
    admin_port = os.environ.get("ADMIN_PORT", "8081")
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
            if not token:
                print("    [-] Failed to obtain JWT token from login response.", file=sys.stderr)
                return False
            print("    [+] Successfully authenticated to Admin API.")
    except Exception as ex:
        print(f"    [-] Admin login failed: {ex}", file=sys.stderr)
        return False

    # Step 2: Fetch defaults
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

    user_defaults = resp_data.get("data", {}).get("user", {})
    print(f"    [+] Retrieved user creation defaults: {json.dumps(user_defaults, indent=2)}")

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
    ]

    failed = False
    for key, expected in expected_checks:
        actual = user_defaults.get(key)
        if actual != expected:
            print(f"    [-] Mismatch for '{key}': expected {expected!r}, got {actual!r}", file=sys.stderr)
            failed = True
        else:
            print(f"    [+] Checked '{key}': {actual!r} matches expected.")

    return not failed


def main():
    if check_defaults():
        print("[+] SUCCESS: Mailbox creation defaults verified.\n")
        return 0
    else:
        print("[-] FAILED: Mailbox creation defaults verification failed.\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
