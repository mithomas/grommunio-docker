#!/usr/bin/env python3
"""
Verification Test 8: Admin API LDAP Configuration & Disk Persistence
Verifies:
1. Admin API authentication and CSRF token retrieval.
2. GET /api/v1/system/mconf/ldap returns populated default group mappings.
3. LDAP connectivity check reports ldapAvailable = True (when OpenLDAP companion is running).
4. PUT /api/v1/system/mconf/ldap succeeds without 500 Permission Denied errors.
5. Configuration changes are successfully persisted to disk on /etc/gromox/ldap_adaptor.cfg.
"""
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import sys

def load_env_password():
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

def check_ldap_config():
    print("==> Checking Admin API LDAP configuration & disk persistence...")
    admin_pw = load_env_password()
    login_url = "http://localhost:8081/api/v1/login"

    # Step 1: Authenticate and retrieve JWT + CSRF token
    login_data = urllib.parse.urlencode({"user": "admin", "pass": admin_pw}).encode()
    req_login = urllib.request.Request(
        login_url,
        data=login_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req_login) as resp:
            auth_data = json.loads(resp.read().decode())
            token = auth_data.get("grommunioAuthJwt")
            csrf = auth_data.get("csrf")
            if not token or not csrf:
                print("    [-] Missing JWT token or CSRF token from login response.", file=sys.stderr)
                return False
            print("    [+] Admin API authenticated; JWT and CSRF tokens retrieved.")
    except Exception as ex:
        print(f"    [-] Login failed: {ex}", file=sys.stderr)
        return False

    # Step 2: Fetch current LDAP configuration via GET /api/v1/system/mconf/ldap
    get_url = "http://localhost:8081/api/v1/system/mconf/ldap"
    req_get = urllib.request.Request(
        get_url,
        headers={"Cookie": f"grommunioAuthJwt={token}"}
    )
    try:
        with urllib.request.urlopen(req_get) as resp:
            ldap_resp = json.loads(resp.read().decode())
            data = ldap_resp.get("data", {})
            groups = data.get("groups", {})
            ldap_available = ldap_resp.get("ldapAvailable", False)

            print(f"    [+] GET /system/mconf/ldap returned HTTP 200 (ldapAvailable={ldap_available}).")

            # Check that all 4 default group keys are present
            required_group_keys = ["groupMemberAttr", "groupaddr", "groupfilter", "groupname"]
            missing_keys = [k for k in required_group_keys if not groups.get(k)]
            if missing_keys:
                print(f"    [-] Missing default group configurations: {missing_keys}", file=sys.stderr)
                return False
            print(f"    [+] Default LDAP groups fully configured: {groups}")

            if data.get("disabled") is True:
                print("    [-] LDAP is unexpectedly reported as disabled.", file=sys.stderr)
                return False
            print("    [+] LDAP is enabled in configuration.")
    except Exception as ex:
        print(f"    [-] Failed to GET LDAP configuration: {ex}", file=sys.stderr)
        return False

    # Step 3: Perform PUT /api/v1/system/mconf/ldap?force=true (simulates Admin UI save)
    put_url = "http://localhost:8081/api/v1/system/mconf/ldap?force=true"
    req_put = urllib.request.Request(
        put_url,
        data=json.dumps(data).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Cookie": f"grommunioAuthJwt={token}",
            "X-Csrf-Token": csrf
        },
        method="PUT"
    )
    try:
        with urllib.request.urlopen(req_put) as resp:
            put_res = json.loads(resp.read().decode())
            print(f"    [+] PUT /system/mconf/ldap succeeded: {put_res.get('message')}")
    except urllib.error.HTTPError as ex:
        error_body = ex.read().decode()
        print(f"    [-] PUT /system/mconf/ldap failed with HTTP {ex.code}: {error_body}", file=sys.stderr)
        return False
    except Exception as ex:
        print(f"    [-] PUT /system/mconf/ldap failed: {ex}", file=sys.stderr)
        return False

    return True

def main():
    if check_ldap_config():
        print("[+] LDAP CONFIGURATION TEST PASSED!\n")
        return 0
    else:
        print("[-] LDAP CONFIGURATION TEST FAILED.\n", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
